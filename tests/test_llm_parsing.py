from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from srtp_voice.config import AppConfig
from srtp_voice.llm import LLMResponseError, OLLAMA_STRATEGY_SCHEMA, StrategyGenerator
from srtp_voice.memory import JsonMemory
from srtp_voice.types import EmotionResult, PipelineState


def assert_true(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"ok - {name}")


class FakeRequestException(Exception):
    pass


class FakeTimeout(FakeRequestException):
    pass


class FakeHTTPError(FakeRequestException):
    def __init__(self, response=None):
        super().__init__("HTTP error")
        self.response = response


class FakeRequests:
    Timeout = FakeTimeout
    HTTPError = FakeHTTPError
    RequestException = FakeRequestException

    def __init__(self, get_func, post_func):
        self.get = get_func
        self.post = post_func


def _capture_ollama_messages(max_history_turns: int):
    import srtp_voice.llm as llm_module

    class FakeResponse:
        def __init__(self, data):
            self._data = data
            self.status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return self._data

    captured = {}

    def fake_get(url, timeout):
        return FakeResponse({"models": [{"name": "qwen3:4b-instruct"}]})

    def fake_post(url, json, timeout):
        captured["json"] = json
        return FakeResponse({
            "done_reason": "stop",
            "message": {"content": '{"reply_text":"ok","action":{}}'},
        })

    history = [
        {"user_text": "user 1", "reply_text": "assistant 1", "action": {"drop": True}},
        {"user_text": "user 2", "reply_text": "assistant 2", "lip_sync": {"drop": True}},
        {"user_text": "user 3", "reply_text": "assistant 3", "emotion_state": {"drop": True}},
    ]
    old_requests = llm_module.requests
    llm_module.requests = FakeRequests(fake_get, fake_post)
    try:
        cfg = AppConfig(
            llm_backend="ollama",
            llm_model="qwen3:4b-instruct",
            max_history_turns=max_history_turns,
        )
        emotion = EmotionResult(label="neutral", intensity=0.35, confidence=0.5, features={})
        StrategyGenerator(cfg).generate("current question", emotion, history=history)
    finally:
        llm_module.requests = old_requests

    return captured["json"]["messages"]


def test_pure_json() -> None:
    result = StrategyGenerator.parse_strategy_content(
        '{"reply_text":"你好","action":{"expression":"happy"}}'
    )
    assert_true("pure json reply", result.reply_text == "你好")
    assert_true("pure json action", result.action["expression"] == "happy")
    assert_true("pure json default gaze", result.action["gaze"] == "look_at_user")


def test_markdown_json() -> None:
    result = StrategyGenerator.parse_strategy_content(
        '```json\n{"reply_text":"收到","action":{"gaze":"look_left"}}\n```'
    )
    assert_true("markdown json reply", result.reply_text == "收到")
    assert_true("markdown json action default gaze", result.action["gaze"] == "look_at_user")


def test_missing_action() -> None:
    result = StrategyGenerator.parse_strategy_content('{"reply_text":"只有文本"}')
    assert_true("missing action default expression", result.action["expression"] == "neutral_smile")
    assert_true("missing action default servo", "mouth_open" in result.action["servo_targets_placeholder"])


def test_action_semantic_normalization() -> None:
    result = StrategyGenerator.parse_strategy_content(
        json.dumps({
            "reply_text": "ok",
            "action": {
                "expression": "confused_custom",
                "gaze": "diagonal",
                "blink": "No",
                "mouth_sync": "neutral",
                "tts_style": {
                    "speed": 2.0,
                    "pitch": -2.0,
                    "volume": 0.2,
                },
                "servo_targets_placeholder": {
                    "mouth_open": 2.0,
                    "left_eye": -1.0,
                    "right_eye": 0.5,
                    "brow": True,
                },
            },
        })
    )
    assert_true("unknown expression default", result.action["expression"] == "neutral_smile")
    assert_true("unknown gaze default", result.action["gaze"] == "look_at_user")
    assert_true("blink no normalized", result.action["blink"] == "natural")
    assert_true("mouth sync forced", result.action["mouth_sync"] == "short_time_energy")
    assert_true("tts speed clamped", result.action["tts_style"]["speed"] == 1.2)
    assert_true("tts pitch clamped", result.action["tts_style"]["pitch"] == -1.0)
    assert_true("tts volume clamped", result.action["tts_style"]["volume"] == 0.5)
    assert_true("servo high clamped", result.action["servo_targets_placeholder"]["mouth_open"] == 1.0)
    assert_true("servo low clamped", result.action["servo_targets_placeholder"]["left_eye"] == 0.0)
    assert_true("servo normal unchanged", result.action["servo_targets_placeholder"]["right_eye"] == 0.5)
    assert_true("servo bool default", result.action["servo_targets_placeholder"]["brow"] == 0.4)


