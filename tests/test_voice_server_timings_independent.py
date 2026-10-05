"""Independent server timing/privacy checks; no model or device invocation."""

from __future__ import annotations

import json
from types import MappingProxyType

import pytest

import srtp_voice.llm as llm_module
from srtp_voice.config import AppConfig
from srtp_voice.llm import StrategyGenerator
from srtp_voice.types import EmotionResult


EMOTION = EmotionResult("neutral", 0.2, 0.8, {})
RAW_KEYS = ["total_duration", "load_duration", "prompt_eval_duration", "eval_duration",
            "prompt_eval_count", "prompt_eval_cached_count", "eval_count"]


def test_units_whitelist_and_zero_are_precise():
    raw = {
        "total_duration": 1_234_567, "load_duration": 0,
        "prompt_eval_duration": 20_000_000, "eval_duration": 5_001_234,
        "prompt_eval_count": 10, "prompt_eval_cached_count": 0, "eval_count": 7,
        "message": {"content": "do-not-export"}, "model": "do-not-export",
        "thinking": "do-not-export", "url": "do-not-export", "context": [1234],
    }
    assert llm_module._extract_ollama_server_timings(MappingProxyType(raw)) == {
        "server_total_ms": 1.235, "model_load_ms": 0.0,
        "input_processing_ms": 20.0, "generation_ms": 5.001,
        "input_tokens": 10, "input_cached_tokens": 0, "output_tokens": 7,
    }


@pytest.mark.parametrize("value", [True, False, -1, 1.2, "100", None, {}, [],
                                   float("nan"), float("inf"), 2**63])
def test_invalid_raw_numbers_are_omitted_not_zero_filled(value):
    assert llm_module._extract_ollama_server_timings(dict.fromkeys(RAW_KEYS, value)) == {}


@pytest.mark.parametrize("value", [None, [], "not a mapping", 0])
def test_unusable_server_diagnostics_do_not_break_text(value):
    assert llm_module._extract_ollama_server_timings(value) == {}


class Response:
    def __init__(self, text="直接答复。", milliseconds=None, *, done_only=False, truncated=False):
        rows = [] if done_only else [{"message": {"content": text}, "done": False}]
        done = {"message": {"content": ""}, "done": True}
        if milliseconds is not None:
            done["load_duration"] = milliseconds * 1_000_000
        if truncated:
            done["done_reason"] = "length"
        rows.append(done)
        self.body = b"".join((json.dumps(x) + "\n").encode() for x in rows)
        self.closed = 0

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        assert chunk_size == 1
        yield self.body

    def close(self):
        self.closed += 1


def _generator(monkeypatch, responses):
    class Requests:
        class RequestException(Exception):
            pass

        Timeout = RequestException
        HTTPError = RequestException

        def post(self, *args, **kwargs):
            return responses.pop(0)

    req = Requests()
    monkeypatch.setattr(llm_module, "requests", req)
    generator = StrategyGenerator(AppConfig(llm_backend="ollama", llm_fallback_to_mock=False))
    monkeypatch.setattr(generator, "_uses_standard_ollama_chat_url", lambda: False)
    return generator, req


def test_interleaved_streams_keep_diagnostics_local_and_do_not_delay_text(monkeypatch):
    first_response = Response(milliseconds=111)
    second_response = Response(milliseconds=222)
    generator, _ = _generator(monkeypatch, [first_response, second_response])
    first = generator.generate_stream("公开问题", EMOTION, [])
    token = next(first)
    assert token.text == "直接答复。"
    assert token.diagnostics == {}
    second = list(generator.generate_stream("另一个问题", EMOTION, []))
    remaining = list(first)
    assert second[-1].is_final and not second[-1].text
    assert remaining[-1].is_final and not remaining[-1].text
    assert second[-1].diagnostics == {"llm_server_requests": [{"model_load_ms": 222.0}]}
    assert remaining[-1].diagnostics == {"llm_server_requests": [{"model_load_ms": 111.0}]}
    second[-1].diagnostics["llm_server_requests"][0]["model_load_ms"] = 999
    assert remaining[-1].diagnostics["llm_server_requests"][0]["model_load_ms"] == 111.0
    assert first_response.closed == second_response.closed == 1


def test_echo_repair_records_completed_requests_separately(monkeypatch):
    generator, _ = _generator(monkeypatch, [Response("公开问题", 111), Response("直接答复。", 222)])
    chunks = list(generator.generate_stream("公开问题", EMOTION, []))
    assert "".join(x.text for x in chunks) == "直接答复。"
    assert chunks[-1].diagnostics == {"llm_server_requests": [
        {"model_load_ms": 111.0}, {"model_load_ms": 222.0},
    ]}


@pytest.mark.parametrize("response", [Response(milliseconds=111, done_only=True),
                                      Response(milliseconds=111, truncated=True)])
def test_failed_or_empty_response_does_not_publish_success_diagnostics(response):
    class Requests:
        class RequestException(Exception):
            pass

        Timeout = RequestException
        HTTPError = RequestException

        def post(self, *args, **kwargs):
            return response

    diagnostics = []
    with pytest.raises(Exception):
        list(StrategyGenerator(AppConfig())._request_ollama_text_stream(Requests(), [], diagnostics))
    assert diagnostics == []
    assert response.closed == 1
