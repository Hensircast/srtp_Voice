from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from .types import PipelineState
from .utils import load_json, save_json


class JsonMemory:
    """短期记忆：先用 JSON 文件保存最近几轮，方便调试和后续替换数据库。"""

    def __init__(self, path: Path, max_turns: int = 6):
        self.path = path
        self.max_turns = max_turns

    def load(self) -> List[Dict[str, Any]]:
        data = load_json(self.path, default=[])
        if not isinstance(data, list):
            return []
        return data[-self.max_turns:]

    def append(self, state: PipelineState) -> None:
        data = self.load()
        data.append(state.to_dict())
        save_json(self.path, data[-self.max_turns:])
