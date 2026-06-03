from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.llm.placeholder_llm import generate_reply
from srtp_voice.audio_io import play_mp3
from srtp_voice.config import AppConfig
from srtp_voice.tts import TTSAdapter
from srtp_voice.utils import ensure_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Text input -> placeholder LLM reply -> edge-tts MP3 playback.")
    parser.add_argument("--no-play", action="store_true", help="Generate MP3 without playback.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = AppConfig.from_env()
    cfg.tts_backend = "edge_tts"
    ensure_dir(cfg.output_dir)

    tts = TTSAdapter(cfg)
    out_mp3 = cfg.output_dir / "reply.mp3"

    print("Enter text. Type q or quit to exit.")
    while True:
        user_text = input("You> ").strip()
        if user_text.lower() in {"q", "quit"}:
            print("Bye.")
            break
        if not user_text:
            print("Please enter some text, or type q to exit.")
            continue

        reply = generate_reply(user_text)
        print(f"Bot> {reply}")

        tts.synthesize(reply, out_mp3)
        if not out_mp3.exists() or out_mp3.stat().st_size == 0:
            raise RuntimeError(f"TTS output was not created: {out_mp3}")

        print(f"Audio: {out_mp3} ({out_mp3.stat().st_size} bytes)")
        if not args.no_play:
            play_mp3(out_mp3)


if __name__ == "__main__":
    main()
