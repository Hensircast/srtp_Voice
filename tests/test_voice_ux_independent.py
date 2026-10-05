"""Independent behavioral checks for latency and natural streaming delivery."""

from __future__ import annotations

import json
import struct
import threading
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer

from srtp_voice.config import AppConfig
from srtp_voice.llm import StrategyGenerator
from srtp_voice.streaming import StreamEvent, StreamEventType, TextChunk, TurnTiming
from srtp_voice.streaming_tts import IncrementalTTSPlayer
from srtp_voice.types import EmotionResult
from srtp_voice.streaming_runtime import StreamingResponseRuntime


def test_runtime_uses_factory_result_and_keeps_original_adapter_available(tmp_path):
    calls = []
    closed = []

    class StreamingAdapter:
        def synthesize(self, text, path):
            calls.append(text)
            _write_wav(path)

        def close(self):
            closed.append("owned")

    class OriginalAdapter:
        def for_streaming(self):
            return StreamingAdapter()

        def synthesize(self, text, path):
            raise AssertionError("runtime used original adapter instead of streaming factory result")

        def close(self):
            closed.append("original")

    class Generator:
        def generate_stream(self, *args, turn_id):
            yield TextChunk("核心回答。", turn_id=turn_id)
            yield TextChunk("", turn_id=turn_id, is_final=True, sequence_id=1)

        def default_stream_action(self):
            return {}

    runtime = StreamingResponseRuntime(
        AppConfig(), Generator(), OriginalAdapter(), temp_parent=tmp_path, playback_enabled=False,
    )
    try:
        result = runtime.run_response(
            runtime.begin_turn(), user_text="建议？", emotion=EmotionResult("neutral", 0.2, 0.8, {}),
            history=[], reply_audio=tmp_path / "combined.wav",
        )
        assert result.audio_chunks == 1
        assert calls == ["核心回答。"]
    finally:
        runtime.close()
    assert closed == ["owned"]


def test_runtime_initialization_failure_closes_factory_resource(tmp_path, monkeypatch):
    import pytest
    import srtp_voice.streaming_runtime as runtime_module

    closed = []

    class Resource:
        def close(self):
            closed.append(True)

    class Adapter:
        def for_streaming(self):
            return Resource()

    class FailingWorker:
        def __init__(self, *args, **kwargs):
            raise OSError("temporary workspace unavailable")

    monkeypatch.setattr(runtime_module, "IncrementalTTSPlayer", FailingWorker)
    with pytest.raises(OSError, match="temporary workspace unavailable"):
        StreamingResponseRuntime(AppConfig(), object(), Adapter(), temp_parent=tmp_path)
    assert closed == [True]


def test_runtime_factory_failure_does_not_close_callers_shared_adapter(tmp_path):
    import pytest

    closed = []

    class SharedAdapter:
        def for_streaming(self):
            raise OSError("factory could not allocate its own resource")

        def close(self):
            closed.append(True)

    with pytest.raises(OSError, match="factory could not allocate"):
        StreamingResponseRuntime(AppConfig(), object(), SharedAdapter(), temp_parent=tmp_path)
    assert closed == []


def _write_wav(path):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(struct.pack("<hhhh", 100, 200, 300, 400))


def test_next_sentence_synthesis_overlaps_current_playback(tmp_path):
    first_playing = threading.Event()
    release_playback = threading.Event()
    second_synthesized = threading.Event()
    played = []

    class Synthesizer:
        def synthesize(self, text, out_wav):
            _write_wav(out_wav)
            if text == "第二句。":
                second_synthesized.set()

    def player(path):
        played.append(path.name)
        if len(played) == 1:
            first_playing.set()
            assert release_playback.wait(3)

    worker = IncrementalTTSPlayer(Synthesizer(), temp_parent=tmp_path, player=player)
    worker.start()
    try:
        assert worker.submit(TextChunk("第一句。", turn_id="same", sequence_id=0))
        assert first_playing.wait(2)
        assert worker.submit(TextChunk("第二句。", turn_id="same", sequence_id=1))
        assert second_synthesized.wait(1), "next synthesis waits for playback to finish"
    finally:
        release_playback.set()
        worker.join()
        worker.close()
    assert played == ["chunk-00000000.wav", "chunk-00000001.wav"]
    assert worker.failures == ()
    assert not worker.is_alive


