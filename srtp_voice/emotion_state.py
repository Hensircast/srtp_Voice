from __future__ import annotations

import json
import math
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict

from .types import EmotionResult


EMOTION_TO_VAD = {
    "neutral": (0.0, 0.0, 0.0),
    "happy": (0.70, 0.45, 0.35),
    "sad": (-0.65, -0.40, -0.45),
    "angry": (-0.65, 0.80, 0.55),
    "fear": (-0.70, 0.70, -0.55),
    "surprise": (0.20, 0.80, 0.10),
    "disgust": (-0.70, 0.35, 0.25),
    "tired": (-0.35, -0.55, -0.30),
    "excited": (0.55, 0.85, 0.45),
    "unknown": (0.0, 0.0, 0.0),
}

DEFAULT_DECAY_HALF_LIFE_SECONDS = 120.0
DEFAULT_MAX_STEP = 0.25
MIN_LABEL_DISTANCE_FROM_NEUTRAL = 0.12
UNKNOWN_EVIDENCE_MULTIPLIER = 0.10
INTENSITY_WEIGHT_FLOOR = 0.25
INTENSITY_WEIGHT_SCALE = 0.75


def _finite_float(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clamp(value: object, minimum: float, maximum: float, default: float = 0.0) -> float:
    return max(minimum, min(maximum, _finite_float(value, default)))


def _state_label(valence: float, arousal: float, dominance: float) -> str:
    magnitude = math.sqrt(valence * valence + arousal * arousal + dominance * dominance)
    if magnitude < MIN_LABEL_DISTANCE_FROM_NEUTRAL:
        return "neutral"
    candidates = {
        label: values
        for label, values in EMOTION_TO_VAD.items()
        if label not in {"neutral", "unknown"}
    }
    return min(
        candidates,
        key=lambda label: sum(
            (current - target) ** 2
            for current, target in zip(
                (valence, arousal, dominance),
                candidates[label],
            )
        ),
    )


@dataclass
class EmotionState:
    valence: float = 0.0
    arousal: float = 0.0
    dominance: float = 0.0
    label: str = "neutral"
    intensity: float = 0.0
    confidence: float = 0.0
    updated_at: float | None = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class EmotionStateTracker:
    def __init__(
        self,
        state_file: Path,
        alpha: float = 0.35,
        decay_half_life_seconds: float = DEFAULT_DECAY_HALF_LIFE_SECONDS,
        max_step: float = DEFAULT_MAX_STEP,
    ):
        self.state_file = Path(state_file)
        self.alpha = _clamp(alpha, 0.0, 1.0)
        self.decay_half_life_seconds = _finite_float(decay_half_life_seconds)
        self.max_step = _finite_float(max_step)
        if self.decay_half_life_seconds <= 0.0:
            raise ValueError("emotion decay half-life must be greater than 0 seconds")
        if not 0.0 < self.max_step <= 2.0:
            raise ValueError("emotion max step must be in the range (0, 2]")
        self.state = self._load()

    def _load(self) -> EmotionState:
        if not self.state_file.is_file():
            return EmotionState()
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            return EmotionState()
        if not isinstance(data, dict):
            return EmotionState()

        label = data.get("label", "neutral")
        if not isinstance(label, str) or label not in EMOTION_TO_VAD:
            label = "neutral"
        raw_updated_at = data.get("updated_at")
        updated_at = None
        if raw_updated_at is not None:
            parsed_timestamp = _finite_float(raw_updated_at, -1.0)
            if parsed_timestamp >= 0.0:
                updated_at = parsed_timestamp
        return EmotionState(
            valence=_clamp(data.get("valence", 0.0), -1.0, 1.0),
            arousal=_clamp(data.get("arousal", 0.0), -1.0, 1.0),
            dominance=_clamp(data.get("dominance", 0.0), -1.0, 1.0),
            label=label,
            intensity=_clamp(data.get("intensity", 0.0), 0.0, 1.0),
            confidence=_clamp(data.get("confidence", 0.0), 0.0, 1.0),
            updated_at=updated_at,
        )

    @staticmethod
    def _timestamp(now: float | None) -> float:
        if now is None:
            return time.time()
        timestamp = _finite_float(now, -1.0)
        if timestamp < 0.0:
            raise ValueError("emotion state timestamp must be a finite non-negative value")
        return timestamp

    def _apply_decay(self, now: float) -> None:
        previous = self.state.updated_at
        if previous is None:
            self.state.updated_at = now
            return
        elapsed = max(0.0, now - previous)
        decay_factor = math.pow(0.5, elapsed / self.decay_half_life_seconds)
        self.state.valence *= decay_factor
        self.state.arousal *= decay_factor
        self.state.dominance *= decay_factor
        self.state.intensity *= decay_factor
        self.state.confidence *= decay_factor
        self.state.updated_at = max(previous, now)

    def _save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            self.state.to_dict(),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        file_descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{self.state_file.name}.",
            suffix=".tmp",
            dir=self.state_file.parent,
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.state_file)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def decay(self, now: float | None = None) -> EmotionState:
        current_time = self._timestamp(now)
        self._apply_decay(current_time)
        self.state.label = _state_label(
            self.state.valence,
            self.state.arousal,
            self.state.dominance,
        )
        self._save()
        return self.state

    def update(self, emotion: EmotionResult, now: float | None = None) -> EmotionState:
        current_time = self._timestamp(now)
        self._apply_decay(current_time)

        label = emotion.label if emotion.label in EMOTION_TO_VAD else "unknown"
        confidence = _clamp(emotion.confidence, 0.0, 1.0)
        intensity = _clamp(emotion.intensity, 0.0, 1.0)
        evidence_weight = self.alpha * confidence * (
            INTENSITY_WEIGHT_FLOOR + INTENSITY_WEIGHT_SCALE * intensity
        )
        if label == "unknown":
            evidence_weight *= UNKNOWN_EVIDENCE_MULTIPLIER

        target = EMOTION_TO_VAD[label]
        for field_name, target_value in zip(
            ("valence", "arousal", "dominance"),
            target,
        ):
            current_value = getattr(self.state, field_name)
            desired_value = target_value * intensity
            delta = (desired_value - current_value) * evidence_weight
            limited_delta = _clamp(delta, -self.max_step, self.max_step)
            setattr(
                self.state,
                field_name,
                _clamp(current_value + limited_delta, -1.0, 1.0),
            )

        self.state.intensity = _clamp(
            self.state.intensity
            + (intensity - self.state.intensity) * evidence_weight,
            0.0,
            1.0,
        )
        self.state.confidence = _clamp(
            self.state.confidence
            + (confidence - self.state.confidence) * evidence_weight,
            0.0,
            1.0,
        )
        self.state.label = _state_label(
            self.state.valence,
            self.state.arousal,
            self.state.dominance,
        )
        self.state.updated_at = max(self.state.updated_at or current_time, current_time)
        self._save()
        return self.state


EmotionStateSmoother = EmotionStateTracker
