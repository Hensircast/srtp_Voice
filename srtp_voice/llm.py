from __future__ import annotations

import codecs
import json
from time import monotonic
from typing import Any, Callable, Dict, Iterable, Iterator, List

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover - exercised by monkeypatch tests
    requests = None

from .config import AppConfig
from .streaming import TextChunk
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

_ECHO_TRAILING_PUNCTUATION = "?!。！？.,，;；"
_EXPLICIT_REPEAT_PREFIXES = (
    "请重复",
    "请复述",
    "请原样说",
    "请原样重复",
    "请原样返回",
    "请原样输出",
    "复述这句话",
    "重复这句话",
    "原样说",
    "原样返回",
)


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


class _EchoOnlyStreamError(LLMResponseError):
    pass


STREAMING_FALLBACK_REPLY = "抱歉，我没有生成可靠的回答，请换一种说法再试一次。"


def _require_requests():
    if requests is None:
        raise RuntimeError(
            "The requests package is required for Ollama and LM Studio backends. "
            "Install it with: python -m pip install requests"
        )
    return requests


def _iter_ndjson_objects(chunks: Iterable[bytes]) -> Iterator[Dict[str, Any]]:
    """Incrementally decode UTF-8 bytes and parse one JSON object per line."""

    decoder = codecs.getincrementaldecoder("utf-8")()
    buffer = ""
    line_number = 0

    def parse_line(line: str) -> Dict[str, Any] | None:
        nonlocal line_number
        line_number += 1
        stripped = line.strip()
        if line_number == 1:
            stripped = stripped.lstrip("\ufeff")
        if not stripped:
            return None
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise LLMResponseError(
                f"Ollama stream returned invalid NDJSON on line {line_number}."
            ) from exc
        if not isinstance(value, dict):
            raise LLMResponseError(
                f"Ollama stream NDJSON line {line_number} is not an object."
            )
        return value

    try:
        for chunk in chunks:
            if not isinstance(chunk, bytes):
                raise LLMResponseError("Ollama stream yielded a non-bytes response chunk.")
            if not chunk:
                continue
            buffer += decoder.decode(chunk)
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                value = parse_line(line.rstrip("\r"))
                if value is not None:
                    yield value
        buffer += decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        raise LLMResponseError("Ollama stream ended with invalid UTF-8 data.") from exc

    if buffer:
        value = parse_line(buffer.rstrip("\r"))
        if value is not None:
            yield value


def _normalize_echo_text(text: str) -> str:
    normalized = "".join(text.strip().casefold().split())
    return normalized.rstrip(_ECHO_TRAILING_PUNCTUATION)


def _is_explicit_repeat_request(user_text: str) -> bool:
    normalized = "".join(user_text.strip().casefold().split())
    return any(normalized.startswith(prefix) for prefix in _EXPLICIT_REPEAT_PREFIXES)


