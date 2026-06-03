from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List


@dataclass
class EmotionResult:
    label: str
    intensity: float
    confidence: float
    features: Dict[str, float]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


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
