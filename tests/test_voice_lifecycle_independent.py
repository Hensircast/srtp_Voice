"""Independent cancellation and lifecycle stressors for the two-stage player."""

import struct
import threading
import wave

import pytest

from srtp_voice.streaming import TextChunk
from srtp_voice.streaming_tts import IncrementalTTSPlayer


def _wav(path):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(struct.pack("<hh", 100, 200))


def test_cancel_during_synthesis_releases_slot_for_next_turn(tmp_path):
    next_played = threading.Event()
    worker = None

    class Synth:
        def synthesize(self, text, path):
            _wav(path)
            if text == "取消旧轮。":
                worker.cancel_turn("old")

    worker = IncrementalTTSPlayer(Synth(), temp_parent=tmp_path, player=lambda path: next_played.set())
    worker.start()
    try:
        assert worker.submit(TextChunk("取消旧轮。", turn_id="old"))
        assert worker.submit(TextChunk("新轮。", turn_id="new"))
        assert next_played.wait(1), "cancelled synthesis leaked its prefetch slot"
        worker.join()
    finally:
        worker.close(drain=False)
    assert worker.failures == ()


def test_drain_close_rejects_new_submissions_while_finishing_old_work(tmp_path):
    playing = threading.Event()
    release = threading.Event()
    closing = threading.Event()
    errors = []

    class Synth:
        def synthesize(self, text, path):
            _wav(path)

    def player(path):
        playing.set()
        assert release.wait(3)

    worker = IncrementalTTSPlayer(Synth(), temp_parent=tmp_path, player=player)
    worker.start()
    worker.submit(TextChunk("已接受。", turn_id="drain"))

    def close():
        closing.set()
        try:
            worker.close(drain=True)
        except Exception as error:
            errors.append(error)

    closer = threading.Thread(target=close)
    try:
        assert playing.wait(1)
        closer.start()
        assert closing.wait(1)
        # Observe the closing state without imposing scheduling on the closer.
        for _ in range(100):
            try:
                worker.submit(TextChunk("新提交。", turn_id="late"))
            except RuntimeError:
                break
            threading.Event().wait(0.01)
        else:
            pytest.fail("draining close still accepts new work")
    finally:
        release.set()
        closer.join(4)
        worker.close(drain=False)
    assert not closer.is_alive()
    assert errors == []


def test_unstarted_close_leaves_no_unfinished_queue_work(tmp_path):
    worker = IncrementalTTSPlayer(object(), temp_parent=tmp_path)
    worker.submit(TextChunk("尚未启动。", turn_id="not-started"))
    worker.close(drain=False)
    assert worker._queue.unfinished_tasks == 0
    assert worker._prepared.unfinished_tasks == 0
    worker.join()
    assert not worker.is_alive


def test_callback_failure_is_reported_and_next_turn_can_play(tmp_path):
    first_finished = threading.Event()
    next_finished = threading.Event()

    class Synth:
        def synthesize(self, text, path):
            _wav(path)

    def finished(result):
        if result.turn_id == "bad-callback":
            first_finished.set()
            raise ValueError("completion callback failed")
        next_finished.set()

    worker = IncrementalTTSPlayer(Synth(), temp_parent=tmp_path, player=lambda path: None, on_playback_finished=finished)
    worker.start()
    try:
        worker.submit(TextChunk("旧轮。", turn_id="bad-callback"))
        assert first_finished.wait(1)
        worker.submit(TextChunk("新轮。", turn_id="new"))
        assert next_finished.wait(1), "completion callback killed the persistent playback worker"
        worker.join()
    finally:
        worker.close(drain=False)
    assert any("completion callback failed" in str(f.error) for f in worker.failures)


def test_second_thread_start_failure_reclaims_first_thread(tmp_path, monkeypatch):
    started = []
    original = threading.Thread.start

    def start(thread):
        if thread.name == "srtp-streaming-tts-play":
            raise OSError("playback thread could not start")
        original(thread)
        if thread.name == "srtp-streaming-tts-synth":
            started.append(thread)

    monkeypatch.setattr(threading.Thread, "start", start)
    worker = IncrementalTTSPlayer(object(), temp_parent=tmp_path)
    with pytest.raises(OSError, match="playback thread"):
        worker.start()
    assert started and all(not thread.is_alive() for thread in started)
    assert not worker.is_alive
    assert not worker._temp_path.exists()


def test_close_timeout_is_explicit_and_live_state_is_retained_until_retry(tmp_path):
    playing = threading.Event()
    release = threading.Event()

    class Synth:
        def synthesize(self, text, path):
            _wav(path)

    def player(path):
        playing.set()
        assert release.wait(3)

    worker = IncrementalTTSPlayer(Synth(), temp_parent=tmp_path, player=player)
    worker.start()
    try:
        worker.submit(TextChunk("无法立即停止的外部播放器。", turn_id="timeout"))
        assert playing.wait(1)
        with pytest.raises(TimeoutError, match="shutdown timeout"):
            worker.close(drain=False, timeout=0.1)
        assert worker.is_alive
        assert worker.close_timed_out
        assert worker._temp_path.exists()
        assert worker._active_turn_id == "timeout"
    finally:
        release.set()
        worker.close(drain=False, timeout=2)
    assert not worker.is_alive
    assert not worker.close_timed_out
    assert not worker._temp_path.exists()


def test_piper_reader_start_failure_reaps_child(tmp_path, monkeypatch):
    import subprocess
    import sys
    from pathlib import Path
    from srtp_voice.config import AppConfig
    from srtp_voice.piper_session import PersistentPiperSession

    model = tmp_path / "fake-model.onnx"
    model.write_bytes(b"fake")
    processes = []
    original = threading.Thread.start

    def popen(*args, **kwargs):
        process = subprocess.Popen(*args, **kwargs)
        processes.append(process)
        return process

    def start(thread):
        if thread.name == "srtp-piper-stderr":
            raise RuntimeError("stderr reader could not start")
        original(thread)

    monkeypatch.setattr(threading.Thread, "start", start)
    session = PersistentPiperSession(
        AppConfig(tts_piper_exe=Path(sys.executable), tts_piper_model=model), popen=popen,
        command_prefix=[sys.executable, "-c", "import sys; sys.stdin.read()"],
    )
    try:
        with pytest.raises(RuntimeError, match="stderr reader"):
            session.start()
        assert processes and all(process.poll() is not None for process in processes)
        assert session._thread is None
        assert session._stderr_thread is None
        assert not session.started
    finally:
        session.close()