def test_action_normal_response_unchanged() -> None:
    result = StrategyGenerator.parse_strategy_content(
        json.dumps({
            "reply_text": "ok",
            "action": {
                "expression": "happy",
                "gaze": "center",
                "blink": "slow",
                "mouth_sync": "short_time_energy",
                "tts_style": {
                    "speed": 1.1,
                    "pitch": 0.2,
                    "volume": 0.8,
                },
                "servo_targets_placeholder": {
                    "mouth_open": 0.6,
                    "left_eye": 0.4,
                    "right_eye": 0.4,
                    "brow": 0.7,
                },
            },
        })
    )
    assert_true("valid expression unchanged", result.action["expression"] == "happy")
    assert_true("valid gaze unchanged", result.action["gaze"] == "center")
    assert_true("valid blink unchanged", result.action["blink"] == "slow")
    assert_true("valid mouth sync unchanged", result.action["mouth_sync"] == "short_time_energy")
    assert_true("valid tts unchanged", result.action["tts_style"]["speed"] == 1.1)
    assert_true("valid servo unchanged", result.action["servo_targets_placeholder"]["mouth_open"] == 0.6)


def test_invalid_nested_action_keeps_defaults() -> None:
    for bad_tts in ["fast", [], None, True]:
        result = StrategyGenerator.parse_strategy_content(
            json.dumps({"reply_text": "ok", "action": {"tts_style": bad_tts}})
        )
        assert_true(f"bad tts default speed {bad_tts!r}", result.action["tts_style"]["speed"] == 1.0)
        assert_true(f"bad tts default pitch {bad_tts!r}", result.action["tts_style"]["pitch"] == 0.0)
        assert_true(f"bad tts default volume {bad_tts!r}", result.action["tts_style"]["volume"] == 0.9)

    for bad_servo in [[], "invalid", None, False]:
        result = StrategyGenerator.parse_strategy_content(
            json.dumps({"reply_text": "ok", "action": {"servo_targets_placeholder": bad_servo}})
        )
        assert_true(f"bad servo default mouth {bad_servo!r}", result.action["servo_targets_placeholder"]["mouth_open"] == 0.35)
        assert_true(f"bad servo default left {bad_servo!r}", result.action["servo_targets_placeholder"]["left_eye"] == 0.5)
        assert_true(f"bad servo default right {bad_servo!r}", result.action["servo_targets_placeholder"]["right_eye"] == 0.5)
        assert_true(f"bad servo default brow {bad_servo!r}", result.action["servo_targets_placeholder"]["brow"] == 0.4)


def test_partial_nested_action_merges_and_clamps() -> None:
    result = StrategyGenerator.parse_strategy_content(
        json.dumps({
            "reply_text": "ok",
            "action": {
                "tts_style": {"speed": 1.5},
                "servo_targets_placeholder": {"mouth_open": -1.0, "brow": 2.0},
            },
        })
    )
    assert_true("partial tts speed clamps", result.action["tts_style"]["speed"] == 1.2)
    assert_true("partial tts pitch default", result.action["tts_style"]["pitch"] == 0.0)
    assert_true("partial tts volume default", result.action["tts_style"]["volume"] == 0.9)
    assert_true("partial servo mouth clamps", result.action["servo_targets_placeholder"]["mouth_open"] == 0.0)
    assert_true("partial servo left default", result.action["servo_targets_placeholder"]["left_eye"] == 0.5)
    assert_true("partial servo brow clamps", result.action["servo_targets_placeholder"]["brow"] == 1.0)


def test_invalid_json() -> None:
    try:
        StrategyGenerator.parse_strategy_content("not json")
    except LLMResponseError:
        print("ok - invalid json raises")
        return
    raise AssertionError("invalid json should raise")


def test_mock_backend() -> None:
    cfg = AppConfig(llm_backend="mock")
    emotion = EmotionResult(label="neutral", intensity=0.35, confidence=0.5, features={})
    result = StrategyGenerator(cfg).generate("测试", emotion, history=[])
    assert_true("mock reply", bool(result.reply_text))
    assert_true("mock action", isinstance(result.action, dict))


