from __future__ import annotations

import json
from typing import Any, Dict, List

import requests

from .config import AppConfig
from .types import EmotionResult, StrategyResult


DEFAULT_ACTION: Dict[str, Any] = {
    "expression": "neutral_smile",
    "gaze": "look_at_user",
    "blink": "natural",
    "mouth_sync": "short_time_energy",
    "tts_style": {"speed": 1.0, "pitch": 0.0, "volume": 0.9},
    "servo_targets_placeholder": {
        "mouth_open": 0.35,
        "left_eye": 0.50,
        "right_eye": 0.50,
        "brow": 0.40,
    },
}


OLLAMA_STRATEGY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "reply_text": {"type": "string"},
        "action": {
            "type": "object",
            "properties": {
                "expression": {"type": "string"},
                "gaze": {"type": "string"},
                "blink": {"type": "string"},
                "mouth_sync": {"type": "string"},
                "tts_style": {
                    "type": "object",
                    "properties": {
                        "speed": {"type": "number"},
                        "pitch": {"type": "number"},
                        "volume": {"type": "number"},
                    },
                    "required": ["speed", "pitch", "volume"],
                },
                "servo_targets_placeholder": {
                    "type": "object",
                    "properties": {
                        "mouth_open": {"type": "number"},
                        "left_eye": {"type": "number"},
                        "right_eye": {"type": "number"},
                        "brow": {"type": "number"},
                    },
                    "required": ["mouth_open", "left_eye", "right_eye", "brow"],
                },
            },
            "required": [
                "expression",
                "gaze",
                "blink",
                "mouth_sync",
                "tts_style",
                "servo_targets_placeholder",
            ],
        },
    },
    "required": ["reply_text", "action"],
}


class LLMResponseError(RuntimeError):
    pass


