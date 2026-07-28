from __future__ import annotations

import math
from typing import Final

from .types import (
    EmotionEvidence,
    EmotionResult,
    FusedEmotionResult,
    ProsodyFeatures,
)


KNOWN_EMOTION_LABELS: Final[frozenset[str]] = frozenset(
    {
        "neutral",
        "happy",
        "sad",
        "angry",
        "fear",
        "surprise",
        "disgust",
        "tired",
        "excited",
    }
)

ENERGY_REFERENCE_RMS: Final[float] = 0.12
PEAK_REFERENCE_RMS: Final[float] = 0.30
F0_VARIATION_REFERENCE_HZ: Final[float] = 60.0
SPEECH_RATE_REFERENCE: Final[float] = 8.0

INTENSITY_ENERGY_WEIGHT: Final[float] = 0.38
INTENSITY_PEAK_WEIGHT: Final[float] = 0.17
INTENSITY_VARIATION_WEIGHT: Final[float] = 0.20
INTENSITY_ACTIVITY_WEIGHT: Final[float] = 0.15
INTENSITY_F0_VARIATION_WEIGHT: Final[float] = 0.05
INTENSITY_RATE_WEIGHT: Final[float] = 0.05

QUALITY_DURATION_SECONDS: Final[float] = 1.0
QUALITY_LEVEL_REFERENCE_RMS: Final[float] = 0.04
QUALITY_DURATION_WEIGHT: Final[float] = 0.25
QUALITY_LEVEL_WEIGHT: Final[float] = 0.30
QUALITY_ACTIVITY_WEIGHT: Final[float] = 0.25
QUALITY_PERIODICITY_WEIGHT: Final[float] = 0.20

SENSEVOICE_LABEL_EVIDENCE_BASE: Final[float] = 0.55
SENSEVOICE_QUALITY_WEIGHT: Final[float] = 0.30
HEURISTIC_EVIDENCE_BASE: Final[float] = 0.15
HEURISTIC_QUALITY_WEIGHT: Final[float] = 0.40
HEURISTIC_MAX_CONFIDENCE: Final[float] = 0.55

ABSOLUTE_SILENCE_RMS: Final[float] = 0.001
LOW_ENERGY_RMS: Final[float] = 0.025
LOW_ACTIVITY_RATIO: Final[float] = 0.45
HIGH_ENERGY_RMS: Final[float] = 0.070
HIGH_ENERGY_VARIATION: Final[float] = 0.18
HIGH_ACTIVITY_RATIO: Final[float] = 0.40