def test_mock_backend_without_requests() -> None:
    import srtp_voice.llm as llm_module

    old_requests = llm_module.requests
    llm_module.requests = None
    try:
        cfg = AppConfig(llm_backend="mock")
        emotion = EmotionResult(label="neutral", intensity=0.35, confidence=0.5, features={})
        result = StrategyGenerator(cfg).generate("test", emotion, history=[])
    finally:
        llm_module.requests = old_requests

    assert_true("mock without requests reply", bool(result.reply_text))
    assert_true("mock without requests action", isinstance(result.action, dict))


def test_parse_without_requests() -> None:
    import srtp_voice.llm as llm_module

    old_requests = llm_module.requests
    llm_module.requests = None
    try:
        result = StrategyGenerator.parse_strategy_content('{"reply_text":"ok","action":{}}')
    finally:
        llm_module.requests = old_requests

    assert_true("parse without requests", result.reply_text == "ok")


def test_ollama_without_requests_error() -> None:
    import srtp_voice.llm as llm_module

    old_requests = llm_module.requests
    llm_module.requests = None
    try:
        cfg = AppConfig(llm_backend="ollama", llm_fallback_to_mock=False)
        emotion = EmotionResult(label="neutral", intensity=0.35, confidence=0.5, features={})
        try:
            StrategyGenerator(cfg).generate("test", emotion, history=[])
        except RuntimeError as exc:
            message = str(exc)
            assert_true("missing requests names package", "requests package is required" in message)
            assert_true("missing requests install command", "python -m pip install requests" in message)
            return
    finally:
        llm_module.requests = old_requests

    raise AssertionError("missing requests should raise")


def test_ollama_without_requests_fallback() -> None:
    import srtp_voice.llm as llm_module

    old_requests = llm_module.requests
    llm_module.requests = None
    try:
        cfg = AppConfig(llm_backend="ollama", llm_fallback_to_mock=True)
        emotion = EmotionResult(label="neutral", intensity=0.35, confidence=0.5, features={})
        result = StrategyGenerator(cfg).generate("test", emotion, history=[])
    finally:
        llm_module.requests = old_requests

    assert_true("missing requests fallback reply", bool(result.reply_text))


def test_default_model_name() -> None:
    cfg = AppConfig()
    assert_true("default model qwen instruct", cfg.llm_model == "qwen3:4b-instruct")
    assert_true("default max history", cfg.max_history_turns == 3)
    assert_true("default max tokens", cfg.llm_max_tokens == 512)
    assert_true("default context tokens", cfg.llm_context_tokens == 8192)


