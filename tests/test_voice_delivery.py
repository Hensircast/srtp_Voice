"""Delivery-batch tests: two-stage TTS, cancel safety and bounded prefetch."""

from __future__ import annotations

import struct
import threading
import time
import wave
from pathlib import Path

from srtp_voice.streaming import TextChunk
from srtp_voice.streaming_tts import IncrementalTTSPlayer


def _write_wav(path: Path, frames: int = 4) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(struct.pack("<" + "h" * frames, *([100] * frames)))


class _RecordingSynth:
    def __init__(self, *, on_synthesize=None) -> None:
        self.texts: list[str] = []
        self.paths: list[Path] = []
        self._on_synthesize = on_synthesize

    def synthesize(self, text: str, out_wav: Path) -> None:
        self.texts.append(text)
        self.paths.append(Path(out_wav))
        _write_wav(out_wav)
        if self._on_synthesize is not None:
            self._on_synthesize(text)


def test_synthesis_overlaps_playback_and_prefetch_is_bounded(tmp_path) -> None:
    playing = threading.Event()
    second_ready = threading.Event()
    third_started = threading.Event()
    release = threading.Event()
    events: list[str] = []

    def note(text: str) -> None:
        if text == "第二句。":
            second_ready.set()
        if text == "第三句。":
            third_started.set()

    def player(path: Path) -> None:
        events.append(f"play:{path.name}")
        playing.set()
        assert release.wait(4)

    worker = IncrementalTTSPlayer(
        _RecordingSynth(on_synthesize=note),
        queue_maxsize=4,
        temp_parent=tmp_path,
        player=player,
        on_audio_ready=lambda result: events.append(f"ready:{result.sequence}"),
        on_playback_started=lambda result: events.append(f"started:{result.sequence}"),
        on_playback_finished=lambda result: events.append(f"finished:{result.sequence}"),
    )
    worker.start()
    try:
        assert worker.submit(TextChunk("第一句。", turn_id="t", sequence_id=0))
        assert playing.wait(2)
        assert worker.submit(TextChunk("第二句。", turn_id="t", sequence_id=1))
        assert second_ready.wait(2), "the next sentence was not synthesized during playback"
        assert worker.submit(TextChunk("第三句。", turn_id="t", sequence_id=2))
        # Bounded look-ahead: while the player stays blocked the pipeline must
        # not synthesize the whole backlog, so the third sentence cannot run
        # without limit even though one prepared chunk is allowed.
        assert not third_started.wait(0.3), "prepared audio prefetch is unbounded"
    finally:
        release.set()
        worker.join()
        worker.close()

    assert events[0].startswith("ready:")
    assert "started:0" in events
    assert events.count("started:0") == 1
    assert worker.failures == ()
    assert not list(tmp_path.rglob("chunk-*.wav"))


def test_no_playback_mode_emits_audio_ready_without_play_events(tmp_path) -> None:
    ready: list[int] = []
    playback: list[str] = []
    worker = IncrementalTTSPlayer(
        _RecordingSynth(),
        temp_parent=tmp_path,
        playback_enabled=False,
        on_audio_ready=lambda result: ready.append(result.sequence),
        on_playback_started=lambda result: playback.append("started"),
        on_playback_finished=lambda result: playback.append("finished"),
    )
    worker.start()
    try:
        assert worker.submit(TextChunk("只有合成。", turn_id="quiet", sequence_id=0))
        worker.join()
    finally:
        worker.close()

    assert ready == [0]
    assert playback == []
    assert worker.failures == ()
    assert not list(tmp_path.rglob("chunk-*.wav"))


def test_cancel_stops_old_turn_and_new_turn_plays(tmp_path) -> None:
    playing = threading.Event()
    release = threading.Event()
    finished: list[str] = []
    played: list[str] = []

    class Player:
        def __call__(self, path: Path) -> None:
            played.append(path.name)
            if len(played) == 1:
                playing.set()
                assert release.wait(4)

        def stop(self) -> None:
            release.set()

    worker = IncrementalTTSPlayer(
        _RecordingSynth(),
        temp_parent=tmp_path,
        player=Player(),
        on_playback_finished=lambda result: finished.append(result.turn_id),
    )
    worker.start()
    try:
        assert worker.submit(TextChunk("旧句子。", turn_id="old", sequence_id=0))
        assert playing.wait(2)
        worker.cancel_turn("old")
        assert worker.submit(TextChunk("新句子。", turn_id="new", sequence_id=0), block=True)
        worker.join()
    finally:
        release.set()
        worker.close(drain=False)

    assert finished == ["new"]
    assert len(played) == 2
    assert not list(tmp_path.rglob("chunk-*.wav"))


def test_close_releases_blocked_producer_without_deadlock(tmp_path) -> None:
    playing = threading.Event()
    release = threading.Event()
    producer_done = threading.Event()
    closed = threading.Event()
    failures: list[BaseException] = []

    class Player:
        def __call__(self, path: Path) -> None:
            playing.set()
            assert release.wait(5)

        def stop(self) -> None:
            release.set()

    worker = IncrementalTTSPlayer(
        _RecordingSynth(), queue_maxsize=1, temp_parent=tmp_path, player=Player()
    )
    worker.start()

    def produce() -> None:
        try:
            for sequence in range(50):
                worker.submit(
                    TextChunk("等待关闭。", turn_id="closing", sequence_id=sequence),
                    block=True,
                )
        except RuntimeError:
            pass
        except BaseException as error:  # noqa: BLE001 - surfaced by the assertion
            failures.append(error)
        finally:
            producer_done.set()

    producer = threading.Thread(target=produce)
    try:
        producer.start()
        assert playing.wait(2)
        start = time.monotonic()
        worker.close(drain=False)
        assert time.monotonic() - start < 5, "close blocked while a producer was waiting"
        closed.set()
        assert producer_done.wait(2), "blocked producer survived worker close"
    finally:
        release.set()
        producer.join(4)
        worker.close(drain=False)

    assert closed.is_set()
    assert failures == []
    assert not producer.is_alive()
    assert not worker.is_alive
    assert not list(tmp_path.rglob("chunk-*.wav"))