def _clamp01(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return max(0.0, min(1.0, number))


def _normalized_label(value: object) -> str:
    if not isinstance(value, str):
        return "unknown"
    label = value.strip().lower()
    return label if label in KNOWN_EMOTION_LABELS else "unknown"


def prosody_intensity(features: ProsodyFeatures) -> float:
    """Return deterministic expressive strength derived from acoustic features."""

    energy = _clamp01(features.rms_mean / ENERGY_REFERENCE_RMS)
    peak = _clamp01(features.rms_peak / PEAK_REFERENCE_RMS)
    variation = _clamp01(features.energy_variation)
    activity = _clamp01(1.0 - features.pause_ratio)
    f0_variation = _clamp01(
        (features.f0_std or 0.0) / F0_VARIATION_REFERENCE_HZ
    )
    speech_rate = _clamp01(features.speech_rate_proxy / SPEECH_RATE_REFERENCE)
    return _clamp01(
        energy * INTENSITY_ENERGY_WEIGHT
        + peak * INTENSITY_PEAK_WEIGHT
        + variation * INTENSITY_VARIATION_WEIGHT
        + activity * INTENSITY_ACTIVITY_WEIGHT
        + f0_variation * INTENSITY_F0_VARIATION_WEIGHT
        + speech_rate * INTENSITY_RATE_WEIGHT
    )


def prosody_signal_quality(features: ProsodyFeatures) -> float:
    """Estimate evidence strength from duration, level, activity, and periodicity."""

    duration = _clamp01(features.duration_seconds / QUALITY_DURATION_SECONDS)
    level = _clamp01(features.rms_peak / QUALITY_LEVEL_REFERENCE_RMS)
    activity = _clamp01((1.0 - features.pause_ratio) * 1.25)
    periodicity = _clamp01(features.voiced_ratio)
    return _clamp01(
        duration * QUALITY_DURATION_WEIGHT
        + level * QUALITY_LEVEL_WEIGHT
        + activity * QUALITY_ACTIVITY_WEIGHT
        + periodicity * QUALITY_PERIODICITY_WEIGHT
    )


def classify_unknown_prosody(features: ProsodyFeatures) -> str:
    """Return a conservative label when no reliable discrete label is available.

    Prosody cannot distinguish happy from angry or sad from tired. It only
    contributes conservative activity candidates.
    """

    activity = _clamp01(1.0 - features.pause_ratio)
    if features.rms_peak <= ABSOLUTE_SILENCE_RMS:
        return "neutral"
    if (
        features.rms_mean >= HIGH_ENERGY_RMS
        and features.energy_variation >= HIGH_ENERGY_VARIATION
        and activity >= HIGH_ACTIVITY_RATIO
    ):
        return "excited"
    if features.rms_mean <= LOW_ENERGY_RMS and activity <= LOW_ACTIVITY_RATIO:
        return "tired"
    return "neutral"


def _primary_evidence_strength(
    source: str,
    label: str,
    signal_quality: float,
) -> float:
    if source == "sensevoice" and label != "unknown":
        return _clamp01(
            SENSEVOICE_LABEL_EVIDENCE_BASE
            + signal_quality * SENSEVOICE_QUALITY_WEIGHT
        )
    return min(
        HEURISTIC_MAX_CONFIDENCE,
        _clamp01(
            HEURISTIC_EVIDENCE_BASE
            + signal_quality * HEURISTIC_QUALITY_WEIGHT
        ),
    )


def fuse_emotion(
    base_emotion: EmotionResult,
    prosody: ProsodyFeatures,
    *,
    source: str,
    is_fallback: bool = False,
    timestamp_ms: int | None = None,
) -> FusedEmotionResult:
    """Fuse a discrete label with prosody-derived intensity and evidence strength.

    ``confidence`` is fusion evidence strength. It is not a calibrated
    SenseVoice model probability.
    """

    normalized_source = str(source).strip().lower() or "heuristic"
    discrete_label = _normalized_label(base_emotion.label)
    prosody_label = classify_unknown_prosody(prosody)
    intensity = prosody_intensity(prosody)
    signal_quality = prosody_signal_quality(prosody)

    if normalized_source == "sensevoice" and discrete_label != "unknown":
        final_label = discrete_label
    elif discrete_label != "unknown" and normalized_source not in {
        "heuristic",
        "fallback",
    }:
        final_label = discrete_label
    else:
        final_label = prosody_label

    primary_strength = _primary_evidence_strength(
        normalized_source,
        discrete_label,
        signal_quality,
    )
    prosody_strength = _clamp01(
        HEURISTIC_EVIDENCE_BASE + signal_quality * HEURISTIC_QUALITY_WEIGHT
    )
    final_confidence = (
        primary_strength
        if normalized_source == "sensevoice" and discrete_label != "unknown"
        else min(primary_strength, prosody_strength)
    )

    numeric_features = prosody.numeric_dict()
    numeric_features.update(
        {
            "fusion_signal_quality": signal_quality,
            "fusion_evidence_strength": final_confidence,
            "fusion_is_fallback": 1.0 if is_fallback else 0.0,
        }
    )
    emotion = EmotionResult(
        label=final_label,
        intensity=intensity,
        confidence=final_confidence,
        features=numeric_features,
    )
    evidence = [
        EmotionEvidence(
            source=normalized_source,
            label=discrete_label,
            intensity=intensity,
            confidence=primary_strength,
            features={
                "discrete_label_available": discrete_label != "unknown",
                "signal_quality": signal_quality,
            },
            is_fallback=is_fallback,
            timestamp_ms=timestamp_ms,
        ),
        EmotionEvidence(
            source="prosody",
            label=prosody_label,
            intensity=intensity,
            confidence=prosody_strength,
            features=prosody.to_dict(),
            is_fallback=is_fallback,
            timestamp_ms=timestamp_ms,
        ),
    ]
    return FusedEmotionResult(emotion=emotion, evidence=evidence)
