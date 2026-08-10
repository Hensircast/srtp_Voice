from __future__ import annotations

import argparse
import json
import struct
import types
import wave

import pytest

from srtp_voice.config import AppConfig
from srtp_voice.streaming import StreamEventType, TextChunk
from srtp_voice.streaming_runtime import (
    StreamingResponseRuntime,
    StreamingTurnController,
    TurnCancelledError,
    capture_streaming_microphone,
)
from srtp_voice.state_machine import DialogueStage, DialogueStateMachine
from srtp_voice.types import EmotionResult


def _write_wav(path, value=100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(1000)
        wav_file.writeframes(struct.pack("<hhhh", value, value, value, value))


class FakeTTS:
    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    def synthesize(self, text, path):
        self.calls.append((text, path))
        if self.fail:
            raise RuntimeError("tts failed")
        _write_wav(path, len(self.calls))


class FakeGenerator:
    def __init__(self, tokens):
        self.tokens = list(tokens)

    def generate_stream(self, user_text, emotion, history, *, turn_id=""):
        for sequence, token in enumerate(self.tokens):
            yield TextChunk(token, turn_id=turn_id, sequence_id=sequence)
        yield TextChunk(
            "",
            is_final=True,
            turn_id=turn_id,
            sequence_id=len(self.tokens),
        )

    @staticmethod
    def default_stream_action():
        return {"expression": "neutral_smile", "gaze": "look_at_user"}


def _cfg(tmp_path, **overrides):
    values = {
        "output_dir": tmp_path,
        "sample_rate": 1000,
        "stream_tts_queue_size": 2,
        "stream_sentence_max_chars": 100,
        "stream_sentence_max_wait_seconds": 1.0,
    }
    values.update(overrides)
    return AppConfig(**values)


def test_turn_controller_cancels_old_turn_and_drops_late_events() -> None:
    ids = iter(["turn-1", "turn-2"])
    events = []
    cancelled = []
    controller = StreamingTurnController(
        id_factory=lambda: next(ids),
        event_sink=events.append,
        cancel_hook=cancelled.append,
        history_maxsize=16,
    )

    first = controller.start_turn()
    controller.emit(first.turn_id, StreamEventType.VAD_STARTED)
    second = controller.start_turn()
    assert controller.emit(first.turn_id, StreamEventType.ASR_FINAL) is None
    controller.emit(second.turn_id, StreamEventType.LLM_TOKEN)
    second_snapshot = controller.finish_turn(second.turn_id)

    assert first.cancelled.is_set()
    assert cancelled == ["turn-1"]
    assert controller.late_events == 1
    assert controller.active_turn_id is None
    assert second_snapshot.turn_id == "turn-2"
    per_turn_sequences = {
        turn_id: [event.sequence for event in events if event.turn_id == turn_id]
        for turn_id in ["turn-1", "turn-2"]
    }
    assert per_turn_sequences == {"turn-1": [0, 1, 2], "turn-2": [0, 1, 2]}
    assert [event.event_type for event in events if event.turn_id == "turn-1"][-1] == (
        StreamEventType.TURN_CANCELLED
    )


def test_turn_event_history_is_bounded() -> None:
    controller = StreamingTurnController(
        id_factory=lambda: "turn-1",
        history_maxsize=2,
    )
    handle = controller.start_turn()
    controller.emit(handle.turn_id, StreamEventType.VAD_STARTED)
    controller.emit(handle.turn_id, StreamEventType.VAD_STOPPED)
    controller.finish_turn(handle.turn_id)

    assert len(controller.history) == 2
    assert controller.dropped_history_events == 2


def test_streaming_response_reconstructs_text_and_combines_audio(tmp_path) -> None:
    events = []
    tts = FakeTTS()
    played = []
    runtime = StreamingResponseRuntime(
        _cfg(tmp_path),
        FakeGenerator(["第一句。第", "二句没有标点"]),
        tts,
        event_sink=events.append,
        player=lambda path: played.append(path.name),
        temp_parent=tmp_path,
        id_factory=lambda: "turn-1",
    )
    handle = runtime.begin_turn()
    reply_audio = tmp_path / "reply.wav"
    result = runtime.run_response(
        handle,
        user_text="测试",
        emotion=EmotionResult("neutral", 0.2, 0.8, {}),
        history=[],
        reply_audio=reply_audio,
        action={"emotion_state": {"label": "neutral"}},
    )
    runtime.close()

    assert result.strategy.reply_text == "第一句。第二句没有标点"
    assert [call[0] for call in tts.calls] == ["第一句。", "第二句没有标点"]
    assert len(played) == 2
    assert result.audio_chunks == 2
    assert result.strategy.action["expression"] == "neutral_smile"
    assert result.strategy.action["emotion_state"] == {"label": "neutral"}
    assert result.lip_sync["turn_id"] == "turn-1"
    assert all(frame["turn_id"] == "turn-1" for frame in result.lip_sync["frames"])
    assert reply_audio.exists()
    with wave.open(str(reply_audio), "rb") as wav_file:
        assert wav_file.getframerate() == 1000
        assert wav_file.getnframes() == 8

    event_types = [event.event_type for event in events]
    for required in [
        StreamEventType.TURN_STARTED,
        StreamEventType.LLM_REQUEST_STARTED,
        StreamEventType.LLM_TOKEN,
        StreamEventType.SENTENCE_READY,
        StreamEventType.TTS_STARTED,
        StreamEventType.AUDIO_CHUNK_READY,
        StreamEventType.PLAYBACK_STARTED,
        StreamEventType.PLAYBACK_FINISHED,
        StreamEventType.TURN_FINISHED,
    ]:
        assert required in event_types
    assert [event.sequence for event in events] == list(range(len(events)))
    assert result.latency.latencies_ms["turn_total_ms"] >= 0


def test_streaming_response_without_playback_has_no_playback_events_or_metrics(
    tmp_path,
) -> None:
    events = []
    played = []
    runtime = StreamingResponseRuntime(
        _cfg(tmp_path),
        FakeGenerator(["reply sentence."]),
        FakeTTS(),
        event_sink=events.append,
        player=lambda path: played.append(path),
        playback_enabled=False,
        temp_parent=tmp_path,
        id_factory=lambda: "turn-no-play",
    )
    handle = runtime.begin_turn()
    result = runtime.run_response(
        handle,
        user_text="test",
        emotion=EmotionResult("neutral", 0.2, 0.8, {}),
        history=[],
        reply_audio=tmp_path / "reply.wav",
    )
    runtime.close()

    event_types = [event.event_type for event in events]
    assert played == []
    assert StreamEventType.AUDIO_CHUNK_READY in event_types
    assert StreamEventType.PLAYBACK_STARTED not in event_types
    assert StreamEventType.PLAYBACK_FINISHED not in event_types
    assert "time_to_playback_ms" not in result.latency.latencies_ms
    assert "playback_duration_ms" not in result.latency.latencies_ms


def test_streaming_response_skips_trailing_whitespace_and_coalesces_short_fragments(
    tmp_path,
) -> None:
    events = []
    tts = FakeTTS()
    runtime = StreamingResponseRuntime(
        _cfg(tmp_path, stream_sentence_min_chars=12),
        FakeGenerator(["简短，", "但完整的回答。  \n", "  \n"]),
        tts,
        event_sink=events.append,
        playback_enabled=False,
        temp_parent=tmp_path,
        id_factory=lambda: "turn-clean-chunks",
    )
    handle = runtime.begin_turn()
    result = runtime.run_response(
        handle,
        user_text="test",
        emotion=EmotionResult("neutral", 0.2, 0.8, {}),
        history=[],
        reply_audio=tmp_path / "reply.wav",
    )
    runtime.close()

    assert result.strategy.reply_text == "简短，但完整的回答。  \n  \n"
    assert [call[0] for call in tts.calls] == ["简短，但完整的回答。"]
    sentence_events = [
        event for event in events if event.event_type == StreamEventType.SENTENCE_READY
    ]
    assert [event.payload["text"] for event in sentence_events] == [
        "简短，但完整的回答。"
    ]
    assert [event.payload["chunk_sequence"] for event in sentence_events] == [0]


def test_streaming_tts_failure_cancels_turn_without_a_final_result(tmp_path) -> None:
    events = []
    runtime = StreamingResponseRuntime(
        _cfg(tmp_path),
        FakeGenerator(["失败句子。"]),
        FakeTTS(fail=True),
        event_sink=events.append,
        player=lambda path: None,
        temp_parent=tmp_path,
        id_factory=lambda: "turn-fail",
    )
    handle = runtime.begin_turn()

    with pytest.raises(RuntimeError, match="Incremental TTS failed"):
        runtime.run_response(
            handle,
            user_text="测试",
            emotion=EmotionResult("neutral", 0.2, 0.8, {}),
            history=[],
            reply_audio=tmp_path / "reply.wav",
        )
    runtime.close()

    assert handle.cancelled.is_set()
    event_types = [event.event_type for event in events]
    assert StreamEventType.ERROR in event_types
    assert StreamEventType.TURN_CANCELLED in event_types
    assert StreamEventType.TURN_FINISHED not in event_types
    assert not (tmp_path / "reply.wav").exists()


def test_capture_streaming_microphone_emits_partial_then_one_final(tmp_path) -> None:
    pcm_frames = [
        struct.pack("<" + "h" * 10, *([1000] * 10)),
        struct.pack("<" + "h" * 10, *([1200] * 10)),
        struct.pack("<" + "h" * 10, *([0] * 10)),
        struct.pack("<" + "h" * 10, *([0] * 10)),
    ]

    class FakeRawInputStream:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]

        def start(self):
            status = types.SimpleNamespace(input_overflow=False)
            for pcm16 in pcm_frames:
                self.callback(pcm16, 10, {}, status)

        def stop(self):
            pass

        def close(self):
            pass

    class FakeASR:
        def __init__(self):
            self.calls = 0

        def transcribe(self, path):
            self.calls += 1
            return "部分文本" if self.calls == 1 else "最终文本"

    events = []
    cfg = _cfg(
        tmp_path,
        frame_ms=10,
        max_record_seconds=1.0,
        vad_threshold=0.01,
        min_speech_ms=10,
        silence_ms=20,
        pre_roll_ms=10,
        vad_calibration_ms=0,
        stream_audio_queue_size=8,
        stream_asr_partial_interval_seconds=0.05,
    )
    runtime = StreamingResponseRuntime(
        cfg,
        FakeGenerator(["unused"]),
        FakeTTS(),
        event_sink=events.append,
        player=lambda path: None,
        temp_parent=tmp_path,
        id_factory=lambda: "turn-mic",
    )
    handle = runtime.begin_turn()
    output_wav = tmp_path / "user_input.wav"
    text = capture_streaming_microphone(
        cfg,
        FakeASR(),
        runtime,
        handle,
        output_wav,
        sounddevice_module=types.SimpleNamespace(RawInputStream=FakeRawInputStream),
    )
    runtime.cancel_current(reason="test_complete")
    runtime.close()

    assert text == "最终文本"
    assert output_wav.exists()
    with wave.open(str(output_wav), "rb") as wav_file:
        assert wav_file.getnframes() == 40
    event_types = [event.event_type for event in events]
    assert event_types.count(StreamEventType.VAD_STARTED) == 1
    assert event_types.count(StreamEventType.VAD_STOPPED) == 1
    assert event_types.count(StreamEventType.ASR_PARTIAL) == 1
    assert event_types.count(StreamEventType.ASR_FINAL) == 1
    assert event_types.count(StreamEventType.AUDIO_FRAME) == 4