def test_ollama_native_payload() -> None:
    import srtp_voice.llm as llm_module

    class FakeResponse:
        def __init__(self, data):
            self._data = data
            self.status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return self._data

    captured = {}

    def fake_get(url, timeout):
        return FakeResponse({"models": [{"name": "qwen3:4b-instruct"}]})

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse({
            "done_reason": "stop",
            "eval_count": 12,
            "message": {
                "content": '{"reply_text":"ok","action":{"expression":"neutral_smile"}}'
            }
        })

    old_requests = llm_module.requests
    llm_module.requests = FakeRequests(fake_get, fake_post)
    try:
        cfg = AppConfig(
            llm_backend="ollama",
            llm_model="qwen3:4b-instruct",
            llm_ollama_chat_url="http://localhost:11434/api/chat",
            llm_temperature=0.0,
            llm_max_tokens=777,
            llm_context_tokens=9000,
            llm_timeout_seconds=123,
            max_history_turns=2,
        )
        emotion = EmotionResult(label="neutral", intensity=0.35, confidence=0.5, features={})
        history = [
            {
                "user_text": "old user",
                "reply_text": "old reply",
                "action": {"expression": "old"},
                "lip_sync": [{"mouth_open": 1}],
            },
            {
                "user_text": "recent user 1",
                "reply_text": "recent reply 1",
                "action": {"expression": "drop me"},
                "servo_targets_placeholder": {"mouth_open": 1},
                "emotion_state": {"label": "drop me"},
            },
            {
                "user_text": "recent user 2",
                "reply_text": "recent reply 2",
                "dialogue_stage": "drop me",
                "serial_packet": {"drop": True},
            },
        ]
        result = StrategyGenerator(cfg).generate("test", emotion, history=history)
    finally:
        llm_module.requests = old_requests

    assert_true("ollama native url", captured["url"].endswith("/api/chat"))
    assert_true("ollama model", captured["json"]["model"] == "qwen3:4b-instruct")
    assert_true("ollama stream false", captured["json"]["stream"] is False)
    assert_true("ollama no think field", "think" not in captured["json"])
    assert_true("ollama stream bool", isinstance(captured["json"]["stream"], bool))
    assert_true("ollama format schema", isinstance(captured["json"]["format"], dict))
    assert_true("ollama format reuses schema", captured["json"]["format"] == OLLAMA_STRATEGY_SCHEMA)
    assert_true("ollama no response format", "response_format" not in captured["json"])
    assert_true("ollama schema requires reply", "reply_text" in captured["json"]["format"]["required"])
    action_schema = captured["json"]["format"]["properties"]["action"]["properties"]
    assert_true("schema mouth sync const", action_schema["mouth_sync"]["const"] == "short_time_energy")
    assert_true("schema blink enum", "natural" in action_schema["blink"]["enum"])
    assert_true("schema servo range", action_schema["servo_targets_placeholder"]["properties"]["mouth_open"]["maximum"] == 1.0)
    assert_true("ollama temperature zero", captured["json"]["options"]["temperature"] == 0)
    assert_true("ollama num_predict from config", captured["json"]["options"]["num_predict"] == 777)
    assert_true("ollama num_ctx from config", captured["json"]["options"]["num_ctx"] == 9000)
    assert_true("ollama timeout from config", captured["timeout"] == 123)
    messages = captured["json"]["messages"]
    assert_true("history max turns", len(messages) == 6)
    assert_true("history first recent turn", messages[1]["content"] == "recent user 1")
    assert_true("history role order user", messages[1]["role"] == "user")
    assert_true("history role order assistant", messages[2]["role"] == "assistant")
    assert_true("current user last", messages[-1]["role"] == "user" and "test" in messages[-1]["content"])
    dialogue_text = json.dumps(messages[1:], ensure_ascii=False)
    assert_true("history no action", "action" not in dialogue_text)
    assert_true("ollama response parse", result.reply_text == "ok")


def test_history_messages_preserve_codes() -> None:
    import srtp_voice.llm as llm_module

    class FakeResponse:
        def __init__(self, data):
            self._data = data
            self.status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return self._data

    captured = {}

    def fake_get(url, timeout):
        return FakeResponse({"models": [{"name": "qwen3:4b-instruct"}]})

    def fake_post(url, json, timeout):
        captured["json"] = json
        return FakeResponse({
            "done_reason": "stop",
            "message": {"content": '{"reply_text":"ok","action":{}}'},
        })

    old_requests = llm_module.requests
    llm_module.requests = FakeRequests(fake_get, fake_post)
    try:
        cfg = AppConfig(
            llm_backend="ollama",
            llm_model="qwen3:4b-instruct",
            max_history_turns=2,
        )
        emotion = EmotionResult(label="neutral", intensity=0.35, confidence=0.5, features={})
        history = [
            {
                "user_text": "第一轮测试代号是 ZXQ-407-A9。",
                "reply_text": "我会记住测试代号 ZXQ-407-A9。",
                "action": {"expression": "happy"},
                "emotion_state": {"label": "neutral"},
                "dialogue_stage": "Speaking",
                "lip_sync": {"frames": []},
                "servo_targets_placeholder": {"mouth_open": 1.0},
            },
            {
                "user_text": "请确认刚才的测试代号。",
                "reply_text": "刚才的测试代号是 ZXQ-407-A9。",
                "action": {"expression": "neutral"},
            },
        ]
        StrategyGenerator(cfg).generate("历史里的测试代号是什么？", emotion, history=history)
    finally:
        llm_module.requests = old_requests

    messages = captured["json"]["messages"]
    prompt_text = json.dumps(messages, ensure_ascii=False)
    dialogue_text = json.dumps(messages[1:], ensure_ascii=False)
    roles = [message["role"] for message in messages]
    assert_true("history code preserved", "ZXQ-407-A9" in prompt_text)
    assert_true("history code not rewritten", "ZXQ-300" not in prompt_text)
    assert_true("history role sequence", roles[:5] == ["system", "user", "assistant", "user", "assistant"])
    assert_true("current question last user", messages[-1]["role"] == "user" and "历史里的测试代号是什么？" in messages[-1]["content"])
    assert_true("action not in dialogue prompt", "action" not in dialogue_text)
    assert_true("lip sync not in dialogue prompt", "lip_sync" not in dialogue_text)


