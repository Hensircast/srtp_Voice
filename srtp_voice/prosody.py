from __future__ import annotations

import array
import math
import sys
import wave
from pathlib import Path
from statistics import fmean, pstdev
from typing import Sequence

from .types import ProsodyFeatures


class ProsodyUnavailableError(ValueError):
    """The WAV cannot be decoded by the lightweight prosody extractor."""


class UnsupportedProsodyFormatError(ProsodyUnavailableError):
    """The WAV is valid but its sample format is unsupported for prosody."""


PCM16_SCALE = 32768.0
ENERGY_FRAME_MS = 25
F0_FRAME_MS = 50
MIN_F0_HZ = 70.0
MAX_F0_HZ = 400.0
TARGET_F0_SAMPLE_RATE = 8000
MAX_F0_FRAMES = 12
MIN_PERIODIC_CORRELATION = 0.55
MIN_ACTIVITY_RMS = 0.006
MAX_ACTIVITY_RMS = 0.030
ACTIVITY_PEAK_RATIO = 0.15
MAX_SPEECH_RATE_PROXY = 20.0


def _clamp(value: float, minimum: float, maximum: float) -> float:
    if not math.isfinite(value):
        return minimum
    return max(minimum, min(maximum, value))


def _rms(samples: Sequence[int]) -> float:
    if not samples:
        return 0.0
    value = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
    return _clamp(value / PCM16_SCALE, 0.0, 1.0)


