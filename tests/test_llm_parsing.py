from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from srtp_voice.config import AppConfig
from srtp_voice.llm import LLMResponseError, StrategyGenerator
from srtp_voice.types import EmotionResult


def assert_true(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"ok - {name}")


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
    assert_true("markdown json action", result.action["gaze"] == "look_left")


def test_missing_action() -> None:
    result = StrategyGenerator.parse_strategy_content('{"reply_text":"只有文本"}')
    assert_true("missing action default expression", result.action["expression"] == "neutral_smile")
    assert_true("missing action default servo", "mouth_open" in result.action["servo_targets_placeholder"])


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
        return FakeResponse({"models": [{"name": "qwen3:4b"}]})

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

    old_get = llm_module.requests.get
    old_post = llm_module.requests.post
    llm_module.requests.get = fake_get
    llm_module.requests.post = fake_post
    try:
        cfg = AppConfig(
            llm_backend="ollama",
            llm_model="qwen3:4b",
            llm_ollama_chat_url="http://localhost:11434/api/chat",
            llm_think=False,
            llm_stream=False,
            llm_temperature=0.0,
            llm_max_tokens=777,
            llm_timeout_seconds=123,
        )
        emotion = EmotionResult(label="neutral", intensity=0.35, confidence=0.5, features={})
        result = StrategyGenerator(cfg).generate("test", emotion, history=[])
    finally:
        llm_module.requests.get = old_get
        llm_module.requests.post = old_post

    assert_true("ollama native url", captured["url"].endswith("/api/chat"))
    assert_true("ollama model", captured["json"]["model"] == "qwen3:4b")
    assert_true("ollama stream false", captured["json"]["stream"] is False)
    assert_true("ollama think false", captured["json"]["think"] is False)
    assert_true("ollama stream bool", isinstance(captured["json"]["stream"], bool))
    assert_true("ollama think bool", isinstance(captured["json"]["think"], bool))
    assert_true("ollama format schema", isinstance(captured["json"]["format"], dict))
    assert_true("ollama schema requires reply", "reply_text" in captured["json"]["format"]["required"])
    assert_true("ollama temperature zero", captured["json"]["options"]["temperature"] == 0)
    assert_true("ollama num_predict from config", captured["json"]["options"]["num_predict"] == 777)
    assert_true("ollama timeout from config", captured["timeout"] == 123)
    assert_true("ollama response parse", result.reply_text == "ok")


def test_env_bool_parsing() -> None:
    saved = {name: os.environ.get(name) for name in [
        "LLM_THINK",
        "LLM_STREAM",
        "LLM_TEMPERATURE",
        "LLM_MAX_TOKENS",
        "LLM_TIMEOUT_SECONDS",
    ]}
    try:
        os.environ["LLM_THINK"] = "false"
        os.environ["LLM_STREAM"] = "0"
        os.environ["LLM_TEMPERATURE"] = "0.25"
        os.environ["LLM_MAX_TOKENS"] = "999"
        os.environ["LLM_TIMEOUT_SECONDS"] = "88"
        cfg = AppConfig.from_env()
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    assert_true("env think false", cfg.llm_think is False)
    assert_true("env stream false", cfg.llm_stream is False)
    assert_true("env temperature", cfg.llm_temperature == 0.25)
    assert_true("env max tokens", cfg.llm_max_tokens == 999)
    assert_true("env timeout", cfg.llm_timeout_seconds == 88)


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
        return FakeResponse({"models": [{"name": "qwen3:4b"}]})

    def fake_post(url, json, timeout):
        return FakeResponse({
            "done_reason": "length",
            "eval_count": 1024,
            "thinking": "",
            "message": {"content": '{"reply_text":"truncated"'},
        })

    old_get = llm_module.requests.get
    old_post = llm_module.requests.post
    llm_module.requests.get = fake_get
    llm_module.requests.post = fake_post
    try:
        cfg = AppConfig(llm_backend="ollama", llm_model="qwen3:4b")
        emotion = EmotionResult(label="neutral", intensity=0.35, confidence=0.5, features={})
        try:
            StrategyGenerator(cfg).generate("test", emotion, history=[])
        except LLMResponseError as exc:
            assert_true("length error message", "done_reason=length" in str(exc))
            assert_true("length error suggests max tokens", "LLM_MAX_TOKENS" in str(exc))
            return
    finally:
        llm_module.requests.get = old_get
        llm_module.requests.post = old_post

    raise AssertionError("done_reason=length should raise")


def main() -> None:
    test_pure_json()
    test_markdown_json()
    test_missing_action()
    test_invalid_json()
    test_mock_backend()
    test_ollama_native_payload()
    test_env_bool_parsing()
    test_ollama_done_reason_length()


if __name__ == "__main__":
    main()
