"""Independent PR #25 owned-resource shutdown regressions (no models)."""

import threading

import pytest

from srtp_voice.config import AppConfig
from srtp_voice.streaming import TextChunk
from srtp_voice.streaming_runtime import StreamingResponseRuntime


def test_blocked_owned_synthesis_does_not_extend_worker_close_timeout(tmp_path, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    returned = threading.Event()
    lock = threading.Lock()
    attempts = []
    errors = []

    class Owned:
        def synthesize(self, text, path):
            with lock:
                started.set()
                assert release.wait(3)

        def close(self):
            attempts.append(True)
            with lock:
                pass

    owned = Owned()

    class Caller:
        def for_streaming(self):
            return owned

    runtime = StreamingResponseRuntime(
        AppConfig(), object(), Caller(), temp_parent=tmp_path, playback_enabled=False,
    )
    worker_close = runtime._tts_worker.close
    monkeypatch.setattr(runtime._tts_worker, "close", lambda *, drain: worker_close(drain=drain, timeout=0.1))
    runtime._tts_worker.submit(TextChunk("受控阻塞。", turn_id="blocked"))

    def close_runtime():
        try:
            runtime.close()
        except Exception as error:
            errors.append(error)
        finally:
            returned.set()

    closer = threading.Thread(target=close_runtime)
    try:
        assert started.wait(1)
        closer.start()
        assert returned.wait(1), "runtime shutdown blocked on the live synthesizer's resource lock"
        assert len(errors) == 1 and isinstance(errors[0], TimeoutError)
        assert attempts == []
        assert runtime.worker_alive
        with pytest.raises(RuntimeError, match="closed"):
            runtime.begin_turn()
    finally:
        release.set()
        if closer.ident is not None:
            closer.join(4)
        monkeypatch.setattr(runtime._tts_worker, "close", worker_close)
        runtime.close()
    assert not closer.is_alive()
    assert attempts == [True]
    assert not runtime.worker_alive
    runtime.close()
    assert attempts == [True]


@pytest.mark.parametrize("finished_before_retry", [False, True])
def test_runtime_retry_preserves_then_releases_bare_owned_session(monkeypatch, finished_before_retry):
    import srtp_voice.streaming_runtime as module

    closed = []

    class OwnedSession:
        def close(self):
            closed.append(True)

    owned = OwnedSession()

    class Caller:
        def for_streaming(self):
            return owned

    class Worker:
        def __init__(self, *args, **kwargs):
            self.is_alive = True
            self.calls = 0

        def start(self):
            pass

        def close(self, *, drain):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("still running")
            self.is_alive = False

    monkeypatch.setattr(module, "IncrementalTTSPlayer", Worker)
    runtime = StreamingResponseRuntime(AppConfig(), object(), Caller())
    with pytest.raises(TimeoutError, match="still running"):
        runtime.close()
    assert closed == []
    assert runtime._owned_streaming_resource is owned
    with pytest.raises(RuntimeError, match="closed"):
        runtime.begin_turn()
    if finished_before_retry:
        runtime._tts_worker.is_alive = False
    runtime.close()
    assert closed == [True]
    assert runtime._owned_streaming_resource is None
    runtime.close()
    assert closed == [True]


def test_shutdown_error_after_workers_stop_still_releases_resource(monkeypatch):
    import srtp_voice.streaming_runtime as module

    closed = []

    class Owned:
        def close(self):
            closed.append(True)

    class Caller:
        def for_streaming(self):
            return Owned()

    class Worker:
        def __init__(self, *args, **kwargs):
            self.is_alive = False

        def start(self):
            pass

        def close(self, *, drain):
            raise OSError("post-stop cleanup failed")

    monkeypatch.setattr(module, "IncrementalTTSPlayer", Worker)
    runtime = StreamingResponseRuntime(AppConfig(), object(), Caller())
    with pytest.raises(OSError, match="cleanup failed"):
        runtime.close()
    assert closed == [True]
    runtime.close()
    assert closed == [True]
