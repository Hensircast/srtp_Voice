"""Independent numeric diagnostic gates. Fake models/audio, never a speed claim."""
from __future__ import annotations

import json
import struct
import threading
import wave

import pytest

from srtp_voice.config import AppConfig
from srtp_voice.streaming import StreamEventFactory, StreamEventType, TextChunk, TurnTiming, TurnTimingSnapshot
from srtp_voice.streaming_runtime import StreamingResponseRuntime, StreamingTurnController
from srtp_voice.types import EmotionResult
from tools.workbench_latency import _sanitize_turn, capture_baseline, known_mark_names, known_metric_names


def clean(payload):
    from srtp_voice.streaming import sanitize_llm_diagnostics
    return sanitize_llm_diagnostics(payload)


def stats(value=123.125):
    return {"llm_server_requests": [{"model_load_ms": value, "input_tokens": 0}]}


@pytest.mark.parametrize("value", [True, False, -1, "1", None, [], {}, float("nan"), float("inf"), 10**400])
def test_illegal_durations_are_dropped_without_affecting_valid_zero(value):
    assert clean({"llm_server_requests": [{"model_load_ms": value, "generation_ms": 0}]}) == {
        "llm_server_requests": [{"generation_ms": 0}]
    }


@pytest.mark.parametrize("value", [True, 1.0, -1, "2", 2**63])
def test_token_counts_require_bounded_real_integers(value):
    assert clean({"llm_server_requests": [{"input_tokens": value}]}) == {}


def test_private_fields_bounded_array_and_independent_copies():
    payload = {"llm_server_requests": [
        {"model_load_ms": index, "input_tokens": 2**63 - 1, "prompt": "private", "url": "private"}
        for index in range(20)
    ], "message": "private"}
    result = clean(payload)
    assert result == {"llm_server_requests": [
        {"model_load_ms": index, "input_tokens": 2**63 - 1} for index in range(4)
    ]}
    result["llm_server_requests"][0]["model_load_ms"] = 77
    assert payload["llm_server_requests"][0]["model_load_ms"] == 0
    assert "private" not in json.dumps(result)


def test_cleaner_slices_only_first_four_without_copying_entire_container():
    class BoundedList(list):
        def __iter__(self):
            raise AssertionError("whole list iteration defeats the bound")

    payload = {"llm_server_requests": BoundedList([{"output_tokens": index} for index in range(50)])}
    assert clean(payload) == {"llm_server_requests": [{"output_tokens": index} for index in range(4)]}


@pytest.mark.parametrize("payload", [None, [], "private", {"llm_server_requests": iter([{}])}, {"llm_server_requests": [None]}])
def test_bad_or_unbounded_container_does_not_enter_snapshot(payload):
    assert clean(payload) == {}


def test_optional_snapshot_keeps_legacy_contract_and_copy_isolation():
    assert TurnTimingSnapshot("old", {}, {}).to_dict() == {"turn_id": "old", "marks": {}, "latencies_ms": {}}
    factory = StreamEventFactory("new", clock=lambda: 10.0)
    timing = TurnTiming("new")
    timing.observe(factory.emit(StreamEventType.TURN_STARTED))
    diagnostic = factory.emit(StreamEventType.LLM_DIAGNOSTICS, stats())
    timing.observe(diagnostic)
    snapshot = timing.snapshot()
    snapshot.backend_diagnostics["llm_server_requests"][0]["model_load_ms"] = 654
    assert timing.snapshot().to_dict()["backend_diagnostics"] == stats()
    snapshot = timing.snapshot()
    diagnostic.payload["llm_server_requests"][0]["model_load_ms"] = 999
    timing.observe(factory.emit(StreamEventType.LLM_DIAGNOSTICS, stats(555)))
    assert timing.snapshot().to_dict()["backend_diagnostics"] == stats()
    exported = snapshot.to_dict()
    exported["backend_diagnostics"]["llm_server_requests"][0]["model_load_ms"] = 888
    assert snapshot.to_dict()["backend_diagnostics"] == stats()
    assert snapshot.latencies_ms == {}


def test_late_diagnostics_do_not_pollute_next_turn_or_latency_summary():
    ids = iter(["old", "new"])
    controller = StreamingTurnController(id_factory=lambda: next(ids))
    old = controller.start_turn()
    controller.emit(old.turn_id, StreamEventType.LLM_DIAGNOSTICS, stats(11))
    current = controller.start_turn()
    assert controller.emit(old.turn_id, StreamEventType.LLM_DIAGNOSTICS, stats(99)) is None
    controller.emit(current.turn_id, StreamEventType.LLM_DIAGNOSTICS, stats(22))
    snapshot = controller.finish_turn(current.turn_id)
    assert snapshot.to_dict()["backend_diagnostics"] == stats(22)
    assert all("model_load" not in key for key in controller.latency_summary())
    assert controller.late_events == 1


