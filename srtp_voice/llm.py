from __future__ import annotations

import json
from typing import Any, Dict, List

from .config import AppConfig
from .types import EmotionResult, StrategyResult


class StrategyGenerator:
    """LLM 交互策略生成。

    默认 mock：规则回复 + 动作策略，保证离线可跑。
    后续可替换为 DeepSeek/Qwen/OpenAI-compatible API 或本地 Ollama/LM Studio。
    """

    def __init__(self, cfg: AppConfig):
        self.cfg = cfg

    def generate(self, user_text: str, emotion: EmotionResult, history: List[Dict[str, Any]]) -> StrategyResult:
        if self.cfg.llm_backend == "openai_compatible":
            return self._call_openai_compatible(user_text, emotion, history)
        return self._mock_generate(user_text, emotion, history)

    def _mock_generate(self, user_text: str, emotion: EmotionResult, history: List[Dict[str, Any]]) -> StrategyResult:
        if emotion.label in {"angry_or_excited", "tired_or_sad"} or any(k in user_text for k in ["不会", "做不下去", "崩", "烦", "困难"]):
            reply = "先不要把问题扩大化。我们先只保留语音输入、情绪识别、文本识别、回复生成和语音播放这条主链路，跑通后再接视觉和表情头。"
            expression = "concern_to_soft_smile"
            tts_style = {"speed": 0.92, "pitch": 0.0, "volume": 0.85}
        else:
            reply = "可以。当前版本先按语音通路闭环处理，我会把情绪结果和识别文本一起作为回复生成的输入。"
            expression = "neutral_smile"
            tts_style = {"speed": 1.00, "pitch": 0.0, "volume": 0.90}

        action = {
            "expression": expression,
            "gaze": "look_at_user",
            "blink": "natural",
            "mouth_sync": "audio_energy_placeholder",
            "tts_style": tts_style,
            "servo_targets_placeholder": {
                "mouth_open": 0.35,
                "left_eye": 0.50,
                "right_eye": 0.50,
                "brow": 0.40,
            },
        }
        return StrategyResult(reply_text=reply, action=action)

    def _call_openai_compatible(self, user_text: str, emotion: EmotionResult, history: List[Dict[str, Any]]) -> StrategyResult:
        if not self.cfg.llm_api_key:
            raise RuntimeError("LLM_BACKEND=openai_compatible 时需要设置 LLM_API_KEY")

        import requests

        system_prompt = (
            "你是表情机器人语音交互策略模块。"
            "根据用户文本、语音情绪和历史上下文，输出 JSON。"
            "JSON 字段必须包含 reply_text 和 action。"
            "action 中至少包含 expression, gaze, blink, mouth_sync, tts_style。"
            "回复应简洁、自然，不要输出 Markdown。"
        )
        payload = {
            "model": self.cfg.llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps({
                    "user_text": user_text,
                    "speech_emotion": emotion.to_dict(),
                    "history": history[-self.cfg.max_history_turns:],
                }, ensure_ascii=False)},
            ],
            "temperature": 0.4,
        }
        headers = {"Authorization": f"Bearer {self.cfg.llm_api_key}", "Content-Type": "application/json"}
        resp = requests.post(self.cfg.llm_base_url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        data = json.loads(content)
        return StrategyResult(reply_text=data["reply_text"], action=data["action"])
