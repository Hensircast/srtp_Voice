from __future__ import annotations

import json

import pytest

import srtp_voice.llm as llm_module
from srtp_voice.config import AppConfig
from srtp_voice.llm import LLMResponseError, STREAMING_FALLBACK_REPLY, StrategyGenerator
from srtp_voice.types import EmotionResult


class FakeRequestException(Exception):
    pass


class FakeTimeout(FakeRequestException):
    pass


class FakeHTTPError(FakeRequestException):
    def __init__(self, response=None):
        super().__init__("http error")
        self.response = response


class FakeResponse:
    def __init__(self, chunks=(), *, data=None):
        self._chunks = list(chunks)
        self._data = data
        self.status_code = 200
        self.text = ""
        self.closed = False

    def raise_for_status(self):
        return None

    def json(self):
        return self._data

    def iter_content(self, chunk_size):
        assert chunk_size == 4096
        for chunk in self._chunks:
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk

    def close(self):
        self.closed = True


class FakeRequests:
    Timeout = FakeTimeout
    HTTPError = FakeHTTPError
    RequestException = FakeRequestException

    def __init__(self, responses):
        self.responses = list(responses)
        self.payloads = []
        self.post_calls = []

    def get(self, url, timeout):
        assert timeout == 5
        return FakeResponse(data={"models": [{"name": "qwen3:4b-instruct"}]})

    def post(self, url, *, json, timeout, stream):
        self.payloads.append(json)
        self.post_calls.append((url, timeout, stream))
        if not self.responses:
            raise AssertionError("unexpected extra Ollama request")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _ndjson(*records) -> bytes:
    return b"".join(
        (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        for record in records
    )


def _response_for_text(text: str) -> FakeResponse:
    return FakeResponse(
        [
            _ndjson(
                {"message": {"content": text}, "done": False},
                {"message": {"content": ""}, "done": True, "done_reason": "stop"},
            )
        ]
    )


def _generator(monkeypatch, responses, *, fallback=False):
    fake_requests = FakeRequests(responses)
    monkeypatch.setattr(llm_module, "requests", fake_requests)
    cfg = AppConfig(
        llm_backend="ollama",
        llm_model="qwen3:4b-instruct",
        llm_fallback_to_mock=fallback,
    )
    generator = StrategyGenerator(cfg)
    emotion = EmotionResult("neutral", 0.2, 0.8, {})
    return generator, emotion, fake_requests


def test_ollama_stream_parses_ndjson_across_utf8_byte_boundaries(monkeypatch) -> None:
    encoded = _ndjson(
        {"message": {"content": "你"}, "done": False},
        {"message": {"content": "好，世界。"}, "done": False},
        {"message": {"content": ""}, "done": True, "done_reason": "stop"},
    )
    first_chinese = encoded.index("你".encode("utf-8"))
    response = FakeResponse(
        [
            encoded[: first_chinese + 1],
            encoded[first_chinese + 1 : first_chinese + 2],
            encoded[first_chinese + 2 :],
        ]
    )
    generator, emotion, fake_requests = _generator(monkeypatch, [response])

    chunks = list(
        generator.generate_stream("请打个招呼。", emotion, [], turn_id="turn-utf8")
    )

    assert "".join(chunk.text for chunk in chunks) == "你好，世界。"
    assert [chunk.sequence_id for chunk in chunks] == [0, 1, 2]
    assert all(chunk.turn_id == "turn-utf8" for chunk in chunks)
    assert chunks[-1].is_final is True
    assert chunks[-1].text == ""
    assert fake_requests.payloads[0]["stream"] is True
    assert "format" not in fake_requests.payloads[0]
    assert StrategyGenerator.default_stream_action()["expression"] == "neutral_smile"
    assert fake_requests.post_calls[0][2] is True
    assert response.closed is True


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (FakeResponse([b"not-json\n"]), "invalid NDJSON"),
        (
            FakeResponse([_ndjson({"message": {"content": "answer"}, "done": False})]),
            "before done=true",
        ),
        (
            FakeResponse([_ndjson({"message": {"content": ""}, "done": True})]),
            "empty text stream",
        ),
        (FakeResponse([b'{"message":{"content":"', b"\xe4\xbd"]), "invalid UTF-8"),
        (
            FakeResponse([_ndjson({"error": "model runner stopped", "done": True})]),
            "model runner stopped",
        ),
        (
            FakeResponse(
                [
                    _ndjson(
                        {
                            "message": {"content": "too long"},
                            "done": True,
                            "done_reason": "length",
                        }
                    )
                ]
            ),
            "done_reason=length",
        ),
    ],
)
def test_ollama_stream_reports_malformed_empty_and_truncated_responses(
    monkeypatch,
    response,
    message,
) -> None:
    generator, emotion, _ = _generator(monkeypatch, [response])

    with pytest.raises(LLMResponseError, match=message):
        list(generator.generate_stream("给出答案。", emotion, []))


