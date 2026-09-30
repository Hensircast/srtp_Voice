"""Own tests for the server-timing extraction and TextChunk diagnostics path.

Fakes only: no network, no model, no real Ollama. The numbers asserted here are
server-reported fields, not end-to-end latency.
"""

from __future__ import annotations

import json

import pytest

from srtp_voice import llm as llm_module
from srtp_voice.config import AppConfig
from srtp_voice.llm import StrategyGenerator, _extract_ollama_server_timings
from srtp_voice.streaming import TextChunk
from srtp_voice.types import EmotionResult


def _full_done_body() -> dict:
    return {
        "model": "some-model",
        "created_at": "2026-09-30T00:00:00Z",
        "message": {"role": "assistant", "content": "", "thinking": "hidden"},
        "done": True,
        "done_reason": "stop",
        "total_duration": 2_500_000_000,
        "load_duration": 0,
        "prompt_eval_count": 12,
        "prompt_eval_duration": 125_000_000,
        "prompt_eval_cached_count": 4,
        "eval_count": 30,
        "eval_duration": 1_000_000_000,
        "context": [1, 2, 3],
    }


def test_extraction_keeps_only_whitelisted_numbers() -> None:
    extracted = _extract_ollama_server_timings(_full_done_body())

    assert extracted == {
        "server_total_ms": 2500.0,
        "model_load_ms": 0.0,
        "input_processing_ms": 125.0,
        "generation_ms": 1000.0,
        "input_tokens": 12,
        "input_cached_tokens": 4,
        "output_tokens": 30,
    }
    assert all(isinstance(value, (int, float)) for value in extracted.values())
    for forbidden in ("model", "created_at", "message", "thinking", "context", "done"):
        assert forbidden not in extracted


@pytest.mark.parametrize(
    "value",
    [-1, 1.5, "100", None, True, 2**63, [1], {"a": 1}, float("inf")],
)
def test_invalid_values_are_ignored(value) -> None:
    assert _extract_ollama_server_timings({"total_duration": value}) == {}
    assert _extract_ollama_server_timings({"eval_count": value}) == {}


def test_zero_is_legal_and_kept() -> None:
    assert _extract_ollama_server_timings({"total_duration": 0, "eval_count": 0}) == {
        "server_total_ms": 0.0,
        "output_tokens": 0,
    }


@pytest.mark.parametrize("body", [None, "text", 5, [1, 2], object()])
def test_non_mapping_input_returns_empty(body) -> None:
    assert _extract_ollama_server_timings(body) == {}


def test_extraction_never_carries_private_text() -> None:
    body = _full_done_body()
    body["message"]["content"] = "我的家庭住址是某某路1号"
    extracted = _extract_ollama_server_timings(body)
    assert "我的家庭住址是某某路1号" not in json.dumps(extracted, ensure_ascii=False)


def test_text_chunk_keeps_positional_arguments() -> None:
    chunk = TextChunk("你好。", False, 12, "session", "turn", 3)
    assert (chunk.text, chunk.is_final, chunk.timestamp_ms) == ("你好。", False, 12)
    assert (chunk.session_id, chunk.turn_id, chunk.sequence_id) == ("session", "turn", 3)
    assert chunk.diagnostics == {}
    other = TextChunk("再见。")
    assert other.diagnostics == {}
    assert other.diagnostics is not chunk.diagnostics


class _Response:
    def __init__(self, lines: list[bytes], *, close_after: int | None = None) -> None:
        self._lines = lines
        self.closed = 0

    def raise_for_status(self) -> None:
        return None

    def close(self) -> None:
        self.closed += 1

    def iter_content(self, chunk_size=None):
        lines = list(self._lines)

        class _Iterator:
            def __init__(self) -> None:
                self.index = 0

            def __iter__(self):
                return self

            def __next__(self) -> bytes:
                if self.index >= len(lines):
                    raise StopIteration
                line = lines[self.index]
                self.index += 1
                return line

            def close(self) -> None:
                return None

        return _Iterator()


