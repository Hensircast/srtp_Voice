from __future__ import annotations

import pytest

from srtp_voice.streaming import (
    AudioChunk,
    LatencyTracker,
    StreamEvent,
    StreamEventFactory,
    StreamEventType,
    TextChunk,
    TurnTiming,
)


def test_audio_chunk_legacy_positional_constructor_is_compatible() -> None:
    chunk = AudioChunk(b"pcm", 16000, 1, 407, True)

    assert chunk.pcm16 == b"pcm"
    assert chunk.sample_rate == 16000
    assert chunk.channels == 1
    assert chunk.timestamp_ms == 407
    assert chunk.is_final is True
    assert chunk.session_id == ""
    assert chunk.turn_id == ""
    assert chunk.sequence_id == 0


def test_text_chunk_legacy_positional_constructor_is_compatible() -> None:
    chunk = TextChunk("hello", True, 407)

    assert chunk.text == "hello"
    assert chunk.is_final is True
    assert chunk.timestamp_ms == 407
    assert chunk.session_id == ""
    assert chunk.turn_id == ""
    assert chunk.sequence_id == 0


def test_future_stream_identifiers_are_optional_metadata() -> None:
    audio = AudioChunk(
        b"pcm",
        16000,
        session_id="session-1",
        turn_id="turn-2",
        sequence_id=3,
    )
    text = TextChunk(
        "hello",
        session_id="session-1",
        turn_id="turn-2",
        sequence_id=4,
    )

    assert (audio.session_id, audio.turn_id, audio.sequence_id) == (
        "session-1",
        "turn-2",
        3,
    )
    assert (text.session_id, text.turn_id, text.sequence_id) == (
        "session-1",
        "turn-2",
        4,
    )


def test_v18_stream_event_vocabulary_is_complete() -> None:
    required = {
        "turn_started",
        "audio_frame",
        "vad_started",
        "vad_stopped",
        "asr_partial",
        "asr_final",
        "llm_token",
        "sentence_ready",
        "tts_started",
        "audio_chunk_ready",
        "playback_started",
        "playback_finished",
        "turn_cancelled",
        "error",
    }

    assert required <= {event.value for event in StreamEventType}


def test_event_factory_uses_monotonic_timestamps_and_stable_sequences() -> None:
    times = iter([10.0, 10.125, 10.5])
    factory = StreamEventFactory("turn-1", clock=lambda: next(times))

    started = factory.emit(StreamEventType.TURN_STARTED)
    partial = factory.emit("asr_partial", {"text": "你"})
    final = factory.emit(StreamEventType.ASR_FINAL, {"text": "你好"})

    assert [started.sequence, partial.sequence, final.sequence] == [0, 1, 2]
    assert [started.timestamp, partial.timestamp, final.timestamp] == [10.0, 10.125, 10.5]
    assert partial.to_dict() == {
        "event_type": "asr_partial",
        "turn_id": "turn-1",
        "sequence": 1,
        "timestamp": 10.125,
        "payload": {"text": "你"},
    }


def test_event_factory_rejects_a_clock_that_moves_backwards() -> None:
    times = iter([5.0, 4.9])
    factory = StreamEventFactory("turn-1", clock=lambda: next(times))
    factory.emit(StreamEventType.TURN_STARTED)

    with pytest.raises(RuntimeError, match="backwards"):
        factory.emit(StreamEventType.VAD_STARTED)


def test_stream_event_validates_identity_sequence_and_timestamp() -> None:
    with pytest.raises(ValueError, match="turn_id"):
        StreamEvent(StreamEventType.TURN_STARTED, "", 0, 1.0)
    with pytest.raises(ValueError, match="non-negative"):
        StreamEvent(StreamEventType.TURN_STARTED, "turn-1", -1, 1.0)
    with pytest.raises(ValueError, match="finite"):
        StreamEvent(StreamEventType.TURN_STARTED, "turn-1", 0, float("nan"))


