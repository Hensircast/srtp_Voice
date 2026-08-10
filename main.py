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
from srtp_voice.streaming import StreamEvent, StreamEventType
from srtp_voice.streaming_runtime import (
    StreamingResponseRuntime,
    capture_streaming_microphone,
)
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
    parser.add_argument(
        "--streaming",
        action="store_true",
        help="启用 V1.8 流式 LLM、按句 TTS、时延指标；mic/vad 同时启用流式输入",
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
            decayed = smoother.decay()
            print(
                "      no_evidence_decay="
                f"label={decayed.label}, V={decayed.valence:.2f}, "
                f"A={decayed.arousal:.2f}, D={decayed.dominance:.2f}"
            )
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

    print("[3/9] SER + 韵律情绪融合")
    emotion = ser.predict(turn_audio)
    print(
        f"      instant/fused_emotion={emotion.label}, "
        f"intensity={emotion.intensity:.2f}, "
        f"evidence_strength={emotion.confidence:.2f}"
    )

    print("[4/9] 情感状态平滑：EMA + 时间衰减")
    smoothed = smoother.update(emotion)
    print(
        "      smoothed_emotion_state="
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
    print("完成：VAD/录音 -> SER+韵律融合 -> 情绪平滑 -> ASR -> LLM策略 -> TTS -> 短时能量唇动 -> 动作策略 JSON")


def run_one_streaming_turn(
    *,
    args: argparse.Namespace,
    cfg: AppConfig,
    continuous: bool,
    fsm: DialogueStateMachine,
    ser: SpeechEmotionRecognizer,
    asr: ASRAdapter | None,
    runtime: StreamingResponseRuntime,
    smoother: EmotionStateSmoother,
    memory: JsonMemory,
    user_audio: Path,
    reply_audio: Path,
    action_file: Path,
    serial_packet_file: Path,
    state_file: Path,
    metrics_file: Path,
    events_file: Path,
) -> None:
    handle = runtime.begin_turn()
    turn_audio = user_audio
    captured_streaming_asr = False
    try:
        if args.mode in {"mic", "vad"} and not args.text:
            if asr is None:
                raise RuntimeError("streaming microphone mode requires ASRAdapter")
            print(f"[1/9] V1.8 流式麦克风 + VAD -> {turn_audio}")
            fsm.set(DialogueStage.LISTENING)
            user_text = capture_streaming_microphone(
                cfg,
                asr,
                runtime,
                handle,
                turn_audio,
            ).strip()
            captured_streaming_asr = True
        elif args.mode == "file":
            if not args.audio:
                raise ValueError("--mode file 需要提供 --audio 路径")
            turn_audio = Path(args.audio)
            if not turn_audio.exists():
                raise FileNotFoundError(turn_audio)
            print(f"[1/9] 使用已有音频：{turn_audio}")
            fsm.set(DialogueStage.LISTENING)
            if args.text:
                user_text = args.text.strip()
            else:
                if asr is None:
                    raise RuntimeError("ASRAdapter was not initialized for file mode")
                user_text = asr.transcribe(turn_audio).strip()
        else:
            print(f"[1/9] 流式 console/文本模式：生成占位音频 -> {turn_audio}")
            make_dummy_wav(turn_audio, seconds=1.0, sample_rate=cfg.sample_rate)
            fsm.set(DialogueStage.LISTENING)
            user_text = args.text.strip() if args.text else input("请输入模拟 ASR 文本：").strip()

        if not captured_streaming_asr:
            runtime.emit(
                handle,
                StreamEventType.ASR_FINAL,
                {"text": user_text, "source": "text_override" if args.text else args.mode},
            )
        if not user_text:
            runtime.cancel_current(reason="empty_asr")
            fsm.set(DialogueStage.IDLE)
            save_fsm_state(fsm, state_file)
            print("      ASR 未返回有效 final 文本，本轮结束")
            return
        print(f"      final_user_text={user_text}")

        print("[2/9] 进入 Thinking：SER / streaming LLM")
        fsm.set(DialogueStage.THINKING)
        print("[3/9] SER + 韵律情绪融合")
        emotion = ser.predict(turn_audio)
        print(
            f"      fused_emotion={emotion.label}, intensity={emotion.intensity:.2f}, "
            f"evidence_strength={emotion.confidence:.2f}"
        )
        print("[4/9] 情感状态平滑")
        smoothed = smoother.update(emotion)
        print(
            "      smoothed_emotion_state="
            f"label={smoothed.label}, V={smoothed.valence:.2f}, "
            f"A={smoothed.arousal:.2f}, D={smoothed.dominance:.2f}"
        )

        print("[5/9] 读取 final-only 短期记忆")
        history = memory.load()
        print("[6/9] Ollama token 流 -> 句子切分 -> 增量 TTS")
        fsm.set(DialogueStage.SPEAKING)
        result = runtime.run_response(
            handle,
            user_text=user_text,
            emotion=emotion,
            history=history,
            reply_audio=reply_audio,
            action={
                "emotion_state": smoothed.to_dict(),
                "dialogue_stage": fsm.stage.value,
            },
        )
        print(f"\n      final_reply={result.strategy.reply_text}")
        print(f"      reply_audio={reply_audio}, chunks={result.audio_chunks}")

        print("[7/9] 保存兼容动作策略、串口包和流式指标")
        serial_packet = build_serial_packet(result.strategy.action, result.lip_sync)
        save_json(action_file, result.strategy.to_dict())
        save_serial_packet(serial_packet, serial_packet_file)
        save_json(
            metrics_file,
            {
                "last_turn": result.latency.to_dict(),
                "summary": runtime.controller.latency_summary(),
                "late_events": runtime.controller.late_events,
                "dropped_event_history": runtime.controller.dropped_history_events,
                "tts_backpressure_events": result.backpressure_events,
            },
        )
        save_json(events_file, [event.to_dict() for event in runtime.controller.history])

        print("[8/9] final transcript/reply 写入记忆一次")
        state = PipelineState(
            user_audio=str(turn_audio),
            user_text=user_text,
            emotion=emotion,
            reply_text=result.strategy.reply_text,
            action=result.strategy.action,
            reply_audio=str(reply_audio),
        )
        memory.append(state)

        print("[9/9] 流式轮次完成，状态机回到 Idle")
        fsm.set(DialogueStage.IDLE)
        save_fsm_state(fsm, state_file)
    except NoSpeechDetectedError:
        runtime.cancel_current(reason="no_speech")
        decayed = smoother.decay()
        fsm.set(DialogueStage.IDLE)
        save_fsm_state(fsm, state_file)
        if continuous:
            print(f"      未检测到语音，继续监听；emotion_decay={decayed.label}")
        else:
            print(f"      未检测到语音，本轮结束；emotion_decay={decayed.label}")
    except Exception as exc:
        if runtime.controller.is_active(handle.turn_id):
            runtime.controller.fail_turn(handle.turn_id, exc)
        else:
            runtime.cancel_current(reason="turn_failed")
        fsm.set(DialogueStage.IDLE)
        save_fsm_state(fsm, state_file)
        snapshot = runtime.controller.last_turn_snapshot
        diagnostics = {
            "summary": runtime.controller.latency_summary(),
            "late_events": runtime.controller.late_events,
            "dropped_event_history": runtime.controller.dropped_history_events,
            "tts_backpressure_events": runtime.tts_backpressure_events,
            "failed": True,
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
            },
        }
        if snapshot is not None and snapshot.turn_id == handle.turn_id:
            diagnostics["last_turn"] = snapshot.to_dict()
        save_json(metrics_file, diagnostics)
        save_json(events_file, [event.to_dict() for event in runtime.controller.history])
        if continuous:
            print(
                f"      [TURN ERROR] {type(exc).__name__}: {exc}; "
                "本轮已回到 Idle，将继续监听"
            )
            return
        raise


def main() -> None:
    args = parse_args()
    if bool(getattr(args, "diagnose", False)):
        cfg = AppConfig.from_env()
        print_diagnostics(collect_diagnostics(cfg))
        return

    continuous = bool(getattr(args, "continuous", False))
    streaming = bool(getattr(args, "streaming", False))
    if continuous and args.mode not in {"vad", "mic"}:
        raise ValueError("--continuous 仅支持 --mode vad 或 --mode mic")

    cfg = AppConfig.from_env()
    ensure_dir(cfg.output_dir)

    user_audio = cfg.output_dir / "user_input.wav"
    reply_audio = cfg.output_dir / "reply.wav"
    action_file = cfg.output_dir / "last_action.json"
    serial_packet_file = cfg.output_dir / "serial_packet.json"
    state_file = cfg.output_dir / "last_state.json"
    streaming_metrics_file = cfg.output_dir / "streaming_metrics.json"
    streaming_events_file = cfg.output_dir / "streaming_events.json"

    fsm = DialogueStateMachine()
    print(f"[CONFIG] LLM={cfg.llm_backend}/{cfg.llm_model}, fallback={str(cfg.llm_fallback_to_mock).lower()}")
    print(
        "[CONFIG] "
        f"VAD={cfg.vad_backend}, threshold={cfg.vad_threshold}, min_speech_ms={cfg.min_speech_ms}"
    )
    print("[0/9] 初始化语音交互状态机：Idle / Listening / Thinking / Speaking")
    fsm.set(DialogueStage.IDLE)

    streaming_runtime: StreamingResponseRuntime | None = None
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
        smoother = EmotionStateSmoother(
            cfg.state_file,
            alpha=cfg.emotion_smooth_alpha,
            decay_half_life_seconds=cfg.emotion_decay_half_life_seconds,
            max_step=cfg.emotion_max_step,
        )
        memory = JsonMemory(cfg.memory_file, max_turns=cfg.max_history_turns)

        if streaming:
            def stream_event_sink(event: StreamEvent) -> None:
                if event.event_type == StreamEventType.LLM_TOKEN:
                    print(str(event.payload.get("text", "")), end="", flush=True)
                elif event.event_type == StreamEventType.ASR_PARTIAL:
                    print(f"      [ASR partial] {event.payload.get('text', '')}")

            streaming_runtime = StreamingResponseRuntime(
                cfg,
                generator,
                tts,
                event_sink=stream_event_sink,
                playback_enabled=not args.no_play,
                temp_parent=cfg.output_dir,
            )
            print(
                "[CONFIG] V1.8 streaming=enabled, "
                f"audio_queue={cfg.stream_audio_queue_size}, "
                f"tts_queue={cfg.stream_tts_queue_size}, "
                f"barge_in={str(cfg.stream_barge_in_enabled).lower()}"
            )

        turn_number = 1
        while True:
            print(f"[TURN {turn_number}] 开始监听")
            if streaming:
                if streaming_runtime is None:  # pragma: no cover
                    raise RuntimeError("streaming runtime was not initialized")
                run_one_streaming_turn(
                    args=args,
                    cfg=cfg,
                    continuous=continuous,
                    fsm=fsm,
                    ser=ser,
                    asr=asr,
                    runtime=streaming_runtime,
                    smoother=smoother,
                    memory=memory,
                    user_audio=user_audio,
                    reply_audio=reply_audio,
                    action_file=action_file,
                    serial_packet_file=serial_packet_file,
                    state_file=state_file,
                    metrics_file=streaming_metrics_file,
                    events_file=streaming_events_file,
                )
            else:
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
        if streaming_runtime is not None:
            streaming_runtime.cancel_current(reason="keyboard_interrupt")
            save_json(
                streaming_events_file,
                [event.to_dict() for event in streaming_runtime.controller.history],
            )
            save_json(
                streaming_metrics_file,
                {
                    "summary": streaming_runtime.controller.latency_summary(),
                    "late_events": streaming_runtime.controller.late_events,
                    "dropped_event_history": (
                        streaming_runtime.controller.dropped_history_events
                    ),
                    "cancelled": True,
                },
            )
        fsm.set(DialogueStage.IDLE)
        save_fsm_state(fsm, state_file)
        print("\n已收到 Ctrl+C，状态机已回到 Idle，程序正常退出")
    finally:
        if streaming_runtime is not None:
            streaming_runtime.close(drain=False)


if __name__ == "__main__":
    main()