def test_history_limit_zero_disables_llm_history() -> None:
    messages = _capture_ollama_messages(0)
    assert_true("zero history message count", len(messages) == 2)
    assert_true("zero history system first", messages[0]["role"] == "system")
    assert_true("zero history current user last", messages[-1]["role"] == "user" and "current question" in messages[-1]["content"])
    dialogue_text = json.dumps(messages[1:], ensure_ascii=False)
    assert_true("zero history no old user", "user 1" not in dialogue_text and "user 3" not in dialogue_text)


def test_history_limit_negative_disables_llm_history() -> None:
    messages = _capture_ollama_messages(-1)
    assert_true("negative history message count", len(messages) == 2)
    assert_true("negative history current user last", messages[-1]["role"] == "user" and "current question" in messages[-1]["content"])
    dialogue_text = json.dumps(messages[1:], ensure_ascii=False)
    assert_true("negative history no old user", "user 1" not in dialogue_text and "user 3" not in dialogue_text)


def test_history_limit_one_keeps_latest_turn() -> None:
    messages = _capture_ollama_messages(1)
    assert_true("one history message count", len(messages) == 4)
    assert_true("one history latest user", messages[1]["role"] == "user" and messages[1]["content"] == "user 3")
    assert_true("one history latest assistant", messages[2]["role"] == "assistant" and messages[2]["content"] == "assistant 3")
    assert_true("one history current user last", messages[-1]["role"] == "user" and "current question" in messages[-1]["content"])
    dialogue_text = json.dumps(messages[1:], ensure_ascii=False)
    assert_true("one history excludes older", "user 1" not in dialogue_text and "user 2" not in dialogue_text)
    assert_true("one history no action fields", "action" not in dialogue_text and "lip_sync" not in dialogue_text)


def test_history_limit_two_keeps_latest_two_turns() -> None:
    messages = _capture_ollama_messages(2)
    roles = [message["role"] for message in messages]
    assert_true("two history message count", len(messages) == 6)
    assert_true("two history roles", roles == ["system", "user", "assistant", "user", "assistant", "user"])
    assert_true("two history first selected", messages[1]["content"] == "user 2")
    assert_true("two history second selected", messages[3]["content"] == "user 3")
    assert_true("two history current user last", "current question" in messages[-1]["content"])
    dialogue_text = json.dumps(messages[1:], ensure_ascii=False)
    assert_true("two history excludes oldest", "user 1" not in dialogue_text)
    assert_true("two history no pipeline fields", "action" not in dialogue_text and "lip_sync" not in dialogue_text and "emotion_state" not in dialogue_text)


def _sample_pipeline_state(text: str) -> PipelineState:
    return PipelineState(
        user_audio="outputs/user_input.wav",
        user_text=text,
        emotion=EmotionResult(label="neutral", intensity=0.35, confidence=0.5, features={}),
        reply_text=f"reply {text}",
        action={},
        reply_audio="outputs/reply.wav",
    )


def test_json_memory_zero_disables_load_and_append() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "memory.json"
        path.write_text(json.dumps([{"user_text": "old"}]), encoding="utf-8")
        memory = JsonMemory(path, max_turns=0)
        assert_true("memory zero load empty", memory.load() == [])
        memory.append(_sample_pipeline_state("new"))
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert_true("memory zero append does not accumulate", saved == [{"user_text": "old"}])


def test_json_memory_negative_disables_load_and_append() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "memory.json"
        path.write_text(json.dumps([{"user_text": "old"}]), encoding="utf-8")
        memory = JsonMemory(path, max_turns=-1)
        assert_true("memory negative load empty", memory.load() == [])
        memory.append(_sample_pipeline_state("new"))
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert_true("memory negative append does not accumulate", saved == [{"user_text": "old"}])


def test_json_memory_positive_limit_keeps_recent() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "memory.json"
        memory = JsonMemory(path, max_turns=2)
        memory.append(_sample_pipeline_state("one"))
        memory.append(_sample_pipeline_state("two"))
        memory.append(_sample_pipeline_state("three"))
        loaded = memory.load()
        assert_true("memory positive length", len(loaded) == 2)
        assert_true("memory positive first recent", loaded[0]["user_text"] == "two")
        assert_true("memory positive second recent", loaded[1]["user_text"] == "three")


