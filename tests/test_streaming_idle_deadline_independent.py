"""Held-out, bounded token-stall checks; no real model or audio device."""

from __future__ import annotations

import threading
import wave

import pytest

from srtp_voice.config import AppConfig
from srtp_voice.streaming import StreamEventType, TextChunk
from srtp_voice.streaming_runtime import StreamingResponseRuntime, TurnCancelledError
from srtp_voice.types import EmotionResult


class GatedTokens:
    def __init__(self, first, tail="继续把话说完。", error=None):
        self.first = first
        self.tail = tail
        self.error = error
        self.waiting = threading.Event()
        self.release = threading.Event()
        self.closed = threading.Event()
        self.read_threads = []
        self.close_thread = None

    def generate_stream(self, user_text, emotion, history, *, turn_id=""):
        try:
            self.read_threads.append(threading.get_ident())
            yield TextChunk(self.first, turn_id=turn_id, sequence_id=0)
            self.waiting.set()
            if not self.release.wait(3):
                raise AssertionError("Test gate must be released by its owner")
            self.read_threads.append(threading.get_ident())
            if self.error is not None:
                raise self.error
            yield TextChunk(self.tail, turn_id=turn_id, sequence_id=1)
            yield TextChunk("", is_final=True, turn_id=turn_id, sequence_id=2)
        finally:
            self.close_thread = threading.get_ident()
            self.closed.set()

    @staticmethod
    def default_stream_action():
        return {"expression": "neutral_smile"}


class FakeSpeech:
    def __init__(self):
        self.started = threading.Event()
        self.texts = []

    def synthesize(self, text, path):
        self.texts.append(text)
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(1000)
            output.writeframes(b"\x00\x00" * 8)
        self.started.set()


def launch(tmp_path, source, *, natural=True, min_chars=4):
    speech = FakeSpeech()
    played = threading.Event()
    events = []
    runtime = StreamingResponseRuntime(
        AppConfig(
            output_dir=tmp_path,
            sample_rate=1000,
            stream_natural_boundaries=natural,
            stream_sentence_min_chars=min_chars,
            stream_sentence_max_chars=100,
            stream_sentence_max_wait_seconds=0.1,
            stream_tts_queue_size=2,
        ),
        source,
        speech,
        player=lambda path: played.set(),
        event_sink=events.append,
        temp_parent=tmp_path,
    )
    handle = runtime.begin_turn()
    outcome = {}

    def run():
        try:
            outcome["result"] = runtime.run_response(
                handle,
                user_text="公开测试问题",
                emotion=EmotionResult("neutral", 0.2, 0.8, {}),
                history=[],
                reply_audio=tmp_path / "reply.wav",
            )
        except BaseException as error:
            outcome["error"] = error

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return runtime, thread, speech, played, events, outcome


def cleanup(source, runtime, thread):
    source.release.set()
    thread.join(3)
    runtime.close()
    assert not thread.is_alive(), "run_response leaked beyond the bounded test"
    assert source.closed.wait(1), "token stream was not closed"
    assert not runtime.worker_alive


@pytest.mark.parametrize(
    "first, expected",
    [
        ("这是一个足够长的分句，后面", "这是一个足够长的分句，"),
        ("请先坐稳，然后", "请先坐稳，"),
        ("Please sit down,it", "Please sit down,"),
    ],
)
def test_natural_clause_speaks_while_token_source_is_still_idle(tmp_path, first, expected):
    source = GatedTokens(first)
    runtime, thread, speech, played, events, outcome = launch(tmp_path, source)
    try:
        assert source.waiting.wait(1)
        # The backend remains gated: this cannot pass by waiting for next token.
        assert speech.started.wait(0.4), "deadline did not submit speech during token idle"
        assert played.wait(0.4)
        assert not source.release.is_set()
        assert speech.texts == [expected]
        assert [e.payload["chunk_sequence"] for e in events if e.event_type is StreamEventType.SENTENCE_READY] == [0]
    finally:
        cleanup(source, runtime, thread)
    assert "error" not in outcome
    assert outcome["result"].strategy.reply_text == first + source.tail
    assert "".join(speech.texts) == first + source.tail
    assert len(set(source.read_threads)) == 1
    assert source.close_thread == source.read_threads[0]


