from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from srtp_voice.audio_io import record_from_mic


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record microphone audio to recordings/input.wav.")
    parser.add_argument("--seconds", type=float, default=5.0, help="Recording duration in seconds.")
    parser.add_argument("--sample-rate", type=int, default=16000, help="Recording sample rate in Hz.")
    parser.add_argument("--channels", type=int, default=1, help="Number of input channels.")
    parser.add_argument("--output", type=Path, default=Path("recordings/input.wav"), help="Output WAV path.")
    return parser.parse_args()


def print_input_devices() -> None:
    import sounddevice as sd

    print("Input devices:")
    for index, device in enumerate(sd.query_devices()):
        if int(device.get("max_input_channels", 0)) > 0:
            default_mark = " *default" if index == sd.default.device[0] else ""
            print(
                f"  [{index}] {device['name']}"
                f" | inputs={device['max_input_channels']}"
                f" | default_sr={device['default_samplerate']:.0f}{default_mark}"
            )


def main() -> None:
    args = parse_args()
    if args.seconds <= 0:
        raise ValueError("--seconds must be greater than 0.")
    if args.sample_rate <= 0:
        raise ValueError("--sample-rate must be greater than 0.")
    if args.channels <= 0:
        raise ValueError("--channels must be greater than 0.")

    print_input_devices()
    print(f"Recording: {args.seconds:.1f}s, sample_rate={args.sample_rate} Hz, channels={args.channels}")
    record_from_mic(args.output, seconds=args.seconds, sample_rate=args.sample_rate, channels=args.channels)

    if not args.output.exists() or args.output.stat().st_size == 0:
        raise RuntimeError(f"Recording was not created: {args.output}")

    print(f"Saved path: {args.output}")
    print(f"File size: {args.output.stat().st_size} bytes")


if __name__ == "__main__":
    main()
