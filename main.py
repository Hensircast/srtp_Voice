from __future__ import annotations

import argparse
from pathlib import Path

from srtp_voice.audio_io import make_dummy_wav, play_mp3, record_from_mic
from srtp_voice.asr import ASRAdapter
from srtp_voice.config import AppConfig
from srtp_voice.llm import StrategyGenerator
from srtp_voice.memory import JsonMemory
from srtp_voice.ser import SpeechEmotionRecognizer
from srtp_voice.tts import TTSAdapter
from srtp_voice.types import PipelineState
from srtp_voice.utils import ensure_dir, save_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SRTP 表情机器人语音通路 V1：可跑通骨架，模型接口占位")
    parser.add_argument("--mode", choices=["console", "mic", "file"], default="console",
                        help="console: 不用麦克风，手动输入文本；mic: 麦克风录音；file: 使用已有 wav")
    parser.add_argument("--audio", type=str, default="", help="--mode file 时指定 wav 路径")
    parser.add_argument("--text", type=str, default="", help="直接指定 ASR 文本，便于调试")
    parser.add_argument("--record-seconds", type=float, default=5.0, help="mic 模式录音时长")
    parser.add_argument("--no-play", action="store_true", help="只生成回复音频，不播放")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = AppConfig.from_env()
    ensure_dir(cfg.output_dir)

    user_audio = cfg.output_dir / "user_input.wav"
    reply_audio = cfg.output_dir / "reply.mp3"
    action_file = cfg.output_dir / "last_action.json"

    if args.mode == "mic":
        print(f"[1/7] 麦克风录音 {args.record_seconds:.1f}s -> {user_audio}")
        record_from_mic(user_audio, seconds=args.record_seconds, sample_rate=cfg.sample_rate)
    elif args.mode == "file":
        if not args.audio:
            raise ValueError("--mode file 需要提供 --audio 路径")
        user_audio = Path(args.audio)
        if not user_audio.exists():
            raise FileNotFoundError(user_audio)
        print(f"[1/7] 使用已有音频：{user_audio}")
    else:
        print(f"[1/7] console 模式：生成占位音频 -> {user_audio}")
        make_dummy_wav(user_audio, seconds=1.0, sample_rate=cfg.sample_rate)

    print("[2/7] 语音情绪识别 SER")
    ser = SpeechEmotionRecognizer()
    emotion = ser.predict(user_audio)
    print(f"      emotion={emotion.label}, intensity={emotion.intensity:.2f}, confidence={emotion.confidence:.2f}")

    print("[3/7] 语音转文本 ASR")
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

    print("[4/7] 读取短期交互记忆")
    memory = JsonMemory(cfg.memory_file, max_turns=cfg.max_history_turns)
    history = memory.load()

    print("[5/7] LLM 生成回复文本 + 表情/语音策略")
    generator = StrategyGenerator(cfg)
    strategy = generator.generate(user_text=user_text, emotion=emotion, history=history)
    print(f"      reply={strategy.reply_text}")
    print(f"      action={strategy.action}")
    save_json(action_file, strategy.to_dict())
    print(f"      已保存动作策略：{action_file}")

    print("[6/7] TTS 合成回复音频")
    tts = TTSAdapter(cfg)
    tts.synthesize(strategy.reply_text, reply_audio)
    print(f"      reply_audio={reply_audio}")

    print("[7/7] 播放/输出")
    if not args.no_play:
        play_mp3(reply_audio)
    else:
        print("      --no-play 已启用，跳过播放")

    state = PipelineState(
        user_audio=str(user_audio),
        user_text=user_text,
        emotion=emotion,
        reply_text=strategy.reply_text,
        action=strategy.action,
        reply_audio=str(reply_audio),
    )
    memory.append(state)
    print("完成：语音输入 -> 情绪识别 -> ASR -> LLM策略 -> TTS -> 动作策略文件")


if __name__ == "__main__":
    main()