def test_ollama_stream_reports_read_timeout(monkeypatch) -> None:
    response = FakeResponse([FakeTimeout("slow stream")])
    generator, emotion, _ = _generator(monkeypatch, [response])

    with pytest.raises(RuntimeError, match="timed out while reading"):
        list(generator.generate_stream("给出答案。", emotion, []))


def test_echo_retry_failure_returns_speakable_fallback_without_echo(monkeypatch) -> None:
    user_text = "可以推荐一些家常菜吗？"
    generator, emotion, fake_requests = _generator(
        monkeypatch,
        [_response_for_text(user_text), _response_for_text(user_text)],
    )

    chunks = list(generator.generate_stream(user_text, emotion, []))
    reply = "".join(chunk.text for chunk in chunks)

    assert reply == STREAMING_FALLBACK_REPLY
    assert user_text not in reply
    assert len(fake_requests.payloads) == 2
    assert "上一次回复只复述了问题" in fake_requests.payloads[1]["messages"][0]["content"]
    assert chunks[-1].is_final is True


def test_explicit_repeat_request_can_stream_the_same_text(monkeypatch) -> None:
    user_text = "请原样重复：测试123"
    generator, emotion, fake_requests = _generator(
        monkeypatch,
        [_response_for_text(user_text)],
    )

    chunks = list(generator.generate_stream(user_text, emotion, []))

    assert "".join(chunk.text for chunk in chunks) == user_text
    assert len(fake_requests.payloads) == 1


def test_stream_can_fallback_to_mock_before_any_partial_text(monkeypatch) -> None:
    generator, emotion, _ = _generator(
        monkeypatch,
        [FakeTimeout("request timeout")],
        fallback=True,
    )

    chunks = list(generator.generate_stream("请回答。", emotion, []))

    assert "".join(chunk.text for chunk in chunks).strip()
    assert chunks[-1].is_final is True


def test_stream_error_after_partial_text_does_not_append_fallback(monkeypatch) -> None:
    response = FakeResponse(
        [
            _ndjson({"message": {"content": "实际答案"}, "done": False}),
            b"broken-json\n",
        ]
    )
    generator, emotion, _ = _generator(monkeypatch, [response], fallback=True)
    stream = generator.generate_stream("请回答。", emotion, [])

    first = next(stream)
    assert first.text == "实际答案"
    with pytest.raises(LLMResponseError, match="invalid NDJSON"):
        list(stream)


def test_non_ollama_backend_keeps_a_synchronous_compatibility_stream() -> None:
    generator = StrategyGenerator(AppConfig(llm_backend="mock"))
    emotion = EmotionResult("neutral", 0.2, 0.8, {})

    chunks = list(generator.generate_stream("你好", emotion, [], turn_id="turn-mock"))

    assert len(chunks) == 1
    assert chunks[0].text
    assert chunks[0].is_final is True
    assert chunks[0].turn_id == "turn-mock"