def test_env_bool_parsing() -> None:
    saved = {name: os.environ.get(name) for name in [
        "LLM_TEMPERATURE",
        "LLM_MAX_TOKENS",
        "LLM_CONTEXT_TOKENS",
        "LLM_TIMEOUT_SECONDS",
        "MAX_HISTORY_TURNS",
    ]}
    try:
        os.environ["LLM_TEMPERATURE"] = "0.25"
        os.environ["LLM_MAX_TOKENS"] = "999"
        os.environ["LLM_CONTEXT_TOKENS"] = "12345"
        os.environ["LLM_TIMEOUT_SECONDS"] = "88"
        os.environ["MAX_HISTORY_TURNS"] = "4"
        cfg = AppConfig.from_env()
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    assert_true("env temperature", cfg.llm_temperature == 0.25)
    assert_true("env max tokens", cfg.llm_max_tokens == 999)
    assert_true("env context tokens", cfg.llm_context_tokens == 12345)
    assert_true("env timeout", cfg.llm_timeout_seconds == 88)
    assert_true("env max history", cfg.max_history_turns == 4)


def test_ollama_done_reason_length() -> None:
    import srtp_voice.llm as llm_module

    class FakeResponse:
        def __init__(self, data):
            self._data = data
            self.status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return self._data

    def fake_get(url, timeout):
        return FakeResponse({"models": [{"name": "qwen3:4b-instruct"}]})

    def fake_post(url, json, timeout):
        return FakeResponse({
            "done_reason": "length",
            "eval_count": 1024,
            "thinking": "",
            "message": {"content": '{"reply_text":"truncated"'},
        })

    old_requests = llm_module.requests
    llm_module.requests = FakeRequests(fake_get, fake_post)
    try:
        cfg = AppConfig(llm_backend="ollama", llm_model="qwen3:4b-instruct")
        emotion = EmotionResult(label="neutral", intensity=0.35, confidence=0.5, features={})
        try:
            StrategyGenerator(cfg).generate("test", emotion, history=[])
        except LLMResponseError as exc:
            assert_true("length error message", "done_reason=length" in str(exc))
            assert_true("length error suggests max tokens", "LLM_MAX_TOKENS" in str(exc))
            return
    finally:
        llm_module.requests = old_requests

    raise AssertionError("done_reason=length should raise")


def test_ollama_http_error_includes_body() -> None:
    import srtp_voice.llm as llm_module

    class FakeResponse:
        def __init__(self, data=None, text="", status_code=200, raise_http=False):
            self._data = data if data is not None else {}
            self.text = text
            self.status_code = status_code
            self._raise_http = raise_http

        def raise_for_status(self):
            if self._raise_http:
                raise FakeHTTPError(response=self)
            return None

        def json(self):
            return self._data

    def fake_get(url, timeout):
        return FakeResponse({"models": [{"name": "qwen3:4b-instruct"}]})

    def fake_post(url, json, timeout):
        return FakeResponse(
            text="unsupported thinking field" + ("x" * 600),
            status_code=400,
            raise_http=True,
        )

    old_requests = llm_module.requests
    llm_module.requests = FakeRequests(fake_get, fake_post)
    try:
        cfg = AppConfig(llm_backend="ollama", llm_model="qwen3:4b-instruct")
        emotion = EmotionResult(label="neutral", intensity=0.35, confidence=0.5, features={})
        try:
            StrategyGenerator(cfg).generate("test", emotion, history=[])
        except RuntimeError as exc:
            message = str(exc)
            assert_true("http error status", "HTTP 400" in message)
            assert_true("http error body", "unsupported thinking field" in message)
            assert_true("http error body clipped", len(message) < 650)
            return
    finally:
        llm_module.requests = old_requests

    raise AssertionError("Ollama HTTP error should raise")


