"""Own tests for the step-2 backend diagnostics wiring (fakes only).

The values here are server-reported fields copied through the pipeline; they are
not end-to-end latency measurements and no speed-up is claimed.
"""

from __future__ import annotations

import struct
import threading
import wave

import pytest

from srtp_voice.config import AppConfig
from srtp_voice.streaming import (
    StreamEvent,
    StreamEventType,
    TextChunk,
    TurnTiming,
    sanitize_llm_diagnostics,
)
from srtp_voice.streaming_runtime import StreamingResponseRuntime
from srtp_voice.types import EmotionResult

from tools import workbench_latency


def _payload(**entry) -> dict:
    return {"llm_server_requests": [entry]}


def test_sanitizer_keeps_only_valid_numbers() -> None:
    cleaned = sanitize_llm_diagnostics(
        _payload(
            server_total_ms=2500.0,
            model_load_ms=0,
            input_processing_ms=125.5,
            generation_ms=1000,
            input_tokens=12,
            input_cached_tokens=0,
            output_tokens=30,
            model="private-model",
            prompt="private text",
            done=True,
        )
    )
    assert cleaned == {
        "llm_server_requests": [
            {
                "server_total_ms": 2500.0,
                "model_load_ms": 0.0,
                "input_processing_ms": 125.5,
                "generation_ms": 1000.0,
                "input_tokens": 12,
                "input_cached_tokens": 0,
                "output_tokens": 30,
            }
        ]
    }


@pytest.mark.parametrize(
    "entry",
    [
        {"server_total_ms": -1},
        {"server_total_ms": float("nan")},
        {"server_total_ms": float("inf")},
        {"server_total_ms": True},
        {"server_total_ms": "100"},
        {"server_total_ms": 2**63},
        {"input_tokens": 1.5},
        {"input_tokens": -3},
        {"input_tokens": 2**63},
        {"output_tokens": True},
    ],
)
def test_sanitizer_drops_invalid_values(entry) -> None:
    assert sanitize_llm_diagnostics(_payload(**entry)) == {}


def test_sanitizer_is_defensive_about_shape_and_size() -> None:
    assert sanitize_llm_diagnostics(None) == {}
    assert sanitize_llm_diagnostics("text") == {}
    assert sanitize_llm_diagnostics({"llm_server_requests": "not a list"}) == {}
    assert sanitize_llm_diagnostics({"llm_server_requests": [None, 5, "x"]}) == {}
    # Only the first four entries are inspected.
    many = {"llm_server_requests": [{"input_tokens": n} for n in range(10)]}
    assert sanitize_llm_diagnostics(many) == {
        "llm_server_requests": [
            {"input_tokens": 0},
            {"input_tokens": 1},
            {"input_tokens": 2},
            {"input_tokens": 3},
        ]
    }
    # A huge int is range-checked before isfinite, so it cannot raise.
    assert sanitize_llm_diagnostics(_payload(server_total_ms=10**400)) == {}


def test_sanitizer_copies_instead_of_aliasing() -> None:
    source = _payload(input_tokens=5)
    cleaned = sanitize_llm_diagnostics(source)
    cleaned["llm_server_requests"][0]["input_tokens"] = 999
    assert source["llm_server_requests"][0]["input_tokens"] == 5


def _event(
    turn_id: str,
    sequence: int,
    timestamp: float,
    event_type: StreamEventType,
    payload: dict | None = None,
) -> StreamEvent:
    return StreamEvent(
        event_type=event_type,
        turn_id=turn_id,
        sequence=sequence,
        timestamp=timestamp,
        payload=payload or {},
    )


def test_legacy_snapshot_stays_compatible() -> None:
    legacy = TurnTiming("turn-1")
    legacy.observe(_event("turn-1", 0, 1.0, StreamEventType.TURN_STARTED))
    snapshot = legacy.snapshot()
    assert snapshot.backend_diagnostics == {}
    assert "backend_diagnostics" not in snapshot.to_dict()
    assert snapshot.to_dict()["latencies_ms"] == {}


def test_turn_timing_keeps_the_first_valid_report_only() -> None:
    timing = TurnTiming("turn-2")
    timing.observe(_event("turn-2", 0, 1.0, StreamEventType.TURN_STARTED))
    timing.observe(
        _event(
            "turn-2",
            1,
            1.1,
            StreamEventType.LLM_DIAGNOSTICS,
            _payload(input_tokens=7),
        )
    )
    timing.observe(
        _event(
            "turn-2",
            2,
            1.2,
            StreamEventType.LLM_DIAGNOSTICS,
            _payload(input_tokens=99),
        )
    )
    timing.observe(_event("turn-2", 3, 1.3, StreamEventType.TURN_FINISHED))

    snapshot = timing.snapshot()
    assert snapshot.backend_diagnostics == {"llm_server_requests": [{"input_tokens": 7}]}
    payload = snapshot.to_dict()
    assert payload["backend_diagnostics"] == {"llm_server_requests": [{"input_tokens": 7}]}
    # Server statistics never leak into the latency metrics.
    assert "backend_diagnostics" not in payload["latencies_ms"]
    assert all("token" not in name for name in payload["latencies_ms"])

    other = TurnTiming("turn-3")
    other.observe(_event("turn-3", 0, 2.0, StreamEventType.TURN_STARTED))
    assert other.snapshot().backend_diagnostics == {}


