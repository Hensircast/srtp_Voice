from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Mapping


def _finite_float(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clamp01(value: object, default: float = 0.0) -> float:
    return max(0.0, min(1.0, _finite_float(value, default)))


def _numeric_features(features: Mapping[str, object] | None) -> Dict[str, float]:
    cleaned: Dict[str, float] = {}
    for key, value in (features or {}).items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        number = float(value)
        if math.isfinite(number):
            cleaned[str(key)] = number
    return cleaned


def _serializable_features(features: Mapping[str, object] | None) -> Dict[str, Any]:
    cleaned: Dict[str, Any] = {}
    for key, value in (features or {}).items():
        if value is None or isinstance(value, (str, bool)):
            cleaned[str(key)] = value
        elif isinstance(value, (int, float)):
            cleaned[str(key)] = _finite_float(value)
        elif isinstance(value, Mapping):
            cleaned[str(key)] = _serializable_features(value)
        elif isinstance(value, (list, tuple)):
            cleaned[str(key)] = [
                _finite_float(item) if isinstance(item, (int, float)) else str(item)
                for item in value
            ]
        else:
            cleaned[str(key)] = str(value)
    return cleaned


@dataclass
class EmotionResult:
    label: str
    intensity: float
    confidence: float
    features: Dict[str, float]

    def __post_init__(self) -> None:
        self.label = str(self.label)
        self.intensity = _clamp01(self.intensity)
        self.confidence = _clamp01(self.confidence)
        self.features = _numeric_features(self.features)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProsodyFeatures:
    rms_mean: float = 0.0
    rms_peak: float = 0.0
    energy_variation: float = 0.0
    f0_mean: float | None = None
    f0_std: float | None = None
    voiced_ratio: float = 0.0
    pause_ratio: float = 1.0
    duration_seconds: float = 0.0
    speech_rate_proxy: float = 0.0

    def __post_init__(self) -> None:
        self.rms_mean = _clamp01(self.rms_mean)
        self.rms_peak = _clamp01(self.rms_peak)
        self.energy_variation = _clamp01(self.energy_variation)
        self.voiced_ratio = _clamp01(self.voiced_ratio)
        self.pause_ratio = _clamp01(self.pause_ratio, 1.0)
        self.duration_seconds = max(0.0, _finite_float(self.duration_seconds))
        self.speech_rate_proxy = max(0.0, _finite_float(self.speech_rate_proxy))
        self.f0_mean = self._optional_positive(self.f0_mean)
        self.f0_std = self._optional_nonnegative(self.f0_std)

    @staticmethod
    def _optional_positive(value: object) -> float | None:
        number = _finite_float(value, -1.0)
        return number if number > 0.0 else None

    @staticmethod
    def _optional_nonnegative(value: object) -> float | None:
        if value is None:
            return None
        number = _finite_float(value, -1.0)
        return number if number >= 0.0 else None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def numeric_dict(self) -> Dict[str, float]:
        data = self.to_dict()
        return {
            key: float(value)
            for key, value in data.items()
            if isinstance(value, (int, float)) and math.isfinite(float(value))
        }


@dataclass
class EmotionEvidence:
    source: str
    label: str
    intensity: float
    confidence: float
    features: Dict[str, Any]
    is_fallback: bool = False
    is_final: bool = True
    timestamp_ms: int | None = None

    def __post_init__(self) -> None:
        self.source = str(self.source)
        self.label = str(self.label)
        self.intensity = _clamp01(self.intensity)
        self.confidence = _clamp01(self.confidence)
        self.features = _serializable_features(self.features)
        self.is_fallback = bool(self.is_fallback)
        self.is_final = bool(self.is_final)
        if self.timestamp_ms is not None:
            timestamp = _finite_float(self.timestamp_ms, -1.0)
            self.timestamp_ms = max(0, int(timestamp)) if timestamp >= 0.0 else None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FusedEmotionResult:
    emotion: EmotionResult
    evidence: List[EmotionEvidence]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "emotion": self.emotion.to_dict(),
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass
class StrategyResult:
    reply_text: str
    action: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PipelineState:
    user_audio: str
    user_text: str
    emotion: EmotionResult
    reply_text: str
    action: Dict[str, Any]
    reply_audio: str

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["emotion"] = self.emotion.to_dict()
        return data