def test_ollama_context_error_message() -> None:
    import srtp_voice.llm as llm_module

    class FakeResponse:
        def __init__(self, data=None, text="", status_code=200, raise_http=False):
            self._data = data if data is not None else {}
            self.text = text
            self.status_code = status_code
            self._raise_http = raise_http

        def raise_for_status(self):
            if self._raise_http:
                raise FakeHTTPError(response=self)
            return None

        def json(self):
            return self._data

    def fake_get(url, timeout):
        return FakeResponse({"models": [{"name": "qwen3:4b-instruct"}]})

    def fake_post(url, json, timeout):
        return FakeResponse(
            text='{"error":"{\\"error\\":{\\"code\\":400,\\"message\\":\\"request (5938 tokens) exceeds the available context size (4096 tokens)\\",\\"type\\":\\"exceed_context_size_error\\",\\"n_prompt_tokens\\":5938,\\"n_ctx\\":4096}}"}',
            status_code=400,
            raise_http=True,
        )

    old_requests = llm_module.requests
    llm_module.requests = FakeRequests(fake_get, fake_post)
    try:
        cfg = AppConfig(
            llm_backend="ollama",
            llm_model="qwen3:4b-instruct",
            llm_fallback_to_mock=False,
        )
        emotion = EmotionResult(label="neutral", intensity=0.35, confidence=0.5, features={})
        try:
            StrategyGenerator(cfg).generate("test", emotion, history=[])
        except RuntimeError as exc:
            message = str(exc)
            assert_true("context request tokens", "request_tokens=5938" in message)
            assert_true("context size", "context_size=4096" in message)
            assert_true("context advice", "LLM_CONTEXT_TOKENS" in message and "reduce history" in message)
            return
    finally:
        llm_module.requests = old_requests

    raise AssertionError("context size error should raise")


def test_lmstudio_timeout_from_config() -> None:
    result, captured = _generate_lmstudio_with_temperature(0.4, 321)

    assert_true("lmstudio url", captured["url"].endswith("/v1/chat/completions"))
    assert_true("lmstudio timeout from config", captured["timeout"] == 321)
    assert_true("lmstudio response parse", result.reply_text == "ok")


def _generate_lmstudio_with_temperature(temperature: float, timeout_seconds: int):
    import srtp_voice.llm as llm_module

    class FakeResponse:
        def __init__(self, data):
            self._data = data
            self.status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return self._data

    captured = {}

    def fake_get(url, timeout):
        return FakeResponse({"data": [{"id": "local-model"}]})

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse({
            "choices": [
                {"message": {"content": '{"reply_text":"ok","action":{}}'}}
            ]
        })

    old_requests = llm_module.requests
    llm_module.requests = FakeRequests(fake_get, fake_post)
    try:
        cfg = AppConfig(
            llm_backend="lmstudio",
            llm_model="local-model",
            llm_lmstudio_base_url="http://localhost:1234",
            llm_lmstudio_chat_url="http://localhost:1234/v1/chat/completions",
            llm_temperature=temperature,
            llm_timeout_seconds=timeout_seconds,
        )
        emotion = EmotionResult(label="neutral", intensity=0.35, confidence=0.5, features={})
        result = StrategyGenerator(cfg).generate("test", emotion, history=[])
    finally:
        llm_module.requests = old_requests

    return result, captured


def test_lmstudio_temperature_from_config() -> None:
    for temperature in [0.0, 0.25]:
        result, captured = _generate_lmstudio_with_temperature(temperature, 456)
        assert_true(f"lmstudio temperature {temperature}", captured["json"]["temperature"] == temperature)
        assert_true(f"lmstudio timeout with temperature {temperature}", captured["timeout"] == 456)
        assert_true(f"lmstudio parsed with temperature {temperature}", result.reply_text == "ok")


def test_lmstudio_response_format_schema() -> None:
    result, captured = _generate_lmstudio_with_temperature(0.25, 456)
    payload = captured["json"]
    response_format = payload["response_format"]
    json_schema = response_format["json_schema"]
    schema = json_schema["schema"]
    action_schema = schema["properties"]["action"]
    action_properties = action_schema["properties"]

    assert_true("lmstudio response format type", response_format["type"] == "json_schema")
    assert_true("lmstudio schema name", isinstance(json_schema["name"], str) and bool(json_schema["name"]))
    assert_true("lmstudio schema strict", json_schema["strict"] is True)
    assert_true("lmstudio schema reused", schema == OLLAMA_STRATEGY_SCHEMA)
    assert_true("lmstudio schema requires reply", "reply_text" in schema["required"])
    assert_true("lmstudio schema requires action", "action" in schema["required"])
    for field in [
        "expression",
        "gaze",
        "blink",
        "mouth_sync",
        "tts_style",
        "servo_targets_placeholder",
    ]:
        assert_true(f"lmstudio action schema has {field}", field in action_properties)
    assert_true("lmstudio mouth sync const", action_properties["mouth_sync"]["const"] == "short_time_energy")
    assert_true("lmstudio root closed", schema["additionalProperties"] is False)
    assert_true("lmstudio action closed", action_schema["additionalProperties"] is False)
    assert_true("lmstudio tts closed", action_properties["tts_style"]["additionalProperties"] is False)
    assert_true(
        "lmstudio servo closed",
        action_properties["servo_targets_placeholder"]["additionalProperties"] is False,
    )
    assert_true("lmstudio schema temperature", payload["temperature"] == 0.25)
    assert_true("lmstudio schema timeout", captured["timeout"] == 456)
    assert_true("lmstudio messages system", payload["messages"][0]["role"] == "system")
    assert_true("lmstudio messages current user last", payload["messages"][-1]["role"] == "user")
    assert_true("lmstudio schema response parse", result.reply_text == "ok")


