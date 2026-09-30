"""Existing-model benchmark using fixed public text; never record or play audio.

Usage: python -m tools.benchmark_piper --mode persistent --output outputs/NEW_DIR
Output must be a new directory beneath this repository's outputs directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
import wave
from pathlib import Path

from srtp_voice.config import AppConfig
from srtp_voice.tts import TTSAdapter
from tools.workbench import PROJECT_ROOT, resolve_project_path

PUBLIC_TEXTS = (
    "可以试试凉拌黄瓜，清爽，也容易做。",
    "先把番茄切块，再把鸡蛋炒熟。",
    "水开以后转小火，慢慢煮十分钟。",
    "如果喜欢酸一点，可以多放半个番茄。",
    "这三道菜搭配米饭就很好。",
    "你想先听哪一道的具体做法？",
)


def new_output_directory(value: str) -> Path:
    output = resolve_project_path(value, root=PROJECT_ROOT)
    allowed = (PROJECT_ROOT / "outputs").resolve()
    if allowed not in output.parents:
        raise ValueError("benchmark output must be a new directory under outputs/")
    if output.exists():
        raise FileExistsError("benchmark refuses to overwrite an existing directory")
    return output


def benchmark(cfg: AppConfig, output: Path, mode: str) -> dict:
    if mode not in {"one-shot", "persistent"}:
        raise ValueError("unknown benchmark mode")
    # The benchmark is explicitly real Piper, regardless of the app's mock default.
    from dataclasses import replace

    cfg = replace(cfg, tts_backend="piper", tts_piper_persistent=mode == "persistent")
    model_info = cfg.tts_piper_model.stat()
    output.mkdir(parents=True, exist_ok=False)
    adapter = TTSAdapter(cfg)
    engine = adapter.for_streaming() if mode == "persistent" else adapter
    rows = []
    try:
        for ordinal, text in enumerate(PUBLIC_TEXTS, start=1):
            path = output / f"public-{ordinal}.wav"
            started = time.perf_counter()
            engine.synthesize(text, path)
            elapsed = (time.perf_counter() - started) * 1000
            with wave.open(str(path), "rb") as audio:
                frames = audio.getnframes()
                rate = audio.getframerate()
                payload = audio.readframes(frames)
                expected = frames * audio.getnchannels() * audio.getsampwidth()
                if frames <= 0 or rate <= 0 or len(payload) != expected:
                    raise ValueError("benchmark generated incomplete audio")
                duration = frames / rate
            rows.append({"ordinal": ordinal, "synthesis_ms": round(elapsed, 3), "audio_seconds": round(duration, 3)})
    finally:
        engine.close()
    result = {
        "mode": mode,
        "measurement": "real_piper_synthesis_only",
        "microphone": False,
        "playback": False,
        "subjective_listening_verified": False,
        "corpus_sha256": hashlib.sha256("\n".join(PUBLIC_TEXTS).encode("utf-8")).hexdigest(),
        "model_bytes": model_info.st_size,
        "model_mtime_ns": model_info.st_mtime_ns,
        "python": sys.version.split()[0],
        "rows": rows,
        "first_observed_ms": rows[0]["synthesis_ms"],
        "subsequent_median_ms": statistics.median(row["synthesis_ms"] for row in rows[1:]),
        "total_synthesis_ms": round(sum(row["synthesis_ms"] for row in rows), 3),
    }
    (output / "timings.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("one-shot", "persistent"), required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    output = new_output_directory(args.output)
    result = benchmark(AppConfig.from_env(), output, args.mode)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
