"""Low-latency Ollama streaming reader tests (no model, no user data)."""

from __future__ import annotations

import json
import threading
from typing import Any

from srtp_voice import llm as llm_module
from srtp_voice.config import AppConfig
from srtp_voice.llm import StrategyGenerator
from srtp_voice.types import EmotionResult

FIRST = (json.dumps({"message": {"content": "可以。"}, "done": False}) + "\n").encode()
SECOND = (json.dumps({"message": {"content": ""}, "done": True}) + "\n").encode()


class _FakeResponse:
    def __init__(self, chunks, *, on_first=None) -> None:
        self._chunks = list(chunks)
        self._on_first = on_first
        self.chunk_sizes: list[Any] = []
        self.closed = False

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size=None):
        self.chunk_sizes.append(chunk_size)
        for index, chunk in enumerate(self._chunks):
            if index == 1 and self._on_first is not None:
                self._on_first()
            yield chunk

    def close(self) -> None:
        self.closed = True


class _FakeRequests:
    Timeout = RuntimeError
    RequestException = RuntimeError

    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[dict] = []
        self.get_calls = 0

    def post(self, url, **kwargs):
        self.calls.append({"url": url, "kwargs": kwargs})
        return self.response

    def get(self, url, **kwargs):
        # The default Ollama URL triggers a catalogue check; answer it locally
        # so the test never touches a real service.
        self.get_calls += 1
        return _Tags()


class _Tags:
    def raise_for_status(self) -> None:
        return None

    def json(self):
        return {"models": [{"name": "qwen3:4b-instruct"}]}


def test_first_token_arrives_before_done_is_sent(monkeypatch) -> None:
    release_done = threading.Event()
    first_received = threading.Event()
    values: list[str] = []
    errors: list[BaseException] = []

    response = _FakeResponse([FIRST, SECOND], on_first=release_done.wait)
    fake_requests = _FakeRequests(response)
    monkeypatch.setattr(llm_module, "requests", fake_requests)

    generator = StrategyGenerator(
        AppConfig(llm_backend="ollama", llm_timeout_seconds=3)
    )
    stream = generator.generate_stream(
        "有什么建议？", EmotionResult("neutral", 0.2, 0.8, {}), []
    )

    def consume() -> None:
        try:
            values.append(next(stream).text)
            first_received.set()
        except BaseException as error:  # noqa: BLE001 - asserted below
            errors.append(error)

    consumer = threading.Thread(target=consume)
    consumer.start()
    try:
        assert first_received.wait(1), "the reader waited for the whole response"
        assert values == ["可以。"]
        assert errors == []
    finally:
        release_done.set()
        consumer.join(4)
        stream.close()

    assert not consumer.is_alive()
    assert response.chunk_sizes and response.chunk_sizes[0] == 1
    assert response.closed is True
    assert fake_requests.calls[0]["kwargs"]["stream"] is True


def test_streaming_prompt_asks_for_face_to_face_short_replies() -> None:
    generator = StrategyGenerator(AppConfig())
    messages = generator._build_streaming_messages(
        "有什么建议？", EmotionResult("neutral", 0.2, 0.8, {}), []
    )

    system = messages[0]["content"]
    assert "第一句先给核心回答" in system
    assert "2-4 个短句" in system
    assert "面对面" in system
    assert "temperature" not in system


def test_done_only_stream_is_reported_not_cached() -> None:
    class _Empty:
        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size=None):
            yield (json.dumps({"message": {"content": ""}, "done": True}) + "\n").encode()

        def close(self) -> None:
            return None

    generator = StrategyGenerator(AppConfig(llm_backend="ollama"))
    from srtp_voice import llm as module

    original = module.requests
    module.requests = _FakeRequests(_Empty())  # type: ignore[assignment]
    try:
        stream = generator.generate_stream(
            "问题？", EmotionResult("neutral", 0.2, 0.8, {}), []
        )
        try:
            list(stream)
        except Exception as error:  # noqa: BLE001 - the point of the test
            assert "empty" in str(error).lower()
        else:  # pragma: no cover - an empty stream must not pass silently
            raise AssertionError("an empty stream must raise")
    finally:
        module.requests = original  # type: ignore[assignment]
