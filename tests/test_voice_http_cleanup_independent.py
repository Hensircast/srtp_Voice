"""Resource assertions keep strong references: collection cannot pass these."""

from __future__ import annotations

import json

import pytest

import srtp_voice.llm as llm_module
from srtp_voice.config import AppConfig
from srtp_voice.llm import StrategyGenerator
from srtp_voice.streaming import TextChunk
from srtp_voice.streaming_runtime import StreamingResponseRuntime, TurnCancelledError
from srtp_voice.types import EmotionResult


EMOTION = EmotionResult("neutral", 0.2, 0.8, {})


class RequestError(Exception):
    pass


class Timeout(RequestError):
    pass


class HTTPError(RequestError):
    def __init__(self, response):
        self.response = response


class Response:
    status_code = 503
    text = "unavailable"

    def __init__(self, mode):
        self.mode = mode
        self.close_calls = 0

    def raise_for_status(self):
        if self.mode == "status":
            raise HTTPError(self)

    def iter_content(self, chunk_size):
        assert chunk_size == 1
        if self.mode == "read":
            raise Timeout("read failed")
        if self.mode == "parse":
            yield b"not-json\n"
            return
        yield (json.dumps({"message": {"content": "直接答复。"}, "done": True}) + "\n").encode()

    def close(self):
        self.close_calls += 1


@pytest.mark.parametrize("mode", ["status", "read", "parse", "done", "early"])
def test_response_closes_exactly_once_on_every_acquired_path(mode):
    response = Response(mode)

    class Requests:
        Timeout = Timeout
        HTTPError = HTTPError
        RequestException = RequestError

        def post(self, *args, **kwargs):
            return response

    reader = StrategyGenerator(AppConfig())._request_ollama_text_stream(Requests(), [])
    if mode in {"status", "read", "parse"}:
        with pytest.raises(Exception):
            next(reader)
    elif mode == "early":
        assert next(reader) == "直接答复。"
    else:
        assert list(reader) == ["直接答复。"]
    reader.close()
    reader.close()
    assert response.close_calls == 1


class RetainedIterator:
    def __init__(self, items):
        self.items = iter(items)
        self.close_calls = 0

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.items)

    def close(self):
        self.close_calls += 1


def test_validated_wrapper_closes_retained_inner_iterator_on_early_stop():
    inner = RetainedIterator(["直接答复。", "尚未消费。"])
    wrapper = StrategyGenerator._validated_stream_reply("问题", inner)
    assert next(wrapper) == "直接答复。"
    wrapper.close()
    assert inner.close_calls == 1


def test_outer_stream_closes_retained_inner_iterator_without_gc(monkeypatch):
    generator = StrategyGenerator(AppConfig(llm_backend="ollama"))
    inner = RetainedIterator(["直接答复。", "尚未消费。"])
    monkeypatch.setattr(llm_module, "requests", object())
    monkeypatch.setattr(generator, "_uses_standard_ollama_chat_url", lambda: False)
    monkeypatch.setattr(generator, "_request_ollama_text_stream", lambda *_: inner)
    stream = generator.generate_stream("问题", EMOTION, [])
    assert next(stream).text == "直接答复。"
    stream.close()
    assert inner.close_calls == 1


@pytest.mark.parametrize("mode", ["status", "read", "parse", "done"])
def test_response_cleanup_failure_does_not_replace_request_result(mode):
    class FaultyClose(Response):
        def close(self):
            super().close()
            raise RuntimeError("cleanup must not replace the outcome")

    response = FaultyClose(mode)

    class Requests:
        Timeout = Timeout
        HTTPError = HTTPError
        RequestException = RequestError

        def post(self, *args, **kwargs):
            return response

    reader = StrategyGenerator(AppConfig())._request_ollama_text_stream(Requests(), [])
    if mode == "done":
        assert list(reader) == ["直接答复。"]
    else:
        with pytest.raises(Exception) as failure:
            list(reader)
        assert "cleanup must not replace" not in str(failure.value)
    assert response.close_calls == 1


@pytest.mark.parametrize("mode", ["cancel", "error", "empty", "submit_error"])
@pytest.mark.parametrize("close_failure", [False, True])
def test_runtime_closes_only_its_retained_turn_iterator(mode, close_failure, monkeypatch, tmp_path):
    class TurnIterator(RetainedIterator):
        def __next__(self):
            if mode == "cancel":
                runtime.cancel_current()
                return TextChunk(text="不应播放。")
            if mode == "error":
                raise RuntimeError("original reader failure")
            return super().__next__()

        def close(self):
            super().close()
            if close_failure:
                raise RuntimeError("cleanup must not replace the outcome")

    turn_stream = TurnIterator([] if mode == "empty" else [TextChunk(text="直接答复。")])

    class Generator:
        close_calls = 0

        def generate_stream(self, *args, **kwargs):
            return turn_stream

        def close(self):
            self.close_calls += 1

    class Adapter:
        def synthesize(self, *args):
            raise AssertionError("nothing should have reached synthesis")

    shared_generator = Generator()
    runtime = StreamingResponseRuntime(
        AppConfig(), shared_generator, Adapter(), playback_enabled=False, temp_parent=tmp_path,
    )
    if mode == "submit_error":
        def reject(*args):
            raise RuntimeError("original submit failure")
        monkeypatch.setattr(runtime, "_submit_sentence", reject)
    handle = runtime.begin_turn()
    try:
        with pytest.raises(TurnCancelledError if mode == "cancel" else RuntimeError) as failure:
            runtime.run_response(
                handle, user_text="问题", emotion=EMOTION, history=[],
                reply_audio=tmp_path / "reply.wav",
            )
        assert "cleanup must not replace" not in str(failure.value)
        assert turn_stream.close_calls == 1
        assert shared_generator.close_calls == 0
    finally:
        runtime.close(drain=False)
    assert shared_generator.close_calls == 0
