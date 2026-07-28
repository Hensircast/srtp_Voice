from __future__ import annotations

import json
import math
import random
import struct
import wave
from pathlib import Path

import pytest

import srtp_voice.prosody as prosody_module
from srtp_voice.prosody import extract_prosody_features
from srtp_voice.types import EmotionResult


def _sine(
    *,
    sample_rate: int,
    frequency: float = 200.0,
    seconds: float = 0.4,
    amplitude: int = 8000,
) -> list[int]:
    return [
        round(amplitude * math.sin(2.0 * math.pi * frequency * index / sample_rate))
        for index in range(round(sample_rate * seconds))
    ]


def _write_pcm16(
    path: Path,
    samples: list[int],
    *,
    sample_rate: int = 16000,
    channels: int = 1,
) -> Path:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        if samples:
            wav_file.writeframes(struct.pack("<" + "h" * len(samples), *samples))
    return path


def _assert_finite(features) -> None:
    for value in features.to_dict().values():
        if value is not None:
            assert math.isfinite(float(value))
    assert 0.0 <= features.rms_mean <= 1.0
    assert 0.0 <= features.rms_peak <= 1.0
    assert 0.0 <= features.energy_variation <= 1.0
    assert 0.0 <= features.voiced_ratio <= 1.0
    assert 0.0 <= features.pause_ratio <= 1.0
    assert features.duration_seconds >= 0.0
    assert 0.0 <= features.speech_rate_proxy <= 20.0
    if features.f0_mean is not None:
        assert 70.0 <= features.f0_mean <= 400.0
    if features.f0_std is not None:
        assert features.f0_std >= 0.0


def test_emotion_result_legacy_positional_contract_is_preserved() -> None:
    result = EmotionResult("happy", 0.4, 0.6, {"rms": 0.1})

    assert result.label == "happy"
    assert result.intensity == 0.4
    assert result.confidence == 0.6
    assert result.features == {"rms": 0.1}
    assert result.to_dict() == {
        "label": "happy",
        "intensity": 0.4,
        "confidence": 0.6,
        "features": {"rms": 0.1},
    }


def test_silence_has_no_fabricated_f0(tmp_path) -> None:
    path = _write_pcm16(tmp_path / "silence.wav", [0] * 8000)

    features = extract_prosody_features(path)

    assert features.rms_mean == 0.0
    assert features.rms_peak == 0.0
    assert features.f0_mean is None
    assert features.f0_std is None
    assert features.voiced_ratio == 0.0
    assert features.pause_ratio == 1.0
    _assert_finite(features)


@pytest.mark.parametrize("sample_rate", [8000, 16000, 44100])
def test_estimates_200_hz_at_common_sample_rates(tmp_path, sample_rate) -> None:
    path = _write_pcm16(
        tmp_path / f"sine-{sample_rate}.wav",
        _sine(sample_rate=sample_rate),
        sample_rate=sample_rate,
    )

    features = extract_prosody_features(path)

    assert features.f0_mean == pytest.approx(200.0, abs=8.0)
    assert features.voiced_ratio > 0.5
    assert features.duration_seconds == pytest.approx(0.4, abs=0.01)
    _assert_finite(features)


@pytest.mark.parametrize("channels", [2, 3])
def test_multichannel_is_downmixed_safely(tmp_path, channels) -> None:
    mono = _sine(sample_rate=16000)
    interleaved = [value for sample in mono for value in (sample,) * channels]
    path = _write_pcm16(
        tmp_path / f"{channels}-channel.wav",
        interleaved,
        sample_rate=16000,
        channels=channels,
    )

    features = extract_prosody_features(path)

    assert features.f0_mean == pytest.approx(200.0, abs=8.0)
    assert features.duration_seconds == pytest.approx(0.4, abs=0.01)


def test_empty_and_very_short_wav_are_safe(tmp_path) -> None:
    empty = extract_prosody_features(_write_pcm16(tmp_path / "empty.wav", []))
    short = extract_prosody_features(
        _write_pcm16(tmp_path / "short.wav", [100, -100, 50])
    )

    assert empty.duration_seconds == 0.0
    assert empty.f0_mean is None
    assert short.duration_seconds > 0.0
    assert short.f0_mean is None
    _assert_finite(empty)
    _assert_finite(short)


def test_missing_and_damaged_wav_errors_include_context(tmp_path) -> None:
    missing = tmp_path / "missing.wav"
    with pytest.raises(FileNotFoundError, match="missing.wav"):
        extract_prosody_features(missing)

    damaged = tmp_path / "damaged.wav"
    damaged.write_bytes(b"not a wav")
    with pytest.raises(ValueError, match="damaged.wav"):
        extract_prosody_features(damaged)

    truncated = _write_pcm16(tmp_path / "truncated.wav", [100] * 100)
    truncated.write_bytes(truncated.read_bytes()[:-1])
    with pytest.raises(ValueError, match="truncated.*truncated.wav"):
        extract_prosody_features(truncated)


def test_non_pcm16_wav_is_rejected(tmp_path) -> None:
    path = tmp_path / "pcm8.wav"
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(1)
        wav_file.setframerate(16000)
        wav_file.writeframes(bytes([128] * 160))

    with pytest.raises(ValueError, match="16-bit PCM.*pcm8.wav"):
        extract_prosody_features(path)


def test_compressed_wav_format_is_rejected(tmp_path) -> None:
    path = tmp_path / "compressed.wav"
    fmt_chunk = struct.pack("<HHIIHH", 6, 1, 8000, 8000, 1, 8)
    audio_data = bytes([0xD5] * 80)
    riff_size = 4 + (8 + len(fmt_chunk)) + (8 + len(audio_data))
    path.write_bytes(
        b"RIFF"
        + struct.pack("<I", riff_size)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<I", len(fmt_chunk))
        + fmt_chunk
        + b"data"
        + struct.pack("<I", len(audio_data))
        + audio_data
    )

    with pytest.raises(ValueError, match="compressed.wav"):
        extract_prosody_features(path)


def test_nonperiodic_audio_can_have_no_f0(tmp_path) -> None:
    generator = random.Random(407)
    samples = [generator.randint(-500, 500) for _ in range(3200)]
    path = _write_pcm16(tmp_path / "noise.wav", samples)

    features = extract_prosody_features(path)

    assert features.f0_mean is None
    assert features.f0_std is None
    _assert_finite(features)


def test_f0_analysis_limits_long_audio_to_bounded_frame_count(
    monkeypatch,
    tmp_path,
) -> None:
    path = _write_pcm16(
        tmp_path / "long.wav",
        _sine(sample_rate=44100, seconds=2.0),
        sample_rate=44100,
    )
    calls = []

    def fake_estimate(frame, sample_rate):
        calls.append((len(frame), sample_rate))
        return 200.0

    monkeypatch.setattr(prosody_module, "_estimate_frame_f0", fake_estimate)

    features = extract_prosody_features(path)

    assert len(calls) == prosody_module.MAX_F0_FRAMES
    assert features.f0_mean == 200.0


def test_result_is_json_serializable(tmp_path) -> None:
    path = _write_pcm16(
        tmp_path / "serializable.wav",
        _sine(sample_rate=16000),
    )

    features = extract_prosody_features(path)
    payload = json.loads(json.dumps(features.to_dict(), allow_nan=False))

    assert payload["duration_seconds"] > 0.0
    assert payload["f0_mean"] == pytest.approx(200.0, abs=8.0)
