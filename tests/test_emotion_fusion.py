from __future__ import annotations

import json
import math

import pytest

from srtp_voice.emotion_fusion import (
    HEURISTIC_MAX_CONFIDENCE,
    INTENSITY_ACTIVITY_WEIGHT,
    INTENSITY_ENERGY_WEIGHT,
    INTENSITY_F0_VARIATION_WEIGHT,
    INTENSITY_PEAK_WEIGHT,
    INTENSITY_RATE_WEIGHT,
    INTENSITY_VARIATION_WEIGHT,
    classify_unknown_prosody,
    fuse_emotion,
    prosody_intensity,
    prosody_signal_quality,
)
from srtp_voice.types import EmotionResult, ProsodyFeatures


def _prosody(**overrides) -> ProsodyFeatures:
    values = {
        "rms_mean": 0.04,
        "rms_peak": 0.10,
        "energy_variation": 0.25,
        "f0_mean": 180.0,
        "f0_std": 20.0,
        "voiced_ratio": 0.65,
        "pause_ratio": 0.25,
        "duration_seconds": 1.2,
        "speech_rate_proxy": 4.0,
    }
    values.update(overrides)
    return ProsodyFeatures(**values)


def test_intensity_formula_uses_named_bounded_components() -> None:
    features = _prosody()
    expected = (
        (features.rms_mean / 0.12) * INTENSITY_ENERGY_WEIGHT
        + (features.rms_peak / 0.30) * INTENSITY_PEAK_WEIGHT
        + features.energy_variation * INTENSITY_VARIATION_WEIGHT
        + (1.0 - features.pause_ratio) * INTENSITY_ACTIVITY_WEIGHT
        + (features.f0_std / 60.0) * INTENSITY_F0_VARIATION_WEIGHT
        + (features.speech_rate_proxy / 8.0) * INTENSITY_RATE_WEIGHT
    )

    assert prosody_intensity(features) == pytest.approx(expected)
    assert 0.0 <= prosody_intensity(features) <= 1.0


@pytest.mark.parametrize(
    "sensevoice_label",
    [
        "neutral",
        "happy",
        "sad",
        "angry",
        "fear",
        "surprise",
        "disgust",
        "tired",
        "excited",
    ],
)
def test_known_sensevoice_label_wins_over_prosody_candidate(
    sensevoice_label,
) -> None:
    high_activity = _prosody(
        rms_mean=0.15,
        rms_peak=0.35,
        energy_variation=0.8,
        pause_ratio=0.05,
    )

    result = fuse_emotion(
        EmotionResult(sensevoice_label, 0.5, 0.5, {}),
        high_activity,
        source="sensevoice",
    )

    assert classify_unknown_prosody(high_activity) == "excited"
    assert result.emotion.label == sensevoice_label
    assert result.evidence[0].label == sensevoice_label
    assert result.evidence[1].label == "excited"


def test_unknown_uses_conservative_prosody_candidates() -> None:
    excited = _prosody(
        rms_mean=0.10,
        rms_peak=0.25,
        energy_variation=0.60,
        pause_ratio=0.20,
    )
    tired = _prosody(
        rms_mean=0.01,
        rms_peak=0.02,
        energy_variation=0.05,
        voiced_ratio=0.10,
        pause_ratio=0.80,
    )
    ordinary = _prosody(
        rms_mean=0.04,
        rms_peak=0.08,
        energy_variation=0.08,
        pause_ratio=0.30,
    )

    assert classify_unknown_prosody(excited) == "excited"
    assert classify_unknown_prosody(tired) == "tired"
    assert classify_unknown_prosody(ordinary) == "neutral"


def test_silence_is_neutral_low_strength_evidence() -> None:
    silence = ProsodyFeatures()
    result = fuse_emotion(
        EmotionResult("unknown", 0.0, 0.0, {}),
        silence,
        source="heuristic",
    )

    assert result.emotion.label == "neutral"
    assert result.emotion.intensity == 0.0
    assert result.emotion.confidence <= HEURISTIC_MAX_CONFIDENCE


def test_intensity_and_confidence_change_with_prosody() -> None:
    weak = ProsodyFeatures(
        rms_mean=0.005,
        rms_peak=0.010,
        pause_ratio=0.80,
        duration_seconds=0.10,
    )
    strong = _prosody(
        rms_mean=0.10,
        rms_peak=0.25,
        energy_variation=0.60,
        voiced_ratio=0.90,
        pause_ratio=0.10,
        duration_seconds=2.0,
        speech_rate_proxy=7.0,
    )

    weak_result = fuse_emotion(
        EmotionResult("neutral", 0.5, 0.5, {}),
        weak,
        source="sensevoice",
    )
    strong_result = fuse_emotion(
        EmotionResult("neutral", 0.5, 0.5, {}),
        strong,
        source="sensevoice",
    )

    assert strong_result.emotion.intensity > weak_result.emotion.intensity
    assert strong_result.emotion.confidence > weak_result.emotion.confidence
    assert prosody_signal_quality(strong) > prosody_signal_quality(weak)