def _read_pcm16_mono(path: Path) -> tuple[int, list[int]]:
    audio_path = Path(path)
    if not audio_path.is_file():
        raise FileNotFoundError(f"prosody WAV file not found: {audio_path}")

    try:
        with wave.open(str(audio_path), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            compression = wav_file.getcomptype()
            frame_count = wav_file.getnframes()
            raw = wav_file.readframes(frame_count)
    except wave.Error as exc:
        error_type = (
            UnsupportedProsodyFormatError
            if "unknown format" in str(exc).lower()
            else ProsodyUnavailableError
        )
        raise error_type(
            f"prosody is unavailable for WAV file '{audio_path}': {exc}"
        ) from exc
    except EOFError as exc:
        raise ProsodyUnavailableError(
            f"prosody is unavailable for WAV file '{audio_path}': {exc}"
        ) from exc

    if compression != "NONE":
        raise UnsupportedProsodyFormatError(
            f"compressed WAV is not supported for prosody extraction: {audio_path}"
        )
    if sample_width != 2:
        raise UnsupportedProsodyFormatError(
            f"prosody extraction requires 16-bit PCM WAV: {audio_path} "
            f"(sample_width={sample_width})"
        )
    if channels < 1 or sample_rate <= 0:
        raise ProsodyUnavailableError(
            f"invalid WAV format for prosody extraction: {audio_path} "
            f"(channels={channels}, sample_rate={sample_rate})"
        )
    frame_width = channels * sample_width
    expected_bytes = frame_count * frame_width
    if len(raw) != expected_bytes or len(raw) % frame_width != 0:
        raise ProsodyUnavailableError(
            f"truncated PCM data in WAV file: {audio_path}"
        )

    values = array.array("h")
    values.frombytes(raw)
    if sys.byteorder != "little":
        values.byteswap()

    if channels == 1:
        return sample_rate, values.tolist()

    mono: list[int] = []
    for index in range(0, len(values), channels):
        frame = values[index : index + channels]
        mono.append(round(sum(frame) / channels))
    return sample_rate, mono


def _frame_rms_values(samples: Sequence[int], frame_samples: int) -> list[float]:
    return [
        _rms(samples[start : start + frame_samples])
        for start in range(0, len(samples), frame_samples)
    ]


def _evenly_limit(values: Sequence[Sequence[int]], limit: int) -> list[Sequence[int]]:
    if len(values) <= limit:
        return list(values)
    if limit <= 1:
        return [values[0]]
    indexes = {
        round(position * (len(values) - 1) / (limit - 1))
        for position in range(limit)
    }
    return [values[index] for index in sorted(indexes)]


def _normalized_autocorrelation(
    samples: Sequence[float],
    lag: int,
) -> float:
    left = samples[:-lag]
    right = samples[lag:]
    numerator = sum(a * b for a, b in zip(left, right))
    left_energy = sum(value * value for value in left)
    right_energy = sum(value * value for value in right)
    denominator = math.sqrt(left_energy * right_energy)
    if denominator <= 1e-12:
        return 0.0
    return numerator / denominator


def _estimate_frame_f0(frame: Sequence[int], sample_rate: int) -> float | None:
    if len(frame) < 4:
        return None

    decimation = max(1, math.ceil(sample_rate / TARGET_F0_SAMPLE_RATE))
    reduced = frame[::decimation]
    reduced_rate = sample_rate / decimation
    if len(reduced) < 4:
        return None

    mean = fmean(reduced)
    centered = [float(value) - mean for value in reduced]
    if sum(value * value for value in centered) <= 1e-9:
        return None

    minimum_lag = max(1, int(reduced_rate / MAX_F0_HZ))
    maximum_lag = min(
        int(reduced_rate / MIN_F0_HZ),
        len(centered) // 2,
    )
    if maximum_lag <= minimum_lag:
        return None

    correlations = {
        lag: _normalized_autocorrelation(centered, lag)
        for lag in range(minimum_lag, maximum_lag + 1)
    }
    best_lag = max(correlations, key=correlations.get)
    best_correlation = correlations[best_lag]
    if best_correlation < MIN_PERIODIC_CORRELATION:
        return None

    refined_lag = float(best_lag)
    previous = correlations.get(best_lag - 1)
    following = correlations.get(best_lag + 1)
    if previous is not None and following is not None:
        denominator = previous - 2.0 * best_correlation + following
        if abs(denominator) > 1e-9:
            refined_lag += 0.5 * (previous - following) / denominator

    if refined_lag <= 0.0:
        return None
    frequency = reduced_rate / refined_lag
    if not MIN_F0_HZ <= frequency <= MAX_F0_HZ:
        return None
    return float(frequency)


def _estimate_f0_values(
    samples: Sequence[int],
    sample_rate: int,
    activity_threshold: float,
) -> tuple[list[float], int]:
    frame_samples = max(1, round(sample_rate * F0_FRAME_MS / 1000))
    frames = [
        samples[start : start + frame_samples]
        for start in range(0, len(samples), frame_samples)
    ]
    active_frames = [frame for frame in frames if _rms(frame) >= activity_threshold]
    selected = _evenly_limit(active_frames, MAX_F0_FRAMES)
    estimates = [
        frequency
        for frame in selected
        if (frequency := _estimate_frame_f0(frame, sample_rate)) is not None
    ]
    return estimates, len(selected)


def _count_energy_events(
    rms_values: Sequence[float],
    active: Sequence[bool],
    activity_threshold: float,
) -> int:
    onsets = sum(
        1
        for index, is_active in enumerate(active)
        if is_active and (index == 0 or not active[index - 1])
    )
    peaks = sum(
        1
        for index in range(1, len(rms_values) - 1)
        if active[index]
        and rms_values[index] > rms_values[index - 1]
        and rms_values[index] >= rms_values[index + 1]
        and rms_values[index] >= activity_threshold * 1.20
    )
    return onsets + peaks


def extract_prosody_features(wav_path: Path) -> ProsodyFeatures:
    """Extract deterministic lightweight prosody features from a PCM16 WAV.

    ``speech_rate_proxy`` counts energy onsets and peaks per second. It is an
    activity-change proxy, not a word, syllable, or linguistic speech rate.
    """

    sample_rate, samples = _read_pcm16_mono(Path(wav_path))
    duration = len(samples) / sample_rate if sample_rate > 0 else 0.0
    if not samples:
        return ProsodyFeatures(duration_seconds=0.0)

    energy_frame_samples = max(1, round(sample_rate * ENERGY_FRAME_MS / 1000))
    rms_values = _frame_rms_values(samples, energy_frame_samples)
    rms_mean = fmean(rms_values) if rms_values else 0.0
    rms_peak = max(rms_values, default=0.0)
    variation = (
        pstdev(rms_values) / max(rms_mean, 1e-9)
        if len(rms_values) > 1
        else 0.0
    )
    energy_variation = _clamp(variation, 0.0, 1.0)

    activity_threshold = max(
        MIN_ACTIVITY_RMS,
        min(MAX_ACTIVITY_RMS, rms_peak * ACTIVITY_PEAK_RATIO),
    )
    active = [value >= activity_threshold for value in rms_values]
    active_ratio = sum(active) / len(active) if active else 0.0
    pause_ratio = _clamp(1.0 - active_ratio, 0.0, 1.0)

    f0_values, analyzed_voiced_frames = _estimate_f0_values(
        samples,
        sample_rate,
        activity_threshold,
    )
    if f0_values:
        f0_mean = fmean(f0_values)
        f0_std = pstdev(f0_values) if len(f0_values) > 1 else 0.0
    else:
        f0_mean = None
        f0_std = None
    periodic_ratio = (
        len(f0_values) / analyzed_voiced_frames
        if analyzed_voiced_frames > 0
        else 0.0
    )
    voiced_ratio = _clamp(periodic_ratio * active_ratio, 0.0, 1.0)

    event_count = _count_energy_events(rms_values, active, activity_threshold)
    speech_rate_proxy = (
        _clamp(event_count / duration, 0.0, MAX_SPEECH_RATE_PROXY)
        if duration > 0.0
        else 0.0
    )

    return ProsodyFeatures(
        rms_mean=rms_mean,
        rms_peak=rms_peak,
        energy_variation=energy_variation,
        f0_mean=f0_mean,
        f0_std=f0_std,
        voiced_ratio=voiced_ratio,
        pause_ratio=pause_ratio,
        duration_seconds=duration,
        speech_rate_proxy=speech_rate_proxy,
    )
