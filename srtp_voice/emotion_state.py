from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict

from .types import EmotionResult


EMOTION_TO_VAD = {
    "neutral": (0.0, 0.0, 0.0),
    "angry_or_excited": (-0.20, 0.75, 0.35),
    "tired_or_sad": (-0.50, -0.35, -0.35),
}


@dataclass
class EmotionState:
    valence: float = 0.0
    arousal: float = 0.0
    dominance: float = 0.0
    label: str = "neutral"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class EmotionStateTracker:
    def __init__(self, state_file: Path, alpha: float = 0.35):
        self.state_file = state_file
        self.alpha = alpha
        self.state = self._load()

    def _load(self) -> EmotionState:
        if not self.state_file.exists():
            return EmotionState()
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            return EmotionState(**data)
        except Exception:
            return EmotionState()

    def update(self, emotion: EmotionResult) -> EmotionState:
        target = EMOTION_TO_VAD.get(emotion.label, EMOTION_TO_VAD["neutral"])
        a = self.alpha * max(0.1, min(1.0, emotion.confidence))

        self.state.valence = (1 - a) * self.state.valence + a * target[0]
        self.state.arousal = (1 - a) * self.state.arousal + a * target[1]
        self.state.dominance = (1 - a) * self.state.dominance + a * target[2]
        self.state.label = emotion.label

        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(
            json.dumps(self.state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return self.state


EmotionStateSmoother = EmotionStateTracker
