from __future__ import annotations

import struct
import threading
import wave

from srtp_voice.streaming import TextChunk
from srtp_voice.streaming_tts import IncrementalTTSPlayer


def _write_wav(path, value: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(struct.pack("<hhhh", value, value, value, value))


class FakeSynthesizer:
    def __init__(self, *, fail_text=None, after_synthesize=None):
        self.calls = []
        self.fail_text = fail_text
        self.after_synthesize = after_synthesize

    def synthesize(self, text, out_wav):
        self.calls.append((text, out_wav))
        if text == self.fail_text:
            raise RuntimeError("synthetic failure")
        _write_wav(out_wav, len(self.calls))
        if self.after_synthesize is not None:
            self.after_synthesize(text)


def test_incremental_tts_synthesizes_and_plays_in_sentence_order(tmp_path) -> None:
    synthesizer = FakeSynthesizer()
    ready = []
    playback = []
    playback_events = []
    paths = []

    def on_ready(result):
        ready.append((result.turn_id, result.sequence, result.text))
        paths.append(result.wav_path)
        assert result.wav_path.exists()
        assert result.audio.turn_id == result.turn_id
        assert result.audio.sequence_id == result.sequence
        assert result.audio.pcm16
        assert result.lip_sync["turn_id"] == result.turn_id
        assert result.lip_sync["chunk_sequence"] == result.sequence
        assert all(
            frame["turn_id"] == result.turn_id
            and frame["chunk_sequence"] == result.sequence
            and frame["sequence"] == index
            for index, frame in enumerate(result.lip_sync["frames"])
        )

    worker = IncrementalTTSPlayer(
        synthesizer,
        queue_maxsize=4,
        temp_parent=tmp_path,
        player=lambda path: (playback_events.append("play"), playback.append(path.name)),
        on_audio_ready=on_ready,
        on_playback_started=lambda result: playback_events.append(
            f"start-{result.sequence}"
        ),
        on_playback_finished=lambda result: playback_events.append(
            f"finish-{result.sequence}"
        ),
    )
    worker.start()
    for sequence, text in enumerate(["第一句。", "第二句。", "第三句。"]):
        assert worker.submit(TextChunk(text, turn_id="turn-1", sequence_id=sequence))
    worker.join()
    worker.close()

    assert [call[0] for call in synthesizer.calls] == ["第一句。", "第二句。", "第三句。"]
    assert ready == [
        ("turn-1", 0, "第一句。"),
        ("turn-1", 1, "第二句。"),
        ("turn-1", 2, "第三句。"),
    ]
    assert playback == ["chunk-00000000.wav", "chunk-00000001.wav", "chunk-00000002.wav"]
    assert playback_events == [
        "start-0",
        "play",
        "finish-0",
        "start-1",
        "play",
        "finish-1",
        "start-2",
        "play",
        "finish-2",
    ]
    assert all(not path.exists() for path in paths)
    assert worker.failures == ()
    assert worker.is_alive is False


def test_incremental_tts_queue_is_bounded_and_reports_drops(tmp_path) -> None:
    worker = IncrementalTTSPlayer(
        FakeSynthesizer(),
        queue_maxsize=1,
        temp_parent=tmp_path,
    )

    assert worker.queue_capacity == 1
    assert worker.submit(TextChunk("first", turn_id="turn-1", sequence_id=0)) is True
    assert worker.submit(TextChunk("second", turn_id="turn-1", sequence_id=1)) is False
    assert worker.dropped_chunks == 1
    worker.close(drain=False)


def test_cancelled_turn_pending_chunks_are_not_synthesized_or_played(tmp_path) -> None:
    synthesizer = FakeSynthesizer()
    played = []
    worker = IncrementalTTSPlayer(
        synthesizer,
        queue_maxsize=4,
        temp_parent=tmp_path,
        player=lambda path: played.append(path.name),
    )
    assert worker.submit(TextChunk("old-1", turn_id="old", sequence_id=0))
    assert worker.submit(TextChunk("old-2", turn_id="old", sequence_id=1))
    assert worker.submit(TextChunk("new", turn_id="new", sequence_id=0))
    worker.cancel_turn("old")

    worker.start()
    worker.join()
    worker.close()

    assert [call[0] for call in synthesizer.calls] == ["new"]
    assert played == ["chunk-00000000.wav"]


def test_cancellation_after_synthesis_skips_playback_and_cleans_file(tmp_path) -> None:
    played = []
    worker = None

    def cancel_after_synthesis(text):
        assert worker is not None
        worker.cancel_turn("turn-1")

    synthesizer = FakeSynthesizer(after_synthesize=cancel_after_synthesis)
    worker = IncrementalTTSPlayer(
        synthesizer,
        temp_parent=tmp_path,
        player=lambda path: played.append(path),
    )
    worker.start()
    assert worker.submit(TextChunk("cancel me", turn_id="turn-1", sequence_id=0))
    worker.join()
    generated_path = synthesizer.calls[0][1]
    worker.close()

    assert played == []
    assert not generated_path.exists()


def test_worker_records_failure_and_continues_with_next_chunk(tmp_path) -> None:
    synthesizer = FakeSynthesizer(fail_text="bad")
    played = []
    errors = []
    worker = IncrementalTTSPlayer(
        synthesizer,
        temp_parent=tmp_path,
        player=lambda path: played.append(path.name),
        on_error=errors.append,
    )
    worker.start()
    assert worker.submit(TextChunk("bad", turn_id="turn-1", sequence_id=0))
    assert worker.submit(TextChunk("good", turn_id="turn-1", sequence_id=1))
    worker.join()
    worker.close()

    assert len(errors) == 1
    assert errors[0].turn_id == "turn-1"
    assert errors[0].sequence == 0
    assert "synthetic failure" in str(errors[0].error)
    assert len(worker.failures) == 1
    assert played == ["chunk-00000001.wav"]


def test_cancel_turn_stops_active_cancellable_playback(tmp_path) -> None:
    class BlockingPlayer:
        def __init__(self):
            self.started = threading.Event()
            self.released = threading.Event()
            self.stop_calls = 0

        def __call__(self, path):
            self.started.set()
            assert self.released.wait(timeout=2)

        def stop(self):
            self.stop_calls += 1
            self.released.set()

    player = BlockingPlayer()
    finished = []
    worker = IncrementalTTSPlayer(
        FakeSynthesizer(),
        temp_parent=tmp_path,
        player=player,
        on_playback_finished=finished.append,
    )
    worker.start()
    assert worker.submit(TextChunk("playing", turn_id="turn-1", sequence_id=0))
    assert player.started.wait(timeout=2)

    worker.cancel_turn("turn-1")
    worker.join()
    worker.close()

    assert player.stop_calls == 1
    assert finished == []


def test_playback_disabled_still_synthesizes_without_playback_events(tmp_path) -> None:
    played = []
    started = []
    finished = []
    worker = IncrementalTTSPlayer(
        FakeSynthesizer(),
        temp_parent=tmp_path,
        player=lambda path: played.append(path),
        playback_enabled=False,
        on_playback_started=started.append,
        on_playback_finished=finished.append,
    )
    worker.start()
    assert worker.submit(TextChunk("silent output", turn_id="turn-1", sequence_id=0))
    worker.join()
    worker.close()

    assert played == []
    assert started == []
    assert finished == []