def test_legacy_comma_still_speaks_before_backend_continues(tmp_path):
    source = GatedTokens("这是一个足够长的分句，后面")
    runtime, thread, speech, played, events, outcome = launch(tmp_path, source, natural=False)
    try:
        assert source.waiting.wait(1)
        assert speech.started.wait(0.4)
        assert speech.texts[0] == "这是一个足够长的分句，"
    finally:
        cleanup(source, runtime, thread)
    assert "error" not in outcome


def test_hard_boundary_preview_is_not_synthesized_twice_on_idle_deadline(tmp_path):
    source = GatedTokens("先坐稳。", tail="”然后慢慢说。")
    runtime, thread, speech, played, events, outcome = launch(tmp_path, source)
    try:
        assert source.waiting.wait(1)
        assert speech.started.wait(0.4)
        assert not source.release.wait(0.2)
        assert speech.texts == ["先坐稳。"]
    finally:
        cleanup(source, runtime, thread)
    assert "error" not in outcome
    assert speech.texts == ["先坐稳。", "然后慢慢说。"]
    assert outcome["result"].strategy.reply_text == source.first + source.tail


@pytest.mark.parametrize("first, tail", [("金额是3.", "14元。"), ("Please ask Dr.", " Smith for help.")])
def test_ambiguous_period_is_not_cut_without_lookahead(tmp_path, first, tail):
    source = GatedTokens(first, tail)
    runtime, thread, speech, played, events, outcome = launch(tmp_path, source, min_chars=1)
    try:
        assert source.waiting.wait(1)
        assert not speech.started.wait(0.3)
    finally:
        cleanup(source, runtime, thread)
    assert "error" not in outcome
    assert "".join(speech.texts) == first + tail


def test_reader_error_after_idle_speech_preserves_error_and_closes_source(tmp_path):
    error = ValueError("synthetic-reader-error")
    source = GatedTokens("这是一个足够长的分句，后面", error=error)
    runtime, thread, speech, played, events, outcome = launch(tmp_path, source)
    try:
        assert source.waiting.wait(1)
        assert speech.started.wait(0.4)
    finally:
        cleanup(source, runtime, thread)
    assert outcome.get("error") is error
    assert "result" not in outcome


def test_cancelled_idle_turn_never_plays_its_late_tail(tmp_path):
    source = GatedTokens("这是一个足够长的分句，后面")
    runtime, thread, speech, played, events, outcome = launch(tmp_path, source)
    try:
        assert source.waiting.wait(1)
        runtime.cancel_current(reason="bounded-test")
    finally:
        cleanup(source, runtime, thread)
    assert isinstance(outcome.get("error"), TurnCancelledError)
    assert "result" not in outcome
    assert speech.texts == []
    assert not played.is_set()


def test_cancel_returns_without_waiting_for_the_blocked_backend(tmp_path):
    source = GatedTokens("短", tail="完整回复。")
    runtime, thread, speech, played, events, outcome = launch(tmp_path, source)
    try:
        assert source.waiting.wait(1)
        runtime.cancel_current(reason="prompt-cancel")
        thread.join(0.4)
        assert not thread.is_alive(), "cancellation waited for the idle backend reader"
        assert not source.release.is_set()
        assert speech.texts == []
    finally:
        cleanup(source, runtime, thread)
    assert isinstance(outcome.get("error"), TurnCancelledError)


