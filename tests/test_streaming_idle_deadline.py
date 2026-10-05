"""Own bounded gate for the idle-deadline flush (no real model or device).

The fake source stalls *between* tokens, which is the case that used to hold a
due sentence until the backend produced more text. Every case checks that the
flush happens on the runtime's own thread, that the source is closed exactly
once by the thread that read it, and that nothing is prefetched unboundedly.
"""

from __future__ import annotations

import threading
import wave

import pytest

from srtp_voice.config import AppConfig
from srtp_voice.streaming import StreamEventType, TextChunk
from srtp_voice.streaming_runtime import StreamingResponseRuntime, TurnCancelledError
from srtp_voice.types import EmotionResult


class StalledTokens:
    """Yields one token, stalls, then finishes; records reader/close threads."""

    def __init__(self, first, tail="后面的话也要说完。", error=None, gate_seconds=3.0):
        self.first = first
        self.tail = tail
        self.error = error
        self.gate_seconds = gate_seconds
        self.stalled = threading.Event()
        self.release = threading.Event()
        self.closed = threading.Event()
        self.read_threads: list[int] = []
        self.close_thread: int | None = None
        self.yielded = 0

    def generate_stream(self, user_text, emotion, history, *, turn_id=""):
        try:
            self.read_threads.append(threading.get_ident())
            self.yielded += 1
            yield TextChunk(self.first, turn_id=turn_id, sequence_id=0)
            self.stalled.set()
            if not self.release.wait(self.gate_seconds):
                raise AssertionError("test gate must be released by its owner")
            self.read_threads.append(threading.get_ident())
            if self.error is not None:
                raise self.error
            self.yielded += 1
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
        self.texts: list[str] = []

    def synthesize(self, text, path):
        self.texts.append(text)
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(1000)
            output.writeframes(b"\x00\x00" * 8)
        self.started.set()


def launch(tmp_path, source, *, natural=True, min_chars=4, max_wait=0.1):
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
            stream_sentence_max_wait_seconds=max_wait,
            stream_tts_queue_size=2,
        ),
        source,
        speech,
        player=lambda path: played.set(),
        event_sink=events.append,
        temp_parent=tmp_path,
    )
    handle = runtime.begin_turn()
    outcome: dict = {}

    def run():
        try:
            outcome["thread"] = threading.get_ident()
            outcome["result"] = runtime.run_response(
                handle,
                user_text="公开合成问题",
                emotion=EmotionResult("neutral", 0.2, 0.8, {}),
                history=[],
                reply_audio=tmp_path / "reply.wav",
            )
        except BaseException as error:  # recorded, never hidden
            outcome["error"] = error

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return runtime, thread, speech, played, events, outcome


def cleanup(source, runtime, thread):
    source.release.set()
    thread.join(3)
    runtime.close()
    assert not thread.is_alive(), "run_response leaked past the bounded test"
    assert source.closed.wait(1), "token source was not closed"
    assert not runtime.worker_alive


@pytest.mark.parametrize(
    "first, expected",
    [
        ("这是一个足够长的分句，后面", "这是一个足够长的分句，"),
        ("先把水烧开，然后", "先把水烧开，"),
        ("Please sit down,it", "Please sit down,"),
    ],
)
def test_idle_deadline_releases_the_due_sentence(tmp_path, first, expected):
    source = StalledTokens(first)
    runtime, thread, speech, played, events, outcome = launch(tmp_path, source)
    try:
        assert source.stalled.wait(1)
        # Still gated: passing here cannot come from the next token.
        assert speech.started.wait(0.4), "no speech started while the source was idle"
        assert played.wait(0.4)
        assert not source.release.is_set()
        assert speech.texts == [expected]
        assert [
            event.payload["chunk_sequence"]
            for event in events
            if event.event_type is StreamEventType.SENTENCE_READY
        ] == [0]
    finally:
        cleanup(source, runtime, thread)
    assert "error" not in outcome
    assert outcome["result"].strategy.reply_text == first + source.tail
    assert "".join(speech.texts) == first + source.tail
    assert len(set(source.read_threads)) == 1, "the source must be read by one thread"
    assert source.close_thread == source.read_threads[0]


def test_idle_flush_never_duplicates_a_hard_boundary_preview(tmp_path):
    source = StalledTokens("先坐稳。", tail="”然后慢慢说。")
    runtime, thread, speech, played, events, outcome = launch(tmp_path, source)
    try:
        assert source.stalled.wait(1)
        assert speech.started.wait(0.4)
        assert not source.release.wait(0.2)
        assert speech.texts == ["先坐稳。"]
    finally:
        cleanup(source, runtime, thread)
    assert "error" not in outcome
    assert speech.texts == ["先坐稳。", "然后慢慢说。"]