def test_main_streaming_turn_writes_memory_only_after_final_reply(tmp_path) -> None:
    import main as main_module

    cfg = _cfg(tmp_path, memory_file=tmp_path / "memory.json")
    runtime = StreamingResponseRuntime(
        cfg,
        FakeGenerator(["第一句。", "最终句。"]),
        FakeTTS(),
        player=lambda path: None,
        temp_parent=tmp_path,
        id_factory=lambda: "turn-main",
    )
    emotion = EmotionResult("neutral", 0.2, 0.8, {})
    memory_states = []

    class FakeSER:
        def predict(self, path):
            return emotion

    class FakeSmoother:
        def update(self, current_emotion):
            assert current_emotion is emotion
            return types.SimpleNamespace(
                label="neutral",
                valence=0.0,
                arousal=0.0,
                dominance=0.0,
                to_dict=lambda: {"label": "neutral"},
            )

    class FakeMemory:
        def load(self):
            assert memory_states == []
            return []

        def append(self, state):
            memory_states.append(state)

    fsm = DialogueStateMachine()
    fsm.set(DialogueStage.IDLE)
    args = argparse.Namespace(
        mode="console",
        audio="",
        text="最终识别文本",
        record_seconds=1.0,
        no_play=True,
        continuous=False,
        streaming=True,
    )
    paths = {
        "user_audio": tmp_path / "user_input.wav",
        "reply_audio": tmp_path / "reply.wav",
        "action_file": tmp_path / "last_action.json",
        "serial_packet_file": tmp_path / "serial_packet.json",
        "state_file": tmp_path / "last_state.json",
        "metrics_file": tmp_path / "streaming_metrics.json",
        "events_file": tmp_path / "streaming_events.json",
    }

    main_module.run_one_streaming_turn(
        args=args,
        cfg=cfg,
        continuous=False,
        fsm=fsm,
        ser=FakeSER(),
        asr=None,
        runtime=runtime,
        smoother=FakeSmoother(),
        memory=FakeMemory(),
        **paths,
    )
    runtime.close()

    assert len(memory_states) == 1
    state = memory_states[0]
    assert state.user_text == "最终识别文本"
    assert state.reply_text == "第一句。最终句。"
    assert fsm.stage == DialogueStage.IDLE
    for path in paths.values():
        assert path.exists()
    events = json.loads(paths["events_file"].read_text(encoding="utf-8"))
    assert sum(event["event_type"] == "asr_final" for event in events) == 1
    assert sum(event["event_type"] == "asr_partial" for event in events) == 0
    metrics = json.loads(paths["metrics_file"].read_text(encoding="utf-8"))
    assert metrics["last_turn"]["turn_id"] == "turn-main"


