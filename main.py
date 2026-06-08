from __future__ import annotations

import argparse
from pathlib import Path

from srtp_voice.audio_io import make_dummy_wav, play_wav, record_from_mic, record_until_silence
from srtp_voice.asr import ASRAdapter
from srtp_voice.config import AppConfig
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
    parser = argparse.ArgumentParser(description="SRTP 表情机器人语音通路 V2：对齐学长 ASR-LLM-TTS + VAD 状态机方案")
    parser.add_argument("--mode", choices=["console", "mic", "vad", "file"], default="console",
                        help="console: 不用麦克风，手动输入文本；mic: 固定时长录音；vad: VAD 自动端点；file: 使用已有 wav")
    parser.add_argument("--audio", type=str, default="", help="--mode file 时指定 wav 路径")
    parser.add_argument("--text", type=str, default="", help="直接指定 ASR 文本，便于调试")
    parser.add_argument("--record-seconds", type=float, default=5.0, help="mic 模式固定录音时长")
    parser.add_argument("--no-play", action="store_true", help="只生成回复音频，不播放")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = AppConfig.from_env()
    ensure_dir(cfg.output_dir)

    user_audio = cfg.output_dir / "user_input.wav"
    reply_audio = cfg.output_dir / "reply.wav"
    action_file = cfg.output_dir / "last_action.json"
    serial_packet_file = cfg.output_dir / "serial_packet.json"
    state_file = cfg.output_dir / "last_state.json"

    fsm = DialogueStateMachine()

    print("[0/9] 初始化语音交互状态机：Idle / Listening / Thinking / Speaking")
    fsm.set(DialogueStage.IDLE)

    if args.mode == "vad":
        print(f"[1/9] VAD 自动端点录音 -> {user_audio}")
        fsm.set(DialogueStage.LISTENING)
        record_until_silence(user_audio, cfg)
    elif args.mode == "mic":
        print(f"[1/9] 麦克风固定录音 {args.record_seconds:.1f}s -> {user_audio}")
        fsm.set(DialogueStage.LISTENING)
        record_from_mic(user_audio, seconds=args.record_seconds, sample_rate=cfg.sample_rate)
    elif args.mode == "file":
        if not args.audio:
            raise ValueError("--mode file 需要提供 --audio 路径")
        user_audio = Path(args.audio)
        if not user_audio.exists():
            raise FileNotFoundError(user_audio)
        print(f"[1/9] 使用已有音频：{user_audio}")
        fsm.set(DialogueStage.LISTENING)
    else:
        print(f"[1/9] console 模式：生成占位音频 -> {user_audio}")
        make_dummy_wav(user_audio, seconds=1.0, sample_rate=cfg.sample_rate)
        fsm.set(DialogueStage.LISTENING)

    print("[2/9] 进入 Thinking：SER / ASR / LLM")
    fsm.set(DialogueStage.THINKING)

    print("[3/9] 语音情绪识别 SER")
    ser = SpeechEmotionRecognizer()
    emotion = ser.predict(user_audio)
    print(f"      instant_emotion={emotion.label}, intensity={emotion.intensity:.2f}, confidence={emotion.confidence:.2f}")

    print("[4/9] 情感状态平滑：连续 VAD 空间占位")
    smoother = EmotionStateSmoother(cfg.state_file, alpha=cfg.emotion_smooth_alpha)
    smoothed = smoother.update(emotion)
    print(
        "      smoothed="
        f"label={smoothed.label}, V={smoothed.valence:.2f}, A={smoothed.arousal:.2f}, D={smoothed.dominance:.2f}"
    )

    print("[5/9] 语音转文本 ASR")
    asr = ASRAdapter(cfg)
    if args.text:
        user_text = args.text.strip()
    elif args.mode == "console":
        user_text = input("请输入模拟 ASR 文本：").strip()
    else:
        user_text = asr.transcribe(user_audio).strip()
    if not user_text:
        user_text = "我现在语音部分做不下去了。"
    print(f"      user_text={user_text}")

    print("[6/9] 读取短期交互记忆，LLM 生成回复文本 + 表情/语音策略")
    memory = JsonMemory(cfg.memory_file, max_turns=cfg.max_history_turns)
    history = memory.load()
    generator = StrategyGenerator(cfg)
    strategy = generator.generate(user_text=user_text, emotion=emotion, history=history)
    strategy.action["emotion_state"] = smoothed.to_dict()
    strategy.action["dialogue_stage"] = fsm.stage.value
    print(f"      reply={strategy.reply_text}")
    print(f"      action={strategy.action}")

    print("[7/9] Speaking：TTS 合成回复音频")
    fsm.set(DialogueStage.SPEAKING)
    tts = TTSAdapter(cfg)
    tts.synthesize(strategy.reply_text, reply_audio)
    print(f"      reply_audio={reply_audio}")

    print("[8/9] 基于短时能量生成唇动同步参数")
    lip_sync = build_energy_lip_sync(reply_audio)
    strategy.action["lip_sync"] = lip_sync
    strategy.action["dialogue_stage"] = fsm.stage.value
    serial_packet = build_serial_packet(strategy.action, lip_sync)
    save_json(action_file, strategy.to_dict())
    save_serial_packet(serial_packet, serial_packet_file)
    save_json(state_file, {"trace": fsm.trace, "stage": fsm.stage.value})
    print(f"      已保存动作策略：{action_file}")
    print(f"      serial_packet={serial_packet_file}")
    print(f"      lip_sync_frames={len(lip_sync.get('frames', []))}")

    print("[9/9] 播放/输出，结束后回到 Idle")
    if not args.no_play:
        play_wav(reply_audio)
    else:
        print("      --no-play 已启用，跳过播放")
    fsm.set(DialogueStage.IDLE)
    save_json(state_file, {"trace": fsm.trace, "stage": fsm.stage.value})

    state = PipelineState(
        user_audio=str(user_audio),
        user_text=user_text,
        emotion=emotion,
        reply_text=strategy.reply_text,
        action=strategy.action,
        reply_audio=str(reply_audio),
    )
    memory.append(state)
    print("完成：VAD/录音 -> SER -> 情绪平滑 -> ASR -> LLM策略 -> TTS -> 短时能量唇动 -> 动作策略 JSON")


if __name__ == "__main__":
    main()