def test_baseline_retains_only_numeric_backend_diagnostics():
    raw = {"turn_id": "safe-turn", "marks": {}, "latencies_ms": {"llm_first_token_ms": 9},
           "backend_diagnostics": {"llm_server_requests": [{"model_load_ms": 0, "input_tokens": 7,
                                                          "thinking": "private", "model": "private"}],
                                   "prompt": "private"}, "message": "private"}
    result, ignored, renamed = _sanitize_turn(raw, 0, known_metric_names(), known_mark_names())
    assert result["backend_diagnostics"] == {"llm_server_requests": [{"model_load_ms": 0, "input_tokens": 7}]}
    assert result["latencies_ms"] == {"llm_first_token_ms": 9}
    assert (ignored, renamed) == (0, 0)
    assert "private" not in json.dumps(result)
    raw["backend_diagnostics"]["llm_server_requests"][0]["model_load_ms"] = 500
    assert result["backend_diagnostics"]["llm_server_requests"][0]["model_load_ms"] == 0
    legacy, _, _ = _sanitize_turn({"turn_id": "legacy"}, 1, known_metric_names(), known_mark_names())
    assert "backend_diagnostics" not in legacy


def test_runtime_speaks_before_final_stats_and_emits_only_once(tmp_path):
    release = threading.Event()
    spoken = threading.Event()
    events, results, failures = [], [], []

    class Generator:
        def generate_stream(self, *args, turn_id="", **kwargs):
            yield TextChunk("你好！", diagnostics=stats(999), turn_id=turn_id)
            assert release.wait(timeout=3)
            payload = stats(123.125)
            payload["private_text"] = "must-not-enter-event-log"
            payload["llm_server_requests"][0]["thinking"] = "must-not-enter-event-log"
            yield TextChunk("", is_final=True, diagnostics=payload, turn_id=turn_id)
            yield TextChunk("", is_final=True, diagnostics=stats(777), turn_id=turn_id)

        def default_stream_action(self):
            return {}

    class TTS:
        def synthesize(self, text, path):
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(1000)
                wav.writeframes(struct.pack("<hhhh", 1, 2, 3, 4))
            spoken.set()

    runtime = StreamingResponseRuntime(AppConfig(output_dir=tmp_path, sample_rate=1000), Generator(), TTS(),
                                       playback_enabled=False, temp_parent=tmp_path, event_sink=events.append)
    handle = runtime.begin_turn()

    def run():
        try:
            results.append(runtime.run_response(handle, user_text="test", emotion=EmotionResult("neutral", .2, .8, {}),
                                                history=[], reply_audio=tmp_path / "reply.wav"))
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert spoken.wait(timeout=2), "final diagnostic must not delay the first sentence"
        assert not any(event.event_type.value == "llm_diagnostics" for event in events)
    finally:
        release.set()
        thread.join(timeout=5)
        runtime.close()
    assert not thread.is_alive()
    assert failures == []
    diagnostic_events = [event for event in events if event.event_type.value == "llm_diagnostics"]
    assert len(diagnostic_events) == 1
    assert diagnostic_events[0].payload == stats()
    assert "must-not-enter-event-log" not in json.dumps(diagnostic_events[0].to_dict())
    assert results[0].latency.to_dict()["backend_diagnostics"] == stats()
    assert results[0].strategy.reply_text == "你好！"


def test_actual_llm_pipeline_retains_server_fields_in_redacted_baseline(monkeypatch, tmp_path):
    from srtp_voice import llm as llm_module
    from srtp_voice.llm import StrategyGenerator

    class Requests:
        class RequestException(Exception):
            pass
        Timeout = RequestException
        HTTPError = RequestException

        def post(self, *args, **kwargs):
            class Response:
                def raise_for_status(self):
                    pass

                def iter_content(self, chunk_size):
                    assert chunk_size == 1
                    for row in [{"message": {"content": "你好！"}, "done": False},
                                {"message": {"content": "", "thinking": "private-secret"}, "done": True,
                                 "load_duration": 123_125_000, "eval_count": 7, "context": ["private-secret"]}]:
                        yield (json.dumps(row) + "\n").encode("utf-8")

                def close(self):
                    pass
            return Response()

    class TTS:
        def synthesize(self, text, path):
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(1000)
                wav.writeframes(struct.pack("<hhhh", 1, 2, 3, 4))

    monkeypatch.setattr(llm_module, "requests", Requests())
    monkeypatch.setattr(StrategyGenerator, "_check_ollama", lambda self: None)
    cfg = AppConfig(llm_backend="ollama", output_dir=tmp_path, sample_rate=1000)
    runtime = StreamingResponseRuntime(cfg, StrategyGenerator(cfg), TTS(), playback_enabled=False, temp_parent=tmp_path)
    try:
        handle = runtime.begin_turn()
        runtime.run_response(handle, user_text="test", emotion=EmotionResult("neutral", .2, .8, {}), history=[],
                             reply_audio=tmp_path / "reply.wav")
        source = tmp_path / "metrics.json"
        source.write_text(json.dumps({"turns": runtime.controller.latency_history(),
                                     "summary": runtime.controller.latency_summary()}), encoding="utf-8")
        document, _ = capture_baseline(source, measurement="simulated", label="independent-diagnostics", cfg=cfg,
                                       root=tmp_path, git={"commit": "unknown", "branch": "unknown", "dirty": True})
    finally:
        runtime.close()
    assert document["per_turn"][0]["backend_diagnostics"] == {
        "llm_server_requests": [{"model_load_ms": 123.125, "output_tokens": 7}]
    }
    assert "private-secret" not in json.dumps(document)
    assert "model_load_ms" not in document["summary"]
