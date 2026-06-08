from __future__ import annotations

import math
import struct
import wave
from pathlib import Path
from typing import Any, Dict, List


def generate_lip_sync_from_wav(
    wav_path: Path,
    frame_ms: int = 40,
    min_open: float = 0.10,
    max_open: float = 0.95,
) -> Dict[str, Any]:
    if not wav_path.exists():
        raise FileNotFoundError(f"reply wav not found: {wav_path}")

    with wave.open(str(wav_path), "rb") as wf:
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        sample_rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())

    if sampwidth != 2:
        return {
            "method": "unsupported_wav_format",
            "frame_ms": frame_ms,
            "frames": [],
        }

    values = list(struct.unpack("<" + "h" * (len(raw) // 2), raw))

    if channels > 1:
        mono = []
        for i in range(0, len(values), channels):
            mono.append(int(sum(values[i:i + channels]) / channels))
        values = mono

    frame_samples = max(1, int(sample_rate * frame_ms / 1000))
    rms_values: List[float] = []

    for i in range(0, len(values), frame_samples):
        frame = values[i:i + frame_samples]
        if not frame:
            continue
        rms = math.sqrt(sum(s * s for s in frame) / len(frame)) / 32768.0
        rms_values.append(rms)

    peak = max(rms_values) if rms_values else 1e-6
    frames = []

    for idx, rms in enumerate(rms_values):
        norm = min(1.0, rms / peak)
        mouth_open = min_open + (max_open - min_open) * norm
        frames.append({
            "t_ms": idx * frame_ms,
            "mouth_open": round(float(mouth_open), 3),
        })

    return {
        "method": "short_time_energy",
        "frame_ms": frame_ms,
        "sample_rate": sample_rate,
        "frames": frames,
    }


def build_energy_lip_sync(wav_path: Path) -> Dict[str, Any]:
    return generate_lip_sync_from_wav(wav_path)