def test_cancel_discards_prepared_old_audio_and_new_turn_still_plays(tmp_path):
    playing = threading.Event()
    prepared = threading.Event()
    release = threading.Event()
    played = []
    finished = []

    class Synthesizer:
        def synthesize(self, text, out_wav):
            _write_wav(out_wav)

    class Player:
        def __call__(self, path):
            played.append(path.name)
            if len(played) == 1:
                playing.set()
                assert release.wait(3)

        def stop(self):
            release.set()

    def ready(result):
        if result.turn_id == "old" and result.sequence == 1:
            prepared.set()

    worker = IncrementalTTSPlayer(
        Synthesizer(), temp_parent=tmp_path, player=Player(),
        on_audio_ready=ready, on_playback_finished=lambda result: finished.append(result.turn_id),
    )
    worker.start()
    try:
        assert worker.submit(TextChunk("旧第一句。", turn_id="old", sequence_id=0))
        assert playing.wait(2)
        assert worker.submit(TextChunk("旧第二句。", turn_id="old", sequence_id=1))
        assert prepared.wait(1), "audio is not prepared during current playback"
        worker.cancel_turn("old")
        assert worker.submit(TextChunk("新回答。", turn_id="new", sequence_id=0), block=True)
        worker.join()
    finally:
        release.set()
        worker.close(drain=False)
    assert played == ["chunk-00000000.wav", "chunk-00000002.wav"]
    assert finished == ["new"]
    assert not list(tmp_path.rglob("chunk-*.wav"))


def test_playback_duration_reaches_last_sentence_not_first_sentence():
    timing = TurnTiming("multi")
    observations = [
        (StreamEventType.TURN_STARTED, 10.0),
        (StreamEventType.PLAYBACK_STARTED, 11.0),
        (StreamEventType.PLAYBACK_FINISHED, 12.0),
        (StreamEventType.PLAYBACK_STARTED, 12.2),
        (StreamEventType.PLAYBACK_FINISHED, 15.0),
        (StreamEventType.TURN_FINISHED, 15.1),
    ]
    for sequence, (kind, timestamp) in enumerate(observations):
        timing.observe(StreamEvent(kind, "multi", sequence, timestamp))
    snapshot = timing.snapshot()
    assert snapshot.marks["playback_started"] == 11.0
    assert snapshot.marks["playback_finished"] == 15.0
    assert snapshot.latencies_ms["playback_duration_ms"] == 4000.0
    assert snapshot.latencies_ms["time_to_playback_ms"] == 1000.0


def test_http_stream_delivers_first_token_before_server_sends_done():
    """Actual loopback HTTP transport: no model, no text from user data."""
    first = (json.dumps({"message": {"content": "可以。"}, "done": False}) + "\n").encode()
    final = (json.dumps({"message": {"content": ""}, "done": True}) + "\n").encode()
    release_done = threading.Event()
    first_received = threading.Event()
    values = []
    errors = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Content-Length", str(len(first) + len(final)))
            self.end_headers()
            self.wfile.write(first)
            self.wfile.flush()
            if release_done.wait(3):
                self.wfile.write(final)
                self.wfile.flush()

    server = HTTPServer(("127.0.0.1", 0), Handler)
    serve = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01))
    serve.start()
    cfg = AppConfig(
        llm_ollama_chat_url=f"http://127.0.0.1:{server.server_port}/chat",
        llm_timeout_seconds=3,
    )
    generator = StrategyGenerator(cfg)
    stream = generator.generate_stream("有什么建议？", EmotionResult("neutral", 0.2, 0.8, {}), [])

    def consume_first():
        try:
            values.append(next(stream).text)
            first_received.set()
        except Exception as error:
            errors.append(error)

    consumer = threading.Thread(target=consume_first)
    consumer.start()
    try:
        assert first_received.wait(1), "HTTP reader buffers token until remaining data arrives"
        assert values == ["可以。"]
        assert errors == []
    finally:
        release_done.set()
        consumer.join(4)
        stream.close()
        close = getattr(generator, "close", None)
        if callable(close):
            close()
        server.shutdown()
        server.server_close()
        serve.join(2)
    assert not consumer.is_alive()
    assert not serve.is_alive()