def test_legacy_iterator_keeps_the_response_callers_thread(tmp_path):
    source = GatedTokens("这是一个足够长的分句，后面")
    runtime, thread, speech, played, events, outcome = launch(tmp_path, source, natural=False)
    try:
        assert source.waiting.wait(1)
    finally:
        cleanup(source, runtime, thread)
    assert "error" not in outcome
    assert set(source.read_threads) == {thread.ident}
    assert source.close_thread == thread.ident


@pytest.mark.parametrize("failure_stage", ["construct", "start"])
def test_reader_start_failure_closes_the_unstarted_resource(tmp_path, monkeypatch, failure_stage):
    class ResourceTokens:
        def __init__(self):
            self.close_calls = 0

        def __iter__(self):
            return self

        def __next__(self):
            raise StopIteration

        def close(self):
            self.close_calls += 1

    source = ResourceTokens()

    class Generator:
        @staticmethod
        def generate_stream(*args, **kwargs):
            return source

    speech = FakeSpeech()
    runtime = StreamingResponseRuntime(
        AppConfig(output_dir=tmp_path, stream_natural_boundaries=True),
        Generator(), speech, playback_enabled=False, temp_parent=tmp_path,
    )
    original_start = threading.Thread.start
    expected_error = RuntimeError("synthetic-reader-start-error")

    def start(thread):
        if thread.name == "srtp-streaming-llm-reader":
            raise expected_error
        return original_start(thread)

    if failure_stage == "construct":
        original_thread = threading.Thread

        def construct(*args, **kwargs):
            if kwargs.get("name") == "srtp-streaming-llm-reader":
                raise expected_error
            return original_thread(*args, **kwargs)

        monkeypatch.setattr(threading, "Thread", construct)
    else:
        monkeypatch.setattr(threading.Thread, "start", start)
    handle = runtime.begin_turn()
    try:
        with pytest.raises(RuntimeError) as caught:
            runtime.run_response(
                handle, user_text="公开测试", emotion=EmotionResult("neutral", 0.2, 0.8, {}),
                history=[], reply_audio=tmp_path / "reply.wav",
            )
        assert caught.value is expected_error
        assert source.close_calls == 1, "unstarted reader leaked its already-created source"
    finally:
        runtime.close()


def test_partial_reader_start_failure_never_closes_its_running_source(tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    closed = threading.Event()
    read_thread = []
    close_threads = []

    class ResourceTokens:
        def __iter__(self):
            return self

        def __next__(self):
            read_thread.append(threading.get_ident())
            entered.set()
            if not release.wait(3):
                raise AssertionError("test must release the blocked source")
            raise StopIteration

        def close(self):
            close_threads.append(threading.get_ident())
            closed.set()

    source = ResourceTokens()

    class Generator:
        @staticmethod
        def generate_stream(*args, **kwargs):
            return source

    runtime = StreamingResponseRuntime(
        AppConfig(output_dir=tmp_path, stream_natural_boundaries=True),
        Generator(), FakeSpeech(), playback_enabled=False, temp_parent=tmp_path,
    )
    original_start = threading.Thread.start
    expected_error = RuntimeError("synthetic-partial-start-error")
    reader_threads = []

    def start(thread):
        result = original_start(thread)
        if thread.name == "srtp-streaming-llm-reader":
            reader_threads.append(thread)
            assert entered.wait(1)
            raise expected_error
        return result

    monkeypatch.setattr(threading.Thread, "start", start)
    handle = runtime.begin_turn()
    try:
        with pytest.raises(RuntimeError) as caught:
            runtime.run_response(
                handle, user_text="公开测试", emotion=EmotionResult("neutral", 0.2, 0.8, {}),
                history=[], reply_audio=tmp_path / "reply.wav",
            )
        assert caught.value is expected_error
        assert close_threads == [], "caller closed a source already owned by its reader"
    finally:
        release.set()
        for reader in reader_threads:
            reader.join(3)
            assert not reader.is_alive()
        runtime.close()
    assert closed.is_set()
    assert close_threads == read_thread