def test_sensevoice_adapter_values_are_not_treated_as_model_probability() -> None:
    features = _prosody()
    low_placeholder = fuse_emotion(
        EmotionResult("happy", 0.0, 0.0, {}),
        features,
        source="sensevoice",
    )
    high_placeholder = fuse_emotion(
        EmotionResult("happy", 1.0, 1.0, {}),
        features,
        source="sensevoice",
    )

    assert low_placeholder.emotion.intensity == high_placeholder.emotion.intensity
    assert low_placeholder.emotion.confidence == high_placeholder.emotion.confidence
    assert (
        low_placeholder.emotion.confidence
        == low_placeholder.emotion.features["fusion_evidence_strength"]
    )


def test_known_sensevoice_label_survives_without_prosody() -> None:
    result = fuse_emotion(
        EmotionResult("happy", 0.9, 0.9, {}),
        None,
        source="sensevoice",
    )

    assert result.emotion.label == "happy"
    assert result.emotion.intensity == 0.5
    assert result.emotion.confidence == 0.5
    assert result.emotion.features == {
        "prosody_available": 0.0,
        "fusion_signal_quality": 0.0,
        "fusion_evidence_strength": 0.5,
        "fusion_is_fallback": 0.0,
    }
    assert result.evidence[1].features == {"available": False}


def test_unknown_without_prosody_is_conservative_and_has_no_measurements() -> None:
    result = fuse_emotion(
        EmotionResult("unknown", 0.5, 0.5, {}),
        None,
        source="sensevoice",
    )

    assert result.emotion.label == "neutral"
    assert result.emotion.intensity == 0.0
    assert result.emotion.confidence == 0.0
    assert result.emotion.features["prosody_available"] == 0.0
    assert not any(
        name.startswith(("rms", "f0"))
        for name in result.emotion.features
    )


def test_heuristic_and_fallback_are_marked_low_reliability() -> None:
    features = _prosody()
    heuristic = fuse_emotion(
        EmotionResult("unknown", 0.0, 0.0, {}),
        features,
        source="heuristic",
    )
    fallback = fuse_emotion(
        EmotionResult("unknown", 0.0, 0.0, {}),
        features,
        source="fallback",
        is_fallback=True,
    )

    assert heuristic.evidence[0].source == "heuristic"
    assert fallback.evidence[0].source == "fallback"
    assert fallback.evidence[0].is_fallback is True
    assert heuristic.emotion.confidence <= HEURISTIC_MAX_CONFIDENCE
    assert fallback.emotion.confidence <= HEURISTIC_MAX_CONFIDENCE


def test_nonfinite_and_out_of_range_inputs_are_sanitized() -> None:
    features = ProsodyFeatures(
        rms_mean=float("nan"),
        rms_peak=float("inf"),
        energy_variation=-5.0,
        f0_mean=float("nan"),
        f0_std=float("inf"),
        voiced_ratio=2.0,
        pause_ratio=-1.0,
        duration_seconds=float("inf"),
        speech_rate_proxy=-3.0,
    )
    result = fuse_emotion(
        EmotionResult("unknown", float("nan"), float("inf"), {}),
        features,
        source="fallback",
        is_fallback=True,
    )

    assert features.f0_mean is None
    assert features.f0_std is None
    assert 0.0 <= result.emotion.intensity <= 1.0
    assert 0.0 <= result.emotion.confidence <= 1.0
    assert all(math.isfinite(value) for value in result.emotion.features.values())
    json.dumps(result.to_dict(), allow_nan=False)


def test_fused_result_is_serializable_and_keeps_numeric_emotion_features() -> None:
    result = fuse_emotion(
        EmotionResult("surprise", 0.5, 0.5, {}),
        _prosody(),
        source="sensevoice",
        timestamp_ms=407,
    )

    payload = json.loads(json.dumps(result.to_dict(), allow_nan=False))

    assert payload["emotion"]["label"] == "surprise"
    assert payload["evidence"][0]["timestamp_ms"] == 407
    assert all(
        isinstance(value, (int, float))
        for value in payload["emotion"]["features"].values()
    )