class _Module:
    Timeout = TimeoutError
    RequestException = OSError
    HTTPError = RuntimeError

    def __init__(self, bodies: list[dict]) -> None:
        self.bodies = bodies
        self.posts = 0

    def get(self, url, **kwargs):
        return _Response([_json_line({"models": [{"name": "test-model"}]})])

    def post(self, url, **kwargs):
        self.posts += 1
        bodies = self.bodies
        return _Response([_json_line(body) for body in bodies])


def _json_line(payload: dict) -> bytes:
    return (json.dumps(payload) + "\n").encode("utf-8")


def _install(monkeypatch, bodies: list[dict]) -> _Module:
    module = _Module(bodies)
    monkeypatch.setattr(llm_module, "requests", module)
    monkeypatch.setattr(StrategyGenerator, "_check_ollama", lambda self, req=None: None)
    return module


def _generator() -> StrategyGenerator:
    return StrategyGenerator(AppConfig(llm_backend="ollama"))


def _emotion() -> EmotionResult:
    return EmotionResult("neutral", 0.2, 0.8, {})


def test_stream_reports_server_timings_on_the_final_marker(monkeypatch) -> None:
    module = _install(
        monkeypatch,
        [
            {"message": {"content": "第一句。"}, "done": False},
            _full_done_body(),
        ],
    )

    chunks = list(_generator().generate_stream("建议？", _emotion(), [], turn_id="t"))

    assert [chunk.text for chunk in chunks if chunk.text] == ["第一句。"]
    assert all(chunk.diagnostics == {} for chunk in chunks[:-1])
    marker = chunks[-1]
    assert marker.is_final is True and marker.text == ""
    assert marker.diagnostics["llm_server_requests"] == [
        {
            "server_total_ms": 2500.0,
            "model_load_ms": 0.0,
            "input_processing_ms": 125.0,
            "generation_ms": 1000.0,
            "input_tokens": 12,
            "input_cached_tokens": 4,
            "output_tokens": 30,
        }
    ]
    assert module.posts == 1


def test_stream_without_timings_reports_no_diagnostics(monkeypatch) -> None:
    _install(
        monkeypatch,
        [
            {"message": {"content": "第一句。"}, "done": False},
            {"message": {"content": ""}, "done": True, "done_reason": "stop"},
        ],
    )

    chunks = list(_generator().generate_stream("建议？", _emotion(), [], turn_id="t"))
    assert chunks[-1].is_final is True
    assert chunks[-1].diagnostics == {}


def test_interleaved_streams_do_not_share_diagnostics(monkeypatch) -> None:
    _install(
        monkeypatch,
        [
            {"message": {"content": "甲。"}, "done": False},
            {"message": {"content": ""}, "done": True, "eval_count": 7},
        ],
    )
    generator = _generator()

    first = generator.generate_stream("建议？", _emotion(), [], turn_id="a")
    assert next(first).text == "甲。"
    second = generator.generate_stream("建议？", _emotion(), [], turn_id="b")
    second_chunks = list(second)
    first_chunks = list(first)

    assert first_chunks[-1].diagnostics["llm_server_requests"] == [{"output_tokens": 7}]
    assert second_chunks[-1].diagnostics["llm_server_requests"] == [{"output_tokens": 7}]
    assert first_chunks[-1].diagnostics is not second_chunks[-1].diagnostics


def test_truncated_length_stream_fabricates_nothing(monkeypatch) -> None:
    _install(
        monkeypatch,
        [
            {"message": {"content": "片段。"}, "done": False},
            {
                "message": {"content": ""},
                "done": True,
                "done_reason": "length",
                "eval_count": 99,
            },
        ],
    )

    with pytest.raises(Exception):
        list(_generator().generate_stream("建议？", _emotion(), [], turn_id="t"))