def test_continuous_streaming_failure_persists_current_diagnostics_and_returns(
    tmp_path,
) -> None:
    import main as main_module

    cfg = _cfg(tmp_path, memory_file=tmp_path / "memory.json")
    runtime = StreamingResponseRuntime(
        cfg,
        FakeGenerator(["失败句子。"]),
        FakeTTS(fail=True),
        playback_enabled=False,
        temp_parent=tmp_path,
        id_factory=lambda: "turn-current-failure",
    )

    class FakeSER:
        def predict(self, path):
            return EmotionResult("neutral", 0.2, 0.8, {})

    class FakeSmoother:
        def update(self, emotion):
            return types.SimpleNamespace(
                label="neutral",
                valence=0.0,
                arousal=0.0,
                dominance=0.0,
                to_dict=lambda: {"label": "neutral"},
            )

    class FakeMemory:
        def load(self):
            return []

        def append(self, state):
            raise AssertionError("failed turns must not be stored in memory")

    fsm = DialogueStateMachine()
    fsm.set(DialogueStage.IDLE)
    paths = {
        "user_audio": tmp_path / "user_input.wav",
        "reply_audio": tmp_path / "reply.wav",
        "action_file": tmp_path / "last_action.json",
        "serial_packet_file": tmp_path / "serial_packet.json",
        "state_file": tmp_path / "last_state.json",
        "metrics_file": tmp_path / "streaming_metrics.json",
        "events_file": tmp_path / "streaming_events.json",
    }

    main_module.run_one_streaming_turn(
        args=argparse.Namespace(
            mode="console",
            audio="",
            text="trigger failure",
            record_seconds=1.0,
            no_play=True,
            continuous=True,
            streaming=True,
        ),
        cfg=cfg,
        continuous=True,
        fsm=fsm,
        ser=FakeSER(),
        asr=None,
        runtime=runtime,
        smoother=FakeSmoother(),
        memory=FakeMemory(),
        **paths,
    )
    runtime.close()

    metrics = json.loads(paths["metrics_file"].read_text(encoding="utf-8"))
    events = json.loads(paths["events_file"].read_text(encoding="utf-8"))
    assert metrics["failed"] is True
    assert metrics["last_turn"]["turn_id"] == "turn-current-failure"
    assert "tts failed" in metrics["error"]["message"]
    assert events[-1]["event_type"] == "turn_cancelled"
    assert any(event["event_type"] == "error" for event in events)
    assert fsm.stage == DialogueStage.IDLE
    assert not paths["reply_audio"].exists()


def test_one_hundred_turn_stress_keeps_one_worker_and_bounded_queues(tmp_path) -> None:
    turn_number = 0

    def next_turn_id():
        nonlocal turn_number
        turn_number += 1
        return f"turn-{turn_number}"

    cfg = _cfg(tmp_path, stream_tts_queue_size=2)
    runtime = StreamingResponseRuntime(
        cfg,
        FakeGenerator(["稳定回答。"]),
        FakeTTS(),
        player=lambda path: None,
        temp_parent=tmp_path,
        id_factory=next_turn_id,
    )
    emotion = EmotionResult("neutral", 0.2, 0.8, {})
    for index in range(100):
        handle = runtime.begin_turn()
        result = runtime.run_response(
            handle,
            user_text=f"问题 {index}",
            emotion=emotion,
            history=[],
            reply_audio=tmp_path / "reply.wav",
        )
        assert result.strategy.reply_text == "稳定回答。"
        assert runtime.controller.active_turn_id is None

    assert runtime.worker_alive is True
    assert runtime.tts_queue_capacity == 2
    assert runtime.tts_failures == ()
    assert runtime.controller.latency_summary()["turn_total_ms"]["count"] == 100
    runtime.close()
    assert runtime.worker_alive is False