@pytest.mark.parametrize(
    "first, tail",
    [("金额是3.", "14元。"), ("Please ask Dr.", " Smith for help.")],
)
def test_ambiguous_period_is_not_cut_on_the_idle_deadline(tmp_path, first, tail):
    source = StalledTokens(first, tail)
    runtime, thread, speech, played, events, outcome = launch(tmp_path, source, min_chars=1)
    try:
        assert source.stalled.wait(1)
        assert not speech.started.wait(0.3)
    finally:
        cleanup(source, runtime, thread)
    assert "error" not in outcome
    assert "".join(speech.texts) == first + tail


def test_fragment_below_min_chars_is_not_spoken_on_the_idle_deadline(tmp_path):
    # "短，" is only two characters: with min_chars=3 the deadline must not turn
    # a below-threshold fragment into speech.
    source = StalledTokens("短，", "碎片也要说完。")
    runtime, thread, speech, played, events, outcome = launch(tmp_path, source, min_chars=3)
    try:
        assert source.stalled.wait(1)
        assert not speech.started.wait(0.3)
    finally:
        cleanup(source, runtime, thread)
    assert "error" not in outcome
    assert "".join(speech.texts) == "短，碎片也要说完。"


def test_reader_error_is_delivered_and_the_source_is_closed(tmp_path):
    error = ValueError("synthetic-reader-error")
    source = StalledTokens("这是一个足够长的分句，后面", error=error)
    runtime, thread, speech, played, events, outcome = launch(tmp_path, source)
    try:
        assert source.stalled.wait(1)
        assert speech.started.wait(0.4)
    finally:
        cleanup(source, runtime, thread)
    assert outcome.get("error") is error
    assert "result" not in outcome


def test_cancelling_an_idle_turn_stops_the_reader(tmp_path):
    source = StalledTokens("这是一个足够长的分句，后面")
    runtime, thread, speech, played, events, outcome = launch(tmp_path, source)
    try:
        assert source.stalled.wait(1)
        runtime.cancel_current(reason="bounded-test")
    finally:
        cleanup(source, runtime, thread)
    assert isinstance(outcome.get("error"), TurnCancelledError)
    assert speech.texts == []
    assert not played.is_set()


def test_legacy_mode_keeps_its_comma_deadline(tmp_path):
    source = StalledTokens("这是一个足够长的分句，后面")
    runtime, thread, speech, played, events, outcome = launch(tmp_path, source, natural=False)
    try:
        assert source.stalled.wait(1)
        assert speech.started.wait(0.4)
        assert speech.texts[0] == "这是一个足够长的分句，"
    finally:
        cleanup(source, runtime, thread)
    assert "error" not in outcome
    # Legacy keeps the original synchronous contract: the caller thread both
    # iterates and closes the source, with no background reader involved.
    assert set(source.read_threads) == {outcome["thread"]}
    assert source.close_thread == outcome["thread"]


class _CloseCountingGenerator:
    """Counts close() calls on the object the runtime actually closes."""

    def __init__(self, inner):
        self.inner = inner
        self.closes = 0

    def __iter__(self):
        return iter(self.inner)

    def close(self):
        self.closes += 1
        return self.inner.close()

    def __getattr__(self, name):
        return getattr(self.inner, name)


class _CountingSource:
    """Source whose generate_stream returns a close-counting generator."""

    def __init__(self, inner):
        self.inner = inner
        self.generator = _CloseCountingGenerator(
            inner.generate_stream("", None, [], turn_id="")
        )

    def generate_stream(self, *args, **kwargs):
        return self.generator

    @staticmethod
    def default_stream_action():
        return {"expression": "neutral_smile"}


def test_reader_start_failure_closes_the_unstarted_source(tmp_path, monkeypatch):
    inner = StalledTokens("这是一个足够长的分句，后面")
    source = _CountingSource(inner)
    runtime = StreamingResponseRuntime(
        AppConfig(
            output_dir=tmp_path,
            sample_rate=1000,
            stream_natural_boundaries=True,
            stream_sentence_min_chars=4,
            stream_sentence_max_chars=100,
            stream_sentence_max_wait_seconds=0.1,
            stream_tts_queue_size=2,
        ),
        source,
        FakeSpeech(),
        player=lambda path: None,
        event_sink=[].append,
        temp_parent=tmp_path,
    )
    # The TTS worker is created before the patch, so only the reader's start is
    # refused; run_response is driven straight from this caller thread.
    handle = runtime.begin_turn()
    failure = RuntimeError("thread start failed")

    def refuse_start(self):  # pragma: no cover - exercised through the patch
        raise failure

    monkeypatch.setattr(threading.Thread, "start", refuse_start, raising=False)
    try:
        with pytest.raises(RuntimeError) as raised:
            runtime.run_response(
                handle,
                user_text="公开合成问题",
                emotion=EmotionResult("neutral", 0.2, 0.8, {}),
                history=[],
                reply_audio=tmp_path / "reply.wav",
            )
        assert raised.value is failure
    finally:
        monkeypatch.undo()
        runtime.close()

    # The unstarted resource is closed exactly once instead of being left to gc,
    # and the generator body never executed.
    assert source.generator.closes == 1
    assert inner.closed.is_set() is False
    assert runtime.worker_alive is False