def test_prefetch_is_bounded_while_player_is_blocked(tmp_path):
    playing = threading.Event()
    next_ready = threading.Event()
    fourth_started = threading.Event()
    release = threading.Event()
    calls = []

    class Synthesizer:
        def synthesize(self, text, path):
            calls.append(text)
            if len(calls) == 2:
                next_ready.set()
            if len(calls) == 4:
                fourth_started.set()
            _write_wav(path)

    def player(path):
        playing.set()
        assert release.wait(3)

    worker = IncrementalTTSPlayer(Synthesizer(), queue_maxsize=4, temp_parent=tmp_path, player=player)
    worker.start()
    try:
        assert worker.submit(TextChunk("第一句。", turn_id="bounded"))
        assert playing.wait(2)
        for sequence in range(1, 5):
            assert worker.submit(TextChunk(f"第{sequence + 1}句。", turn_id="bounded", sequence_id=sequence))
        assert next_ready.wait(1), "synthesis did not overlap playback"
        assert not fourth_started.wait(0.1), "prepared audio prefetch is unbounded"
    finally:
        release.set()
        worker.join()
        worker.close()
    assert len(calls) == 5


def test_close_without_drain_stops_active_playback_and_releases_producer(tmp_path):
    playing = threading.Event()
    release = threading.Event()
    closed = threading.Event()
    producer_done = threading.Event()
    failures = []
    played = []

    class Synthesizer:
        def synthesize(self, text, path):
            _write_wav(path)

    class Player:
        def __call__(self, path):
            played.append(path.name)
            playing.set()
            assert release.wait(4)

        def stop(self):
            release.set()

    worker = IncrementalTTSPlayer(Synthesizer(), queue_maxsize=1, temp_parent=tmp_path, player=Player())
    worker.start()

    def produce():
        try:
            for sequence in range(100):
                worker.submit(TextChunk("尚未播放的句子。", turn_id="closing", sequence_id=sequence), block=True)
        except RuntimeError:
            pass  # A closed worker must reject outstanding submissions.
        except Exception as error:
            failures.append(error)
        finally:
            producer_done.set()

    def close_worker():
        try:
            worker.close(drain=False)
        except Exception as error:
            failures.append(error)
        finally:
            closed.set()

    producer = threading.Thread(target=produce)
    closer = threading.Thread(target=close_worker)
    try:
        producer.start()
        assert playing.wait(2)
        closer.start()
        assert closed.wait(1.5), "close failed to stop active playback"
        assert producer_done.wait(1), "blocked producer survived worker close"
    finally:
        release.set()
        producer.join(4)
        if closer.ident is not None:
            closer.join(4)
        worker.close(drain=False)
    assert failures == []
    assert not producer.is_alive()
    assert not closer.is_alive()
    assert len(played) == 1
    assert not worker.is_alive
    assert not list(tmp_path.rglob("chunk-*.wav"))


def test_drain_close_preserves_accepted_audio_and_completion_events(tmp_path):
    texts = []
    played = []
    finished = []

    class Synthesizer:
        def synthesize(self, text, path):
            texts.append(text)
            _write_wav(path)

    worker = IncrementalTTSPlayer(
        Synthesizer(), temp_parent=tmp_path, player=lambda path: played.append(path.name),
        on_playback_finished=lambda result: finished.append(result.sequence),
    )
    for sequence in range(3):
        assert worker.submit(TextChunk(f"第{sequence}句。", turn_id="drain", sequence_id=sequence))
    worker.start()
    worker.close(drain=True)
    assert texts == ["第0句。", "第1句。", "第2句。"]
    assert len(played) == 3
    assert finished == [0, 1, 2]
    assert not worker.is_alive


def test_join_waits_for_work_not_for_idle_workers_to_exit(tmp_path):
    joined = threading.Event()
    finished = threading.Event()

    class Synthesizer:
        def synthesize(self, text, path):
            _write_wav(path)

    worker = IncrementalTTSPlayer(
        Synthesizer(), temp_parent=tmp_path, player=lambda path: None,
        on_playback_finished=lambda result: finished.set(),
    )
    worker.start()
    assert worker.submit(TextChunk("短回答。", turn_id="join"))
    joiner = threading.Thread(target=lambda: (worker.join(), joined.set()))
    try:
        joiner.start()
        assert joined.wait(0.75), "join waits on persistent worker threads instead of completed work"
        assert finished.is_set()
        assert worker.is_alive  # Idle threads remain reusable for the next turn.
    finally:
        joiner.join(4)
        worker.close(drain=False)
    assert not joiner.is_alive()
