from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.asr.placeholder_asr import transcribe_audio
from src.llm.placeholder_llm import generate_reply
from srtp_voice.audio_io import play_mp3, record_from_mic
from srtp_voice.config import AppConfig
from srtp_voice.tts import TTSAdapter
from srtp_voice.utils import ensure_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record WAV -> placeholder ASR -> placeholder LLM -> TTS playback.")
    parser.add_argument("--seconds", type=float, default=5.0, help="Recording duration in seconds.")
    parser.add_argument("--sample-rate", type=int, default=16000, help="Recording sample rate in Hz.")
    parser.add_argument("--channels", type=int, default=1, help="Number of microphone input channels.")
    parser.add_argument("--input-wav", type=Path, default=Path("recordings/input.wav"), help="Recorded WAV path.")
    parser.add_argument("--reply-mp3", type=Path, default=Path("outputs/reply.mp3"), help="Reply MP3 path.")
    parser.add_argument("--no-play", action="store_true", help="Generate MP3 without playback.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.seconds <= 0:
        raise ValueError("--seconds must be greater than 0.")
    if args.sample_rate <= 0:
        raise ValueError("--sample-rate must be greater than 0.")
    if args.channels <= 0:
        raise ValueError("--channels must be greater than 0.")

    cfg = AppConfig.from_env()
    cfg.tts_backend = "edge_tts"
    ensure_dir(args.input_wav.parent)
    ensure_dir(args.reply_mp3.parent)

    print(f"Recording: {args.seconds:.1f}s, sample_rate={args.sample_rate} Hz, channels={args.channels}")
    record_from_mic(args.input_wav, seconds=args.seconds, sample_rate=args.sample_rate, channels=args.channels)
    if not args.input_wav.exists() or args.input_wav.stat().st_size == 0:
        raise RuntimeError(f"Recording was not created: {args.input_wav}")
    print(f"Recorded WAV: {args.input_wav} ({args.input_wav.stat().st_size} bytes)")

    user_text = transcribe_audio(str(args.input_wav))
    print(f"ASR placeholder: {user_text}")

    reply_text = generate_reply(user_text)
    print(f"LLM placeholder: {reply_text}")

    TTSAdapter(cfg).synthesize(reply_text, args.reply_mp3)
    if not args.reply_mp3.exists() or args.reply_mp3.stat().st_size == 0:
        raise RuntimeError(f"TTS output was not created: {args.reply_mp3}")
    print(f"Reply MP3: {args.reply_mp3} ({args.reply_mp3.stat().st_size} bytes)")

    if not args.no_play:
        play_mp3(args.reply_mp3)


if __name__ == "__main__":
    main()
