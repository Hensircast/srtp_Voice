from __future__ import annotations

import json
from typing import Any, Dict, List

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover - exercised by monkeypatch tests
    requests = None

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


EXPRESSION_VALUES = {"neutral", "neutral_smile", "happy", "concerned", "sad", "surprised"}
GAZE_VALUES = {"look_at_user", "center", "left", "right"}
BLINK_VALUES = {"natural", "slow", "frequent", "none"}


OLLAMA_STRATEGY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "reply_text": {"type": "string"},
        "action": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "enum": sorted(EXPRESSION_VALUES)},
                "gaze": {"type": "string", "enum": sorted(GAZE_VALUES)},
                "blink": {"type": "string", "enum": sorted(BLINK_VALUES)},
                "mouth_sync": {"type": "string", "const": "short_time_energy"},
                "tts_style": {
                    "type": "object",
                    "properties": {
                        "speed": {"type": "number", "minimum": 0.8, "maximum": 1.2},
                        "pitch": {"type": "number", "minimum": -1.0, "maximum": 1.0},
                        "volume": {"type": "number", "minimum": 0.5, "maximum": 1.0},
                    },
                    "required": ["speed", "pitch", "volume"],
                    "additionalProperties": False,
                },
                "servo_targets_placeholder": {
                    "type": "object",
                    "properties": {
                        "mouth_open": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        "left_eye": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        "right_eye": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        "brow": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    },
                    "required": ["mouth_open", "left_eye", "right_eye", "brow"],
                    "additionalProperties": False,
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
            "additionalProperties": False,
        },
    },
    "required": ["reply_text", "action"],
    "additionalProperties": False,
}


class LLMResponseError(RuntimeError):
    pass


