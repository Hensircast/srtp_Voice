from __future__ import annotations

import argparse
from pathlib import Path

from srtp_voice.audio_io import (
    NoSpeechDetectedError,
    make_dummy_wav,
    play_wav,
    record_from_mic,
    record_until_silence,
)
from srtp_voice.asr import ASRAdapter
from srtp_voice.config import AppConfig
from srtp_voice.diagnostics import collect_diagnostics, print_diagnostics
from srtp_voice.emotion_state import EmotionStateSmoother
from srtp_voice.lip_sync import build_energy_lip_sync
from srtp_voice.llm import StrategyGenerator
from srtp_voice.memory import JsonMemory
from srtp_voice.ser import SpeechEmotionRecognizer
from srtp_voice.serial_out import build_serial_packet, save_serial_packet
from srtp_voice.state_machine import DialogueStage, DialogueStateMachine
from srtp_voice.tts import TTSAdapter
from srtp_voice.types import PipelineState
from srtp_voice.utils import ensure_dir, save_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SRTP 表情机器人语音交互：VAD / SER / ASR / LLM / TTS"
    )
    parser.add_argument(
        "--mode",
        choices=["console", "mic", "vad", "file"],
        default="console",
        help="console: 不用麦克风，手动输入文本；mic: 固定时长录音；vad: VAD 自动端点；file: 使用已有 wav",
    )
    parser.add_argument("--audio", type=str, default="", help="--mode file 时指定 wav 路径")
    parser.add_argument("--text", type=str, default="", help="直接指定 ASR 文本，便于调试")
    parser.add_argument("--record-seconds", type=float, default=5.0, help="mic 模式固定录音时长")
    parser.add_argument("--no-play", action="store_true", help="只生成回复音频，不播放")
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="只输出平台、依赖和音频设备诊断，然后退出",
    )
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="在 vad 或 mic 模式下常驻当前进程并连续执行多轮交互",
    )
    return parser.parse_args()


def save_fsm_state(fsm: DialogueStateMachine, state_file: Path) -> None:
    save_json(state_file, {"trace": fsm.trace, "stage": fsm.stage.value})


def run_one_turn(
    *,
    args: argparse.Namespace,
    cfg: AppConfig,
    continuous: bool,
    fsm: DialogueStateMachine,
    ser: SpeechEmotionRecognizer,
    asr: ASRAdapter | None,
    generator: StrategyGenerator,
    tts: TTSAdapter,
    smoother: EmotionStateSmoother,
    memory: JsonMemory,
    user_audio: Path,
    reply_audio: Path,
    action_file: Path,
    serial_packet_file: Path,
    state_file: Path,
) -> None:
    turn_audio = user_audio

    if args.mode == "vad":
        print(f"[1/9] VAD 自动端点录音 -> {turn_audio}")
        fsm.set(DialogueStage.LISTENING)
        try:
            record_until_silence(turn_audio, cfg)
        except NoSpeechDetectedError:
            if continuous:
                print("      未检测到有效语音，本轮结束，继续监听")
            else:
                print("      未检测到有效语音，本轮结束")
            fsm.set(DialogueStage.IDLE)
            save_fsm_state(fsm, state_file)
            return
    elif args.mode == "mic":
        print(f"[1/9] 麦克风固定录音 {args.record_seconds:.1f}s -> {turn_audio}")
        fsm.set(DialogueStage.LISTENING)
        record_from_mic(turn_audio, seconds=args.record_seconds, sample_rate=cfg.sample_rate)
    elif args.mode == "file":
        if not args.audio:
            raise ValueError("--mode file 需要提供 --audio 路径")
        turn_audio = Path(args.audio)
        if not turn_audio.exists():
            raise FileNotFoundError(turn_audio)
        print(f"[1/9] 使用已有音频：{turn_audio}")
        fsm.set(DialogueStage.LISTENING)
    else:
        print(f"[1/9] console 模式：生成占位音频 -> {turn_audio}")
        make_dummy_wav(turn_audio, seconds=1.0, sample_rate=cfg.sample_rate)
        fsm.set(DialogueStage.LISTENING)

    print("[2/9] 进入 Thinking：SER / ASR / LLM")
    fsm.set(DialogueStage.THINKING)

    print("[3/9] 语音情绪识别 SER")
    emotion = ser.predict(turn_audio)
    print(
        f"      instant_emotion={emotion.label}, "
        f"intensity={emotion.intensity:.2f}, confidence={emotion.confidence:.2f}"
    )

    print("[4/9] 情感状态平滑：连续 VAD 空间占位")
    smoothed = smoother.update(emotion)
    print(
        "      smoothed="
        f"label={smoothed.label}, V={smoothed.valence:.2f}, "
        f"A={smoothed.arousal:.2f}, D={smoothed.dominance:.2f}"
    )

    print("[5/9] 语音转文本 ASR")
    if args.text:
        user_text = args.text.strip()
    elif args.mode == "console":
        user_text = input("请输入模拟 ASR 文本：").strip()
    else:
        if asr is None:
            raise RuntimeError("ASRAdapter was not initialized for an audio input mode")
        user_text = asr.transcribe(turn_audio).strip()
    if not user_text:
        if not args.text and args.mode in {"mic", "vad", "file"}:
            if continuous:
                print("      ASR 未返回有效文本，本轮结束，继续监听")
            else:
                print("      ASR did not return speech text; skipping LLM/TTS for this turn.")
            fsm.set(DialogueStage.IDLE)
            save_fsm_state(fsm, state_file)
            if not continuous:
                print("完成：未识别到有效语音，状态机已回到 Idle")
            return
        user_text = "我现在语音部分做不下去了。"
    print(f"      user_text={user_text}")

    print("[6/9] 读取短期交互记忆，LLM 生成回复文本 + 表情/语音策略")
    history = memory.load()
    strategy = generator.generate(user_text=user_text, emotion=emotion, history=history)
    strategy.action["emotion_state"] = smoothed.to_dict()
    strategy.action["dialogue_stage"] = fsm.stage.value
    print(f"      reply={strategy.reply_text}")
    print(f"      action={strategy.action}")

    print("[7/9] Speaking：TTS 合成回复音频")
    fsm.set(DialogueStage.SPEAKING)
    tts.synthesize(strategy.reply_text, reply_audio)
    print(f"      reply_audio={reply_audio}")

    print("[8/9] 基于短时能量生成唇动同步参数")
    lip_sync = build_energy_lip_sync(reply_audio)
    strategy.action["lip_sync"] = lip_sync
    strategy.action["dialogue_stage"] = fsm.stage.value
    serial_packet = build_serial_packet(strategy.action, lip_sync)
    save_json(action_file, strategy.to_dict())
    save_serial_packet(serial_packet, serial_packet_file)
    save_fsm_state(fsm, state_file)
    print(f"      已保存动作策略：{action_file}")
    print(f"      serial_packet={serial_packet_file}")
    print(f"      lip_sync_frames={len(lip_sync.get('frames', []))}")

    print("[9/9] 播放/输出，结束后回到 Idle")
    if not args.no_play:
        play_wav(reply_audio)
    else:
        print("      --no-play 已启用，跳过播放")
    fsm.set(DialogueStage.IDLE)
    save_fsm_state(fsm, state_file)

    state = PipelineState(
        user_audio=str(turn_audio),
        user_text=user_text,
        emotion=emotion,
        reply_text=strategy.reply_text,
        action=strategy.action,
        reply_audio=str(reply_audio),
    )
    memory.append(state)
    print("完成：VAD/录音 -> SER -> 情绪平滑 -> ASR -> LLM策略 -> TTS -> 短时能量唇动 -> 动作策略 JSON")