def test_turn_timing_ignores_invalid_diagnostics() -> None:
    timing = TurnTiming("turn-4")
    timing.observe(_event("turn-4", 0, 1.0, StreamEventType.TURN_STARTED))
    timing.observe(
        _event(
            "turn-4",
            1,
            1.1,
            StreamEventType.LLM_DIAGNOSTICS,
            {"llm_server_requests": [{"server_total_ms": "bad"}]},
        )
    )
    assert timing.snapshot().backend_diagnostics == {}


def test_baseline_sanitizer_preserves_only_safe_diagnostics() -> None:
    metric_names = workbench_latency.known_metric_names()
    mark_names = workbench_latency.known_mark_names()
    cleaned, ignored, renamed = workbench_latency._sanitize_turn(
        {
            "turn_id": "turn-a",
            "marks": {},
            "latencies_ms": {},
            "backend_diagnostics": {
                "llm_server_requests": [
                    {
                        "input_tokens": 12,
                        "output_tokens": 30,
                        "model": "private-model",
                        "prompt": "private text",
                    }
                ]
            },
        },
        0,
        metric_names,
        mark_names,
    )
    assert cleaned["backend_diagnostics"] == {
        "llm_server_requests": [{"input_tokens": 12, "output_tokens": 30}]
    }
    assert ignored == 0 and renamed == 0
    assert "private" not in str(cleaned)

    without, _, _ = workbench_latency._sanitize_turn(
        {"turn_id": "turn-b", "marks": {}, "latencies_ms": {}},
        1,
        metric_names,
        mark_names,
    )
    assert "backend_diagnostics" not in without


class _TtsStub:
    def synthesize(self, text, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # A real, minimal 16-bit mono WAV: the player must not see raw PCM.
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(1000)
            audio.writeframes(struct.pack("<4h", 0, 1, 0, -1))

    def for_streaming(self):
        return self


class _GeneratorStub:
    def __init__(self, tokens) -> None:
        self._tokens = tokens

    def generate_stream(self, user_text, emotion, history, *, turn_id=""):
        return iter(self._tokens)

    @staticmethod
    def default_stream_action():
        return {"expression": "neutral_smile"}


def _runtime(tmp_path, tokens) -> StreamingResponseRuntime:
    return StreamingResponseRuntime(
        AppConfig(
            output_dir=tmp_path,
            stream_sentence_max_chars=100,
            stream_natural_boundaries=False,
        ),
        _GeneratorStub(tokens),
        _TtsStub(),
        playback_enabled=False,
        temp_parent=tmp_path,
        id_factory=lambda: "diag-turn",
    )


def test_runtime_emits_one_diagnostics_event_at_the_final_marker(tmp_path) -> None:
    diagnostics = _payload(input_tokens=9)
    tokens = [
        TextChunk("第一句。", turn_id="diag-turn", sequence_id=0),
        TextChunk("", is_final=True, turn_id="diag-turn", sequence_id=1, diagnostics=diagnostics),
    ]
    runtime = _runtime(tmp_path, tokens)
    handle = runtime.begin_turn()
    try:
        runtime.run_response(
            handle,
            user_text="建议？",
            emotion=EmotionResult("neutral", 0.2, 0.8, {}),
            history=[],
            reply_audio=tmp_path / "reply.wav",
        )
        events = [
            event
            for event in runtime.controller.history
            if event.event_type is StreamEventType.LLM_DIAGNOSTICS
        ]
        assert len(events) == 1
        snapshot = runtime.controller.latency_history()[-1]
        assert snapshot["backend_diagnostics"] == {
            "llm_server_requests": [{"input_tokens": 9}]
        }
        assert "backend_diagnostics" not in snapshot["latencies_ms"]
    finally:
        runtime.close()


def test_runtime_without_diagnostics_emits_nothing(tmp_path) -> None:
    tokens = [
        TextChunk("第一句。", turn_id="diag-turn", sequence_id=0),
        TextChunk("", is_final=True, turn_id="diag-turn", sequence_id=1),
    ]
    runtime = _runtime(tmp_path, tokens)
    handle = runtime.begin_turn()
    try:
        runtime.run_response(
            handle,
            user_text="建议？",
            emotion=EmotionResult("neutral", 0.2, 0.8, {}),
            history=[],
            reply_audio=tmp_path / "reply.wav",
        )
        assert not [
            event
            for event in runtime.controller.history
            if event.event_type is StreamEventType.LLM_DIAGNOSTICS
        ]
        assert "backend_diagnostics" not in runtime.controller.latency_history()[-1]
    finally:
        runtime.close()
