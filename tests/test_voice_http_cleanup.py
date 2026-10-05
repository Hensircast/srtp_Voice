"""Own lifecycle tests for the streaming HTTP reader (fakes only, no network).

These assert the response/iterator release contract: a status error closes the
acquired response, normal completion closes once, read failures and early stops
still release the reader, and iterators without close() keep working.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from srtp_voice import llm as llm_module
from srtp_voice.config import AppConfig
from srtp_voice.llm import StrategyGenerator
from srtp_voice.types import EmotionResult


class _HTTPError(Exception):
    def __init__(self, message: str, response: Any = None) -> None:
        super().__init__(message)
        self.response = response
        self.status_code = 500
        self.text = "upstream failure"


class _Response:
    def __init__(self, chunks, *, status_error: bool = False) -> None:
        self._chunks = list(chunks)
        self.status_error = status_error
        self.status_code = 500
        self.text = "upstream failure"
        self.closed = 0
        self.iterators = 0
        self.iterator_closed = 0
        self.read_error: BaseException | None = None

    def raise_for_status(self) -> None:
        if self.status_error:
            raise _HTTPError("HTTP 500", self)

    def close(self) -> None:
        self.closed += 1

    def iter_content(self, chunk_size=None):
        self.iterators += 1
        response = self

        class _Iterator:
            def __init__(self) -> None:
                self.index = 0
                self.closed = False

            def __iter__(self) -> "_Iterator":
                return self

            def __next__(self) -> bytes:
                if response.read_error is not None:
                    raise response.read_error
                if self.index >= len(response._chunks):
                    raise StopIteration
                chunk = response._chunks[self.index]
                self.index += 1
                return chunk

            def close(self) -> None:
                if not self.closed:
                    self.closed = True
                    response.iterator_closed += 1

        return _Iterator()


class _Requests:
    Timeout = TimeoutError
    RequestException = OSError
    HTTPError = _HTTPError

    def __init__(self, response: _Response) -> None:
        self.response = response

    def post(self, url, **kwargs):
        return self.response


def _line(payload: dict) -> bytes:
    return (json.dumps(payload) + "\n").encode("utf-8")


def _ollama_line(content: str, done: bool = False) -> bytes:
    return _line({"message": {"content": content}, "done": done})


def _install(monkeypatch, response: _Response) -> _Requests:
    fake = _Requests(response)
    monkeypatch.setattr(llm_module, "requests", fake)
    monkeypatch.setattr(StrategyGenerator, "_check_ollama", lambda self: None)
    return fake


def _generator() -> StrategyGenerator:
    return StrategyGenerator(AppConfig(llm_backend="ollama", llm_timeout_seconds=2))


def _emotion() -> EmotionResult:
    return EmotionResult("neutral", 0.2, 0.8, {})


def test_status_error_closes_the_acquired_response(monkeypatch) -> None:
    response = _Response([], status_error=True)
    _install(monkeypatch, response)

    with pytest.raises(RuntimeError):
        list(_generator().generate_stream("问题？", _emotion(), []))

    assert response.closed == 1
    assert response.iterators == 0


def test_normal_completion_closes_reader_and_response_once(monkeypatch) -> None:
    response = _Response([_ollama_line("可以。"), _ollama_line("", done=True)])
    _install(monkeypatch, response)

    chunks = list(_generator().generate_stream("建议？", _emotion(), []))

    assert [chunk.text for chunk in chunks if chunk.text] == ["可以。"]
    assert response.iterators == 1
    assert response.iterator_closed == 1
    assert response.closed == 1


def test_early_stop_closes_reader_and_response(monkeypatch) -> None:
    response = _Response([_ollama_line("第一段。"), _ollama_line("第二段。", done=True)])
    _install(monkeypatch, response)

    stream = _generator().generate_stream("建议？", _emotion(), [])
    try:
        first = next(stream)
        assert first.text == "第一段。"
    finally:
        stream.close()

    assert response.iterator_closed == 1
    assert response.closed == 1


def test_reader_failure_releases_the_response(monkeypatch) -> None:
    response = _Response([_ollama_line("可以。")])
    response.read_error = _Requests.RequestException("read failed")
    _install(monkeypatch, response)

    with pytest.raises(RuntimeError):
        list(_generator().generate_stream("问题？", _emotion(), []))

    assert response.iterator_closed == 1
    assert response.closed == 1


def test_plain_iterables_without_close_are_unaffected() -> None:
    # A plain list has no close(); the helper must still stream every token.
    assert list(StrategyGenerator._validated_stream_reply("建议？", ["可以。", "再说一句。"])) == [
        "可以。",
        "再说一句。",
    ]
    consumed = list(StrategyGenerator._validated_stream_reply("建议？", iter(["甲。", "乙。"])))
    assert "".join(consumed) == "甲。乙。"