def main() -> None:
    args = parse_args()
    if bool(getattr(args, "diagnose", False)):
        cfg = AppConfig.from_env()
        print_diagnostics(collect_diagnostics(cfg))
        return

    continuous = bool(getattr(args, "continuous", False))
    if continuous and args.mode not in {"vad", "mic"}:
        raise ValueError("--continuous 仅支持 --mode vad 或 --mode mic")

    cfg = AppConfig.from_env()
    ensure_dir(cfg.output_dir)

    user_audio = cfg.output_dir / "user_input.wav"
    reply_audio = cfg.output_dir / "reply.wav"
    action_file = cfg.output_dir / "last_action.json"
    serial_packet_file = cfg.output_dir / "serial_packet.json"
    state_file = cfg.output_dir / "last_state.json"

    fsm = DialogueStateMachine()
    print(f"[CONFIG] LLM={cfg.llm_backend}/{cfg.llm_model}, fallback={str(cfg.llm_fallback_to_mock).lower()}")
    print(
        "[CONFIG] "
        f"VAD={cfg.vad_backend}, threshold={cfg.vad_threshold}, min_speech_ms={cfg.min_speech_ms}"
    )
    print("[0/9] 初始化语音交互状态机：Idle / Listening / Thinking / Speaking")
    fsm.set(DialogueStage.IDLE)

    try:
        ser = SpeechEmotionRecognizer(cfg)
        if cfg.ser_backend.strip().lower() == "sensevoice":
            print("[INIT] 正在预加载 SenseVoice SER 模型")
            ser.warmup()
            if ser.backend_name == "sensevoice":
                print("[INIT] SenseVoice SER 模型加载完成")
            else:
                print("[INIT] SenseVoice SER 预加载失败，已启用 heuristic fallback")

        needs_asr = not args.text and args.mode in {"mic", "vad", "file"}
        asr = ASRAdapter(cfg) if needs_asr else None
        generator = StrategyGenerator(cfg)
        tts = TTSAdapter(cfg)
        smoother = EmotionStateSmoother(cfg.state_file, alpha=cfg.emotion_smooth_alpha)
        memory = JsonMemory(cfg.memory_file, max_turns=cfg.max_history_turns)

        turn_number = 1
        while True:
            print(f"[TURN {turn_number}] 开始监听")
            run_one_turn(
                args=args,
                cfg=cfg,
                continuous=continuous,
                fsm=fsm,
                ser=ser,
                asr=asr,
                generator=generator,
                tts=tts,
                smoother=smoother,
                memory=memory,
                user_audio=user_audio,
                reply_audio=reply_audio,
                action_file=action_file,
                serial_packet_file=serial_packet_file,
                state_file=state_file,
            )
            if not continuous:
                break
            turn_number += 1
    except KeyboardInterrupt:
        fsm.set(DialogueStage.IDLE)
        save_fsm_state(fsm, state_file)
        print("\n已收到 Ctrl+C，状态机已回到 Idle，程序正常退出")


if __name__ == "__main__":
    main()