def _require_requests():
    if requests is None:
        raise RuntimeError(
            "The requests package is required for Ollama and LM Studio backends. "
            "Install it with: python -m pip install requests"
        )
    return requests


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
                "expression": "concerned",
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
        req = _require_requests()
        if self._uses_standard_ollama_chat_url():
            self._check_ollama()

        payload = {
            "model": self.cfg.llm_model,
            "messages": self._build_messages(user_text, emotion, history),
            "stream": False,
            "format": OLLAMA_STRATEGY_SCHEMA,
            "options": {
                "temperature": self.cfg.llm_temperature,
                "num_predict": self.cfg.llm_max_tokens,
                "num_ctx": self.cfg.llm_context_tokens,
            },
        }

        try:
            resp = req.post(self.cfg.llm_ollama_chat_url, json=payload, timeout=self.cfg.llm_timeout_seconds)
            resp.raise_for_status()
        except req.Timeout as exc:
            raise RuntimeError("Ollama request timed out. Check the local model runtime.") from exc
        except req.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            response_text = exc.response.text[:500] if exc.response is not None else ""
            detail = f" Response body: {response_text}" if response_text else ""
            context_hint = self._context_error_hint(response_text)
            raise RuntimeError(
                f"Ollama returned HTTP {status}. Check the loaded model and local server."
                f"{context_hint}{detail}"
            ) from exc
        except req.RequestException as exc:
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

    def _uses_standard_ollama_chat_url(self) -> bool:
        derived_chat_url = self.cfg.llm_ollama_base_url.rstrip("/") + "/api/chat"
        return self.cfg.llm_ollama_chat_url.rstrip("/") == derived_chat_url.rstrip("/")

    def _call_local_openai_compatible(
        self,
        user_text: str,
        emotion: EmotionResult,
        history: List[Dict[str, Any]],
        chat_url: str,
        runtime_name: str,
    ) -> StrategyResult:
        req = _require_requests()
        if runtime_name == "Ollama":
            self._check_ollama()
        elif runtime_name == "LM Studio":
            self._check_lmstudio()

        payload = {
            "model": self.cfg.llm_model,
            "messages": self._build_messages(user_text, emotion, history),
            "temperature": self.cfg.llm_temperature,
            "max_tokens": self.cfg.llm_max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "robot_interaction_strategy",
                    "strict": True,
                    "schema": OLLAMA_STRATEGY_SCHEMA,
                },
            },
        }

        try:
            resp = req.post(chat_url, json=payload, timeout=self.cfg.llm_timeout_seconds)
            resp.raise_for_status()
        except req.Timeout as exc:
            raise RuntimeError(f"{runtime_name} request timed out. Check the local model runtime.") from exc
        except req.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            raise RuntimeError(f"{runtime_name} returned HTTP {status}. Check the loaded model and local server.") from exc
        except req.RequestException as exc:
            raise RuntimeError(f"Cannot connect to {runtime_name} at {chat_url}.") from exc

        try:
            body = resp.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError(f"{runtime_name} returned an invalid chat-completions response.") from exc

        return self.parse_strategy_content(content)

    def _check_ollama(self) -> None:
        req = _require_requests()
        tags_url = self.cfg.llm_ollama_base_url.rstrip("/") + "/api/tags"
        try:
            resp = req.get(tags_url, timeout=5)
            resp.raise_for_status()
            data = resp.json()
        except req.RequestException as exc:
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
        req = _require_requests()
        models_url = self.cfg.llm_lmstudio_base_url.rstrip("/") + "/v1/models"
        try:
            resp = req.get(models_url, timeout=5)
            resp.raise_for_status()
            data = resp.json()
        except req.RequestException as exc:
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
            "数字、代号、型号、姓名、日期和专有名词必须逐字保留。"
            "查询历史事实时必须依据历史消息原文回答。"
            "不得近似改写、猜测或替换历史代号。"
            "不要输出 Markdown，不要输出解释文字。"
        )

    def _build_messages(
        self,
        user_text: str,
        emotion: EmotionResult,
        history: List[Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        messages = [{"role": "system", "content": self._system_prompt()}]
        selected_history = [] if self.cfg.max_history_turns <= 0 else history[-self.cfg.max_history_turns:]
        for item in selected_history:
            if not isinstance(item, dict):
                continue
            history_user = item.get("user_text")
            history_reply = item.get("reply_text")
            if isinstance(history_user, str) and history_user.strip():
                messages.append({"role": "user", "content": history_user})
            if isinstance(history_reply, str) and history_reply.strip():
                messages.append({"role": "assistant", "content": history_reply})

        current_content = (
            f"当前用户原文：{user_text}\n"
            f"语音情绪JSON：{json.dumps(emotion.to_dict(), ensure_ascii=False)}"
        )
        messages.append({"role": "user", "content": current_content})
        return messages

    @staticmethod
    def _context_error_hint(response_text: str) -> str:
        if "exceeds the available context size" not in response_text and "exceed_context_size_error" not in response_text:
            return ""

        current = "unknown"
        context = "unknown"
        try:
            outer = json.loads(response_text)
            inner = outer.get("error")
            if isinstance(inner, str):
                inner = json.loads(inner)
            if isinstance(inner, dict):
                error = inner.get("error", inner)
                if isinstance(error, dict):
                    current = str(error.get("n_prompt_tokens", current))
                    context = str(error.get("n_ctx", context))
        except (ValueError, TypeError):
            pass

        return (
            f" Context size exceeded: request_tokens={current}, context_size={context}. "
            "Increase LLM_CONTEXT_TOKENS or reduce history."
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
            if key == "tts_style":
                if isinstance(value, dict):
                    normalized["tts_style"].update(value)
                continue
            if key == "servo_targets_placeholder":
                if isinstance(value, dict):
                    normalized["servo_targets_placeholder"].update(value)
                continue
            elif key in DEFAULT_ACTION:
                normalized[key] = value
            else:
                normalized[key] = value

        normalized["expression"] = StrategyGenerator._enum_or_default(
            normalized.get("expression"),
            EXPRESSION_VALUES,
            DEFAULT_ACTION["expression"],
        )
        normalized["gaze"] = StrategyGenerator._enum_or_default(
            normalized.get("gaze"),
            GAZE_VALUES,
            DEFAULT_ACTION["gaze"],
        )
        normalized["blink"] = StrategyGenerator._enum_or_default(
            normalized.get("blink"),
            BLINK_VALUES,
            DEFAULT_ACTION["blink"],
        )
        normalized["mouth_sync"] = "short_time_energy"

        normalized["tts_style"] = {
            "speed": StrategyGenerator._number_in_range(
                normalized["tts_style"].get("speed"),
                0.8,
                1.2,
                DEFAULT_ACTION["tts_style"]["speed"],
            ),
            "pitch": StrategyGenerator._number_in_range(
                normalized["tts_style"].get("pitch"),
                -1.0,
                1.0,
                DEFAULT_ACTION["tts_style"]["pitch"],
            ),
            "volume": StrategyGenerator._number_in_range(
                normalized["tts_style"].get("volume"),
                0.5,
                1.0,
                DEFAULT_ACTION["tts_style"]["volume"],
            ),
        }
        normalized["servo_targets_placeholder"] = {
            name: StrategyGenerator._number_in_range(
                normalized["servo_targets_placeholder"].get(name),
                0.0,
                1.0,
                DEFAULT_ACTION["servo_targets_placeholder"][name],
            )
            for name in DEFAULT_ACTION["servo_targets_placeholder"]
        }

        return normalized

    @staticmethod
    def _enum_or_default(value: Any, allowed: set[str], default: str) -> str:
        if isinstance(value, str) and value in allowed:
            return value
        return default

    @staticmethod
    def _number_in_range(value: Any, minimum: float, maximum: float, default: float) -> float:
        if isinstance(value, bool):
            return default
        if not isinstance(value, (int, float)):
            return default
        return max(minimum, min(maximum, float(value)))