def test_mock_distress_expression_for_emotion_labels() -> None:
    for label in ["angry_or_excited", "tired_or_sad"]:
        cfg = AppConfig(llm_backend="mock")
        emotion = EmotionResult(label=label, intensity=0.75, confidence=0.8, features={})
        result = StrategyGenerator(cfg).generate("test", emotion, history=[])
        assert_true(f"mock distress expression for {label}", result.action["expression"] == "concerned")


def test_mock_distress_expression_for_keywords() -> None:
    cfg = AppConfig(llm_backend="mock")
    emotion = EmotionResult(label="neutral", intensity=0.35, confidence=0.5, features={})
    for text in ["不会", "做不下去", "崩", "烦", "困难"]:
        result = StrategyGenerator(cfg).generate(text, emotion, history=[])
        assert_true(f"mock distress expression for {text}", result.action["expression"] == "concerned")


def test_mock_normal_input_keeps_default_expression() -> None:
    cfg = AppConfig(llm_backend="mock")
    emotion = EmotionResult(label="neutral", intensity=0.35, confidence=0.5, features={})
    result = StrategyGenerator(cfg).generate("test", emotion, history=[])
    assert_true("mock normal expression default", result.action["expression"] == "neutral_smile")


def test_lmstudio_fallback_to_mock_distress_expression() -> None:
    import srtp_voice.llm as llm_module

    def fake_get(url, timeout):
        raise FakeRequestException("offline")

    def fake_post(url, json, timeout):
        raise AssertionError("LM Studio chat should not be called when model check fails")

    old_requests = llm_module.requests
    llm_module.requests = FakeRequests(fake_get, fake_post)
    try:
        cfg = AppConfig(
            llm_backend="lmstudio",
            llm_model="local-model",
            llm_fallback_to_mock=True,
        )
        emotion = EmotionResult(label="neutral", intensity=0.35, confidence=0.5, features={})
        result = StrategyGenerator(cfg).generate("不会", emotion, history=[])
    finally:
        llm_module.requests = old_requests

    assert_true("lmstudio fallback mock distress expression", result.action["expression"] == "concerned")


def main() -> None:
    test_pure_json()
    test_markdown_json()
    test_missing_action()
    test_action_semantic_normalization()
    test_action_normal_response_unchanged()
    test_invalid_nested_action_keeps_defaults()
    test_partial_nested_action_merges_and_clamps()
    test_invalid_json()
    test_mock_backend()
    test_mock_backend_without_requests()
    test_parse_without_requests()
    test_ollama_without_requests_error()
    test_ollama_without_requests_fallback()
    test_default_model_name()
    test_ollama_native_payload()
    test_history_messages_preserve_codes()
    test_history_limit_zero_disables_llm_history()
    test_history_limit_negative_disables_llm_history()
    test_history_limit_one_keeps_latest_turn()
    test_history_limit_two_keeps_latest_two_turns()
    test_json_memory_zero_disables_load_and_append()
    test_json_memory_negative_disables_load_and_append()
    test_json_memory_positive_limit_keeps_recent()
    test_env_bool_parsing()
    test_ollama_done_reason_length()
    test_ollama_http_error_includes_body()
    test_ollama_context_error_message()
    test_lmstudio_timeout_from_config()
    test_lmstudio_temperature_from_config()
    test_lmstudio_response_format_schema()
    test_mock_distress_expression_for_emotion_labels()
    test_mock_distress_expression_for_keywords()
    test_mock_normal_input_keeps_default_expression()
    test_lmstudio_fallback_to_mock_distress_expression()


if __name__ == "__main__":
    main()