def _is_echo_only_reply(user_text: str, reply_text: str) -> bool:
    if _is_explicit_repeat_request(user_text):
        return False
    normalized_user = _normalize_echo_text(user_text)
    normalized_reply = _normalize_echo_text(reply_text)
    return bool(normalized_user) and normalized_user == normalized_reply


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

    @staticmethod
    def default_stream_action() -> Dict[str, Any]:
        """Return the deterministic action available before streamed text ends."""

        return StrategyGenerator._normalize_action({})

    def generate_stream(
        self,
        user_text: str,
        emotion: EmotionResult,
        history: List[Dict[str, Any]],
        *,
        turn_id: str = "",
    ) -> Iterator[TextChunk]:
        """Yield reply-text deltas without waiting for structured robot actions.

        Ollama uses its NDJSON ``stream=true`` transport. Other backends retain
        compatibility by yielding their synchronous result as one final chunk.
        A successful Ollama stream ends with an empty ``is_final`` marker so no
        generated text has to be delayed merely to identify the last token.
        """

        backend = self.cfg.llm_backend.lower()
        if backend != "ollama":
            result = self.generate(user_text, emotion, history)
            yield TextChunk(
                text=result.reply_text,
                is_final=True,
                timestamp_ms=int(monotonic() * 1000),
                turn_id=turn_id,
                sequence_id=0,
            )
            return

        sequence = 0
        emitted_text = False

        def emit(parts: Iterable[str]) -> Iterator[TextChunk]:
            nonlocal sequence, emitted_text
            for part in parts:
                if not part:
                    continue
                emitted_text = True
                yield TextChunk(
                    text=part,
                    is_final=False,
                    timestamp_ms=int(monotonic() * 1000),
                    turn_id=turn_id,
                    sequence_id=sequence,
                )
                sequence += 1

        try:
            req = _require_requests()
            if self._uses_standard_ollama_chat_url():
                self._check_ollama()
            messages = self._build_streaming_messages(user_text, emotion, history)
            try:
                parts = self._validated_stream_reply(
                    user_text,
                    self._request_ollama_text_stream(req, messages),
                )
                yield from emit(parts)
            except _EchoOnlyStreamError:
                # The possible echo prefix is held back, so a repair cannot
                # duplicate already emitted text. Repair is collected because
                # correctness matters more than latency on this exceptional path.
                try:
                    repair_parts = list(
                        self._validated_stream_reply(
                            user_text,
                            self._request_ollama_text_stream(
                                req,
                                self._build_echo_repair_messages(user_text, history),
                            ),
                        )
                    )
                except Exception:
                    repair_parts = [STREAMING_FALLBACK_REPLY]
                yield from emit(repair_parts)
        except Exception:
            if emitted_text or not self.cfg.llm_fallback_to_mock:
                raise
            yield from emit([self._mock_generate(user_text, emotion, history).reply_text])

        if not emitted_text:
            raise LLMResponseError("Ollama returned an empty text stream.")
        yield TextChunk(
            text="",
            is_final=True,
            timestamp_ms=int(monotonic() * 1000),
            turn_id=turn_id,
            sequence_id=sequence,
        )

    def _mock_generate(self, user_text: str, emotion: EmotionResult, history: List[Dict[str, Any]]) -> StrategyResult:
        if emotion.label in {
            "angry_or_excited",
            "tired_or_sad",
            "angry",
            "excited",
            "tired",
            "sad",
            "fear",
            "disgust",
        } or any(
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

        messages = self._build_messages(user_text, emotion, history)
        content = self._request_ollama_content(
            req,
            messages,
            structured_output=True,
        )
        first_strategy = self.parse_strategy_content(content)
        return self._repair_echo_only_strategy(
            user_text=user_text,
            history=history,
            first_strategy=first_strategy,
            runtime_name="Ollama",
            request_repair=lambda repair_messages: self._request_ollama_content(
                req,
                repair_messages,
                structured_output=False,
            ),
        )

    def _request_ollama_content(
        self,
        req: Any,
        messages: List[Dict[str, str]],
        *,
        structured_output: bool,
    ) -> str:
        payload = {
            "model": self.cfg.llm_model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self.cfg.llm_temperature,
                "num_predict": self.cfg.llm_max_tokens,
                "num_ctx": self.cfg.llm_context_tokens,
            },
        }
        if structured_output:
            payload["format"] = OLLAMA_STRATEGY_SCHEMA

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
            if structured_output:
                message = "Ollama returned an invalid /api/chat JSON response."
            else:
                message = "Ollama echo repair returned an invalid /api/chat JSON response."
            raise LLMResponseError(message) from exc

        if not isinstance(body, dict):
            if structured_output:
                message = "Ollama returned an invalid /api/chat response."
            else:
                message = "Ollama echo repair returned an invalid /api/chat response."
            raise LLMResponseError(message)

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
            if structured_output:
                error = "Ollama returned an invalid /api/chat response."
            else:
                error = "Ollama echo repair returned an invalid /api/chat response."
            raise LLMResponseError(error)

        return content

    def _request_ollama_text_stream(
        self,
        req: Any,
        messages: List[Dict[str, str]],
    ) -> Iterator[str]:
        payload = {
            "model": self.cfg.llm_model,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": self.cfg.llm_temperature,
                "num_predict": self.cfg.llm_max_tokens,
                "num_ctx": self.cfg.llm_context_tokens,
            },
        }
        try:
            response = req.post(
                self.cfg.llm_ollama_chat_url,
                json=payload,
                timeout=self.cfg.llm_timeout_seconds,
                stream=True,
            )
            response.raise_for_status()
        except req.Timeout as exc:
            raise RuntimeError("Ollama streaming request timed out.") from exc
        except req.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            response_text = exc.response.text[:500] if exc.response is not None else ""
            detail = f" Response body: {response_text}" if response_text else ""
            raise RuntimeError(f"Ollama streaming returned HTTP {status}.{detail}") from exc
        except req.RequestException as exc:
            raise RuntimeError(
                f"Cannot connect to Ollama at {self.cfg.llm_ollama_chat_url}."
            ) from exc

        saw_done = False
        saw_content = False
        try:
            try:
                bodies = _iter_ndjson_objects(response.iter_content(chunk_size=4096))
                for body in bodies:
                    error = body.get("error")
                    if error:
                        raise LLMResponseError(f"Ollama stream error: {error}")
                    message = body.get("message", {})
                    content = message.get("content", "") if isinstance(message, dict) else None
                    if not isinstance(content, str):
                        raise LLMResponseError(
                            "Ollama stream returned non-string message content."
                        )
                    if content:
                        saw_content = True
                        yield content
                    if body.get("done") is True:
                        if body.get("done_reason") == "length":
                            raise LLMResponseError(
                                "Ollama stream was truncated because done_reason=length. "
                                "Increase LLM_MAX_TOKENS."
                            )
                        saw_done = True
                        break
            except req.Timeout as exc:
                raise RuntimeError("Ollama text stream timed out while reading.") from exc
            except req.RequestException as exc:
                raise RuntimeError("Ollama text stream was interrupted.") from exc
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

        if not saw_done:
            raise LLMResponseError("Ollama text stream ended before done=true.")
        if not saw_content:
            raise LLMResponseError("Ollama returned an empty text stream.")

    @staticmethod
    def _validated_stream_reply(
        user_text: str,
        tokens: Iterable[str],
    ) -> Iterator[str]:
        """Hold only a possible echo prefix; release text once it diverges."""

        buffered: List[str] = []
        safe_to_emit = _is_explicit_repeat_request(user_text)
        normalized_user = _normalize_echo_text(user_text)
        for token in tokens:
            buffered.append(token)
            if not safe_to_emit:
                candidate = "".join(buffered)
                normalized_candidate = "".join(candidate.strip().casefold().split())
                without_trailing = normalized_candidate.rstrip(_ECHO_TRAILING_PUNCTUATION)
                safe_to_emit = not (
                    not without_trailing
                    or normalized_user.startswith(without_trailing)
                    or without_trailing == normalized_user
                )
            if safe_to_emit:
                yield "".join(buffered)
                buffered.clear()

        if buffered:
            reply = "".join(buffered)
            if _is_echo_only_reply(user_text, reply):
                raise _EchoOnlyStreamError("Ollama returned an echo-only text stream.")
            if not reply.strip():
                raise LLMResponseError("Ollama returned an empty text stream.")
            yield reply

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
            if self._uses_standard_ollama_chat_url():
                self._check_ollama()
        elif runtime_name == "LM Studio":
            if self._uses_standard_lmstudio_chat_url():
                self._check_lmstudio()

        messages = self._build_messages(user_text, emotion, history)
        content = self._request_local_openai_compatible_content(
            req,
            messages,
            chat_url,
            runtime_name,
            structured_output=True,
        )
        first_strategy = self.parse_strategy_content(content)
        return self._repair_echo_only_strategy(
            user_text=user_text,
            history=history,
            first_strategy=first_strategy,
            runtime_name=runtime_name,
            request_repair=lambda repair_messages: self._request_local_openai_compatible_content(
                req,
                repair_messages,
                chat_url,
                runtime_name,
                structured_output=False,
            ),
        )

    def _request_local_openai_compatible_content(
        self,
        req: Any,
        messages: List[Dict[str, str]],
        chat_url: str,
        runtime_name: str,
        *,
        structured_output: bool,
    ) -> str:
        payload = {
            "model": self.cfg.llm_model,
            "messages": messages,
            "temperature": self.cfg.llm_temperature,
            "max_tokens": self.cfg.llm_max_tokens,
        }
        if structured_output:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "robot_interaction_strategy",
                    "strict": True,
                    "schema": OLLAMA_STRATEGY_SCHEMA,
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
            if structured_output:
                message = f"{runtime_name} returned an invalid chat-completions response."
            else:
                message = f"{runtime_name} echo repair returned an invalid chat-completions response."
            raise LLMResponseError(message) from exc

        if not isinstance(content, str):
            if structured_output:
                message = f"{runtime_name} returned non-string chat content."
            else:
                message = f"{runtime_name} echo repair returned non-string chat content."
            raise LLMResponseError(message)
        return content

    def _repair_echo_only_strategy(
        self,
        user_text: str,
        history: List[Dict[str, Any]],
        first_strategy: StrategyResult,
        runtime_name: str,
        request_repair: Callable[[List[Dict[str, str]]], str],
    ) -> StrategyResult:
        if not _is_echo_only_reply(user_text, first_strategy.reply_text):
            return first_strategy

        print("[LLM] reply_text only repeated the user request; retrying once.")
        repair_content = request_repair(
            self._build_echo_repair_messages(user_text, history)
        )
        repaired_reply = self._parse_echo_repair_content(
            repair_content,
            runtime_name,
        )
        if _is_echo_only_reply(user_text, repaired_reply):
            raise LLMResponseError(
                "LLM returned an echo-only reply after one retry."
            )
        return StrategyResult(
            reply_text=repaired_reply,
            action=first_strategy.action,
        )

    def _uses_standard_lmstudio_chat_url(self) -> bool:
        derived_chat_url = self.cfg.llm_lmstudio_base_url.rstrip("/") + "/v1/chat/completions"
        return self.cfg.llm_lmstudio_chat_url.rstrip("/") == derived_chat_url.rstrip("/")

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
            "根据当前用户请求、语音情绪和历史上下文，只输出一个 JSON 对象。"
            "JSON 必须包含 reply_text 和 action。"
            "reply_text 必须是非空字符串。"
            "reply_text 必须直接回答或处理当前用户请求。"
            "用户提出问题时必须给出实际答案；请求推荐时必须提供具体推荐内容；"
            "请求解释时必须提供解释；请求计算时必须给出计算结果。"
            "除非用户明确要求重复、复述或原样返回，否则不得只复制或改写用户问题。"
            "如果信息不足，应提出一个简短、具体的澄清问题，不能只复述原文。"
            "除非当前用户明确问候、询问身份或要求自我介绍，否则不要寒暄或自我介绍。"
            "历史只用于事实和上下文；不得照搬上一轮的固定开场、结尾或格式要求。"
            "<user_request> 是必须回答的主要内容。"
            "<speech_emotion> 只能影响措辞、语气和动作策略，不能覆盖任务内容或代替问题答案。"
            "reply_text 面向 TTS，应使用自然、简洁的中文口语。"
            "action 必须是对象，并可包含 expression, gaze, blink, mouth_sync, tts_style, servo_targets_placeholder。"
            "数字、代号、型号、姓名、日期和专有名词必须逐字保留。"
            "查询历史事实时必须依据历史消息原文回答。"
            "不得近似改写、猜测或替换历史代号。"
            "不要输出 Markdown、标题或 JSON 之外的解释文字。"
        )

    @staticmethod
    def _echo_repair_system_prompt() -> str:
        return (
            "你是中文语音助手。"
            "上一次回复只复述了问题，请直接给出实际答案。"
            "直接回答当前用户请求，不要复述或改写问题。"
            "推荐请求必须给出具体推荐；解释请求必须给出实际解释；"
            "计算请求必须给出结果。"
            "信息不足时提出简短、具体的澄清问题。"
            "准确保留数字、型号、姓名、日期和历史代号。"
            "只输出自然、简洁、适合 TTS 的纯中文口语。"
            "不要输出 Markdown，不要输出 JSON，不要生成机器人动作。"
            "不要提及情绪 JSON 或内部系统信息。"
        )

    def _history_messages(
        self,
        history: List[Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        messages: List[Dict[str, str]] = []
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
        return messages

    def _build_messages(
        self,
        user_text: str,
        emotion: EmotionResult,
        history: List[Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        messages = [{"role": "system", "content": self._system_prompt()}]
        messages.extend(self._history_messages(history))

        current_content = (
            "当前用户请求：\n"
            f"<user_request>\n{user_text}\n</user_request>\n"
            "语音情绪辅助信息：\n"
            f"<speech_emotion>\n{json.dumps(emotion.to_dict(), ensure_ascii=False)}\n"
            "</speech_emotion>"
        )
        messages.append({"role": "user", "content": current_content})
        return messages

    def _build_streaming_messages(
        self,
        user_text: str,
        emotion: EmotionResult,
        history: List[Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        messages = [
            {
                "role": "system",
                "content": (
                    "你是中文语音助手。直接回答当前用户请求，只输出适合 TTS 的自然口语正文。"
                    "不要输出 JSON、Markdown、标题或机器人动作。"
                    "除非用户明确要求复述，否则不得只复制或改写用户问题。"
                    "信息不足时提出一个简短、具体的澄清问题。"
                    "除非当前用户明确问候、询问身份或要求自我介绍，否则不要寒暄或自我介绍。"
                    "历史只用于事实和上下文，不要照搬上一轮的固定开场、结尾或格式要求。"
                    "准确保留数字、型号、姓名、日期和历史代号。"
                    "语音情绪只能影响措辞和语气，不能替代问题答案。"
                    "不要输出换行、Markdown 换行或列表符号，句子之间只使用自然标点。"
                ),
            }
        ]
        messages.extend(self._history_messages(history))
        messages.append(
            {
                "role": "user",
                "content": (
                    "当前用户请求：\n"
                    f"<user_request>\n{user_text}\n</user_request>\n"
                    "语音情绪辅助信息：\n"
                    f"<speech_emotion>\n{json.dumps(emotion.to_dict(), ensure_ascii=False)}\n"
                    "</speech_emotion>"
                ),
            }
        )
        return messages

    def _build_echo_repair_messages(
        self,
        user_text: str,
        history: List[Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        messages = [
            {
                "role": "system",
                "content": self._echo_repair_system_prompt(),
            }
        ]
        messages.extend(self._history_messages(history))
        messages.append({"role": "user", "content": user_text})
        return messages

    @staticmethod
    def _parse_echo_repair_content(content: str, runtime_name: str) -> str:
        if not isinstance(content, str):
            raise LLMResponseError(
                f"{runtime_name} echo repair response content must be a string."
            )

        repaired_reply = content.strip()
        if not repaired_reply:
            raise LLMResponseError(
                f"{runtime_name} echo repair response content is empty."
            )
        if "```" in repaired_reply:
            raise LLMResponseError(
                f"{runtime_name} echo repair returned Markdown fencing instead of plain text."
            )

        if repaired_reply.startswith(("{", "[")):
            try:
                data = json.loads(repaired_reply)
            except json.JSONDecodeError as exc:
                raise LLMResponseError(
                    f"{runtime_name} echo repair returned invalid JSON-like content."
                ) from exc
            if not isinstance(data, dict):
                raise LLMResponseError(
                    f"{runtime_name} echo repair JSON must be an object with reply_text."
                )
            reply_text = data.get("reply_text")
            if not isinstance(reply_text, str) or not reply_text.strip():
                raise LLMResponseError(
                    f"{runtime_name} echo repair JSON must contain a non-empty reply_text string."
                )
            repaired_reply = reply_text.strip()

        if not any(ch.isalnum() for ch in repaired_reply):
            raise LLMResponseError(
                f"{runtime_name} echo repair response contains no speakable text."
            )
        return repaired_reply

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
