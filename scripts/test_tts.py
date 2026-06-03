from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from srtp_voice.audio_io import play_mp3
from srtp_voice.config import AppConfig
from srtp_voice.tts import TTSAdapter
from srtp_voice.utils import ensure_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test edge-tts MP3 generation and pygame playback.")
    parser.add_argument("--text", type=str, default="", help="Text to synthesize. If omitted, read from keyboard.")
    parser.add_argument("--no-play", action="store_true", help="Generate outputs/reply.mp3 without playback.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    text = args.text.strip() or input("请输入要合成的文本: ").strip()
    if not text:
        raise ValueError("Text must not be empty.")

    cfg = AppConfig.from_env()
    cfg.tts_backend = "edge_tts"
    ensure_dir(cfg.output_dir)

    out_mp3 = cfg.output_dir / "reply.mp3"
    TTSAdapter(cfg).synthesize(text, out_mp3)

    if not out_mp3.exists() or out_mp3.stat().st_size == 0:
        raise RuntimeError(f"TTS output was not created: {out_mp3}")

    print(f"Generated: {out_mp3} ({out_mp3.stat().st_size} bytes)")
    if not args.no_play:
        play_mp3(out_mp3)


if __name__ == "__main__":
    main()