class StrategyGenerator:
    """Generate reply text and structured robot action strategy."""

    def __init__(self, cfg: AppConfig):
        self.cfg = cfg

    def generate(self, user_text: str, emotion: EmotionResult, history: List[Dict[str, Any]]) -> StrategyResult:
        backend = self.cfg.llm_backend.lower()
        if backend == "mock":
            return self._mock_generate(user_text, emotion, history)

        try:
            if backend == "ollama":
                return self._call_ollama(
                    user_text=user_text,
                    emotion=emotion,
                    history=history,
                )
            if backend == "lmstudio":
                return self._call_local_openai_compatible(
                    user_text=user_text,
                    emotion=emotion,
                    history=history,
                    chat_url=self.cfg.llm_lmstudio_chat_url,
                    runtime_name="LM Studio",
                )
        except Exception as exc:
            if self.cfg.llm_fallback_to_mock:
                print(f"      [LLM] {exc} Falling back to mock.")
                return self._mock_generate(user_text, emotion, history)
            raise

        raise ValueError("Unknown LLM_BACKEND. Use mock, ollama, or lmstudio.")

    def _mock_generate(self, user_text: str, emotion: EmotionResult, history: List[Dict[str, Any]]) -> StrategyResult:
        if emotion.label in {"angry_or_excited", "tired_or_sad"} or any(
            k in user_text for k in ["不会", "做不下去", "崩", "烦", "困难"]
        ):
            reply = "先不要把问题扩大化。我们先保留语音输入、情绪识别、文本识别、回复生成和语音播放这条主链路。"
            action = self._normalize_action({
                "expression": "concern_to_soft_smile",
                "tts_style": {"speed": 0.92, "pitch": 0.0, "volume": 0.85},
            })
        else:
            reply = "可以。当前版本先按语音通路闭环处理，我会把情绪结果和识别文本一起作为回复生成的输入。"
            action = self._normalize_action({})

        return StrategyResult(reply_text=reply, action=action)

    def _call_ollama(
        self,
        user_text: str,
        emotion: EmotionResult,
        history: List[Dict[str, Any]],
    ) -> StrategyResult:
        self._check_ollama()

        payload = {
            "model": self.cfg.llm_model,
            "messages": [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": json.dumps({
                    "user_text": user_text,
                    "speech_emotion": emotion.to_dict(),
                    "history": history[-self.cfg.max_history_turns:],
                }, ensure_ascii=False)},
            ],
            "stream": self.cfg.llm_stream,
            "think": self.cfg.llm_think,
            "format": OLLAMA_STRATEGY_SCHEMA,
            "options": {
                "temperature": self.cfg.llm_temperature,
                "num_predict": self.cfg.llm_max_tokens,
            },
        }

        try:
            resp = requests.post(self.cfg.llm_ollama_chat_url, json=payload, timeout=self.cfg.llm_timeout_seconds)
            resp.raise_for_status()
        except requests.Timeout as exc:
            raise RuntimeError("Ollama request timed out. Check the local model runtime.") from exc
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            raise RuntimeError(f"Ollama returned HTTP {status}. Check the loaded model and local server.") from exc
        except requests.RequestException as exc:
            raise RuntimeError(f"Cannot connect to Ollama at {self.cfg.llm_ollama_chat_url}.") from exc

        try:
            body = resp.json()
        except ValueError as exc:
            raise LLMResponseError("Ollama returned an invalid /api/chat JSON response.") from exc

        done_reason = body.get("done_reason")
        eval_count = body.get("eval_count")
        message = body.get("message", {})
        content = message.get("content", "") if isinstance(message, dict) else ""
        thinking = body.get("thinking", "")
        content_length = len(content) if isinstance(content, str) else 0
        thinking_length = len(thinking) if isinstance(thinking, str) else 0
        if done_reason == "length":
            raise LLMResponseError(
                "Ollama response was truncated because done_reason=length. "
                f"Increase LLM_MAX_TOKENS. eval_count={eval_count}, "
                f"content_length={content_length}, thinking_length={thinking_length}"
            )
        if not isinstance(message, dict) or not isinstance(content, str):
            raise LLMResponseError("Ollama returned an invalid /api/chat response.")

        return self.parse_strategy_content(content)

    def _call_local_openai_compatible(
        self,
        user_text: str,
        emotion: EmotionResult,
        history: List[Dict[str, Any]],
        chat_url: str,
        runtime_name: str,
    ) -> StrategyResult:
        if runtime_name == "Ollama":
            self._check_ollama()
        elif runtime_name == "LM Studio":
            self._check_lmstudio()

        payload = {
            "model": self.cfg.llm_model,
            "messages": [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": json.dumps({
                    "user_text": user_text,
                    "speech_emotion": emotion.to_dict(),
                    "history": history[-self.cfg.max_history_turns:],
                }, ensure_ascii=False)},
            ],
            "temperature": 0.4,
        }

        try:
            resp = requests.post(chat_url, json=payload, timeout=60)
            resp.raise_for_status()
        except requests.Timeout as exc:
            raise RuntimeError(f"{runtime_name} request timed out. Check the local model runtime.") from exc
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            raise RuntimeError(f"{runtime_name} returned HTTP {status}. Check the loaded model and local server.") from exc
        except requests.RequestException as exc:
            raise RuntimeError(f"Cannot connect to {runtime_name} at {chat_url}.") from exc

        try:
            body = resp.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError(f"{runtime_name} returned an invalid chat-completions response.") from exc

        return self.parse_strategy_content(content)

    def _check_ollama(self) -> None:
        tags_url = self.cfg.llm_ollama_base_url.rstrip("/") + "/api/tags"
        try:
            resp = requests.get(tags_url, timeout=5)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise RuntimeError(
                "Ollama is not running. Start it with PowerShell command: ollama serve"
            ) from exc
        except ValueError as exc:
            raise RuntimeError("Ollama /api/tags returned invalid JSON.") from exc

        names = {m.get("name", "") for m in data.get("models", []) if isinstance(m, dict)}
        if self.cfg.llm_model not in names:
            raise RuntimeError(
                f"Ollama model '{self.cfg.llm_model}' is not installed. "
                f"Download it with PowerShell command: ollama pull {self.cfg.llm_model}"
            )

    def _check_lmstudio(self) -> None:
        models_url = self.cfg.llm_lmstudio_base_url.rstrip("/") + "/v1/models"
        try:
            resp = requests.get(models_url, timeout=5)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise RuntimeError(
                "LM Studio local server is not running. Start LM Studio, load a model, and enable Local Server."
            ) from exc
        except ValueError as exc:
            raise RuntimeError("LM Studio /v1/models returned invalid JSON.") from exc

        model_ids = {m.get("id", "") for m in data.get("data", []) if isinstance(m, dict)}
        if model_ids and self.cfg.llm_model not in model_ids:
            raise RuntimeError(
                f"LM Studio model '{self.cfg.llm_model}' is not loaded. "
                "Load it in LM Studio Local Server or set LLM_MODEL to the listed model id."
            )

    def _system_prompt(self) -> str:
        return (
            "你是表情机器人语音交互策略模块。"
            "根据用户文本、语音情绪和历史上下文，只输出一个 JSON 对象。"
            "JSON 必须包含 reply_text 和 action。"
            "reply_text 必须是非空字符串。"
            "action 必须是对象，并可包含 expression, gaze, blink, mouth_sync, tts_style, servo_targets_placeholder。"
            "不要输出 Markdown，不要输出解释文字。"
        )

    @classmethod
    def parse_strategy_content(cls, content: str) -> StrategyResult:
        if not isinstance(content, str) or not content.strip():
            raise LLMResponseError("LLM response content is empty.")
        json_text = cls._extract_json_object(cls._strip_markdown_fence(content))
        try:
            data = json.loads(json_text)
        except json.JSONDecodeError as exc:
            raise LLMResponseError(f"LLM response is not valid JSON: {exc.msg}") from exc
        return cls._strategy_from_data(data)

    @staticmethod
    def _strip_markdown_fence(text: str) -> str:
        stripped = text.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if lines and lines[0].lstrip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            return "\n".join(lines).strip()
        return stripped

    @staticmethod
    def _extract_json_object(text: str) -> str:
        start = text.find("{")
        if start < 0:
            raise LLMResponseError("LLM response does not contain a JSON object.")

        depth = 0
        in_string = False
        escaped = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:idx + 1]

        raise LLMResponseError("LLM response contains an incomplete JSON object.")

    @classmethod
    def _strategy_from_data(cls, data: Any) -> StrategyResult:
        if not isinstance(data, dict):
            raise LLMResponseError("LLM JSON root must be an object.")

        reply_text = data.get("reply_text")
        if not isinstance(reply_text, str) or not reply_text.strip():
            raise LLMResponseError("LLM JSON field reply_text must be a non-empty string.")

        action = data.get("action", {})
        if not isinstance(action, dict):
            raise LLMResponseError("LLM JSON field action must be an object.")

        return StrategyResult(reply_text=reply_text.strip(), action=cls._normalize_action(action))

    @staticmethod
    def _normalize_action(action: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(DEFAULT_ACTION)
        normalized["tts_style"] = dict(DEFAULT_ACTION["tts_style"])
        normalized["servo_targets_placeholder"] = dict(DEFAULT_ACTION["servo_targets_placeholder"])

        for key, value in action.items():
            if key == "tts_style" and isinstance(value, dict):
                normalized["tts_style"].update(value)
            elif key == "servo_targets_placeholder" and isinstance(value, dict):
                normalized["servo_targets_placeholder"].update(value)
            elif key in DEFAULT_ACTION:
                normalized[key] = value
            else:
                normalized[key] = value

        return normalized
