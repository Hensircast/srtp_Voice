from __future__ import annotations

from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any, Dict


class DialogueStage(str, Enum):
    IDLE = "Idle"
    LISTENING = "Listening"
    THINKING = "Thinking"
    SPEAKING = "Speaking"


@dataclass
class DialogueState:
    stage: DialogueStage = DialogueStage.IDLE
    last_user_text: str = ""
    last_reply_text: str = ""

    def set_stage(self, stage: DialogueStage) -> None:
        self.stage = stage
        print(f"[STATE] -> {stage.value}")

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["stage"] = self.stage.value
        return data


class DialogueStateMachine:
    def __init__(self) -> None:
        self.state = DialogueState()
        self.trace = [self.state.stage.value]

    @property
    def stage(self) -> DialogueStage:
        return self.state.stage

    def set(self, stage: DialogueStage) -> None:
        self.state.set_stage(stage)
        self.trace.append(stage.value)

    def to_dict(self) -> Dict[str, Any]:
        data = self.state.to_dict()
        data["trace"] = self.trace
        return data