def test_turn_timing_records_first_occurrence_and_latency_boundaries() -> None:
    timing = TurnTiming("turn-1")
    events = [
        StreamEvent(StreamEventType.TURN_STARTED, "turn-1", 0, 10.0),
        StreamEvent(StreamEventType.VAD_STARTED, "turn-1", 1, 10.1),
        StreamEvent(StreamEventType.ASR_PARTIAL, "turn-1", 2, 10.2),
        StreamEvent(StreamEventType.ASR_PARTIAL, "turn-1", 3, 10.3),
        StreamEvent(StreamEventType.VAD_STOPPED, "turn-1", 4, 10.4),
        StreamEvent(StreamEventType.ASR_FINAL, "turn-1", 5, 10.5),
        StreamEvent(StreamEventType.LLM_REQUEST_STARTED, "turn-1", 6, 10.6),
        StreamEvent(StreamEventType.LLM_TOKEN, "turn-1", 7, 10.7),
        StreamEvent(StreamEventType.SENTENCE_READY, "turn-1", 8, 10.9),
        StreamEvent(StreamEventType.TTS_STARTED, "turn-1", 9, 11.0),
        StreamEvent(StreamEventType.AUDIO_CHUNK_READY, "turn-1", 10, 11.2),
        StreamEvent(StreamEventType.PLAYBACK_STARTED, "turn-1", 11, 11.3),
        StreamEvent(StreamEventType.PLAYBACK_FINISHED, "turn-1", 12, 12.0),
        StreamEvent(StreamEventType.TURN_FINISHED, "turn-1", 13, 12.1),
    ]
    for event in events:
        timing.observe(event)

    snapshot = timing.snapshot()

    assert snapshot.marks["asr_first_partial"] == 10.2
    assert snapshot.latencies_ms == {
        "vad_start_ms": 100.0,
        "vad_duration_ms": 300.0,
        "asr_first_partial_ms": 100.0,
        "asr_final_ms": 100.0,
        "llm_first_token_ms": 100.0,
        "first_sentence_ms": 300.0,
        "tts_first_chunk_ms": 300.0,
        "time_to_first_token_ms": 700.0,
        "time_to_first_sentence_ms": 900.0,
        "time_to_first_audio_ms": 1200.0,
        "time_to_playback_ms": 1300.0,
        "playback_duration_ms": 700.0,
        "turn_total_ms": 2100.0,
    }


def test_turn_timing_rejects_wrong_turn_and_out_of_order_events() -> None:
    timing = TurnTiming("turn-1")
    timing.observe(StreamEvent(StreamEventType.TURN_STARTED, "turn-1", 2, 2.0))

    with pytest.raises(ValueError, match="turn_id"):
        timing.observe(StreamEvent(StreamEventType.VAD_STARTED, "turn-2", 3, 3.0))
    with pytest.raises(ValueError, match="strictly increasing"):
        timing.observe(StreamEvent(StreamEventType.VAD_STARTED, "turn-1", 2, 3.0))
    with pytest.raises(ValueError, match="monotonic"):
        timing.observe(StreamEvent(StreamEventType.VAD_STARTED, "turn-1", 3, 1.0))


def test_latency_tracker_aggregates_p50_and_p95_across_turns() -> None:
    tracker = LatencyTracker()
    for index in range(1, 101):
        turn_id = f"turn-{index}"
        tracker.observe(StreamEvent(StreamEventType.TURN_STARTED, turn_id, 0, 10.0))
        tracker.observe(
            StreamEvent(
                StreamEventType.PLAYBACK_FINISHED,
                turn_id,
                1,
                10.0 + index / 1000.0,
            )
        )
        tracker.finish_turn(turn_id)

    summary = tracker.summary()["turn_total_ms"]

    assert tracker.completed_turns == 100
    assert summary == {
        "count": 100,
        "min": 1.0,
        "p50": 50.5,
        "p95": 95.05,
        "max": 100.0,
    }
