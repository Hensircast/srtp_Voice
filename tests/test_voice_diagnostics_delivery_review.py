"""A blocked diagnostic sink must not postpone the final unpunctuated speech."""
from __future__ import annotations

import struct
import threading
import wave

import pytest

from srtp_voice.config import AppConfig
from srtp_voice.streaming import StreamEventType, TextChunk
from srtp_voice.streaming_runtime import StreamingResponseRuntime
from srtp_voice.types import EmotionResult


@pytest.mark.parametrize("natural", [False, True])
@pytest.mark.parametrize("reply", ["好的", "明白了", "欢迎"])
def test_final_unpunctuated_reply_plays_while_diagnostic_sink_is_blocked(tmp_path, natural, reply):
    sink_entered = threading.Event()
    release_sink = threading.Event()
    played = threading.Event()
    results, errors, events = [], [], []

    class Generator:
        def generate_stream(self, *args, turn_id="", **kwargs):
            yield TextChunk(reply, turn_id=turn_id)
            yield TextChunk("", is_final=True, turn_id=turn_id,
                            diagnostics={"llm_server_requests": [{"model_load_ms": 0}]})

        def default_stream_action(self):
            return {}

    class TTS:
        def synthesize(self, text, path):
            assert text == reply
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(1000)
                wav.writeframes(struct.pack("<hhhh", 1, 2, 3, 4))

    def sink(event):
        events.append(event)
        if event.event_type == StreamEventType.LLM_DIAGNOSTICS:
            sink_entered.set()
            assert release_sink.wait(timeout=5)

    runtime = StreamingResponseRuntime(
        AppConfig(output_dir=tmp_path, sample_rate=1000, stream_natural_boundaries=natural,
                  stream_sentence_max_chars=100, stream_sentence_max_wait_seconds=3),
        Generator(), TTS(), event_sink=sink, player=lambda path: played.set(),
        playback_enabled=True, temp_parent=tmp_path,
    )
    handle = runtime.begin_turn()

    def run():
        try:
            results.append(runtime.run_response(handle, user_text="test", emotion=EmotionResult("neutral", .2, .8, {}),
                                                history=[], reply_audio=tmp_path / "reply.wav"))
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert sink_entered.wait(timeout=2)
        assert played.wait(timeout=1), "final TTS/playback must precede diagnostic-sink release"
        assert not release_sink.is_set()
    finally:
        release_sink.set()
        thread.join(timeout=5)
        runtime.close()
    assert not thread.is_alive()
    assert errors == []
    assert results[0].strategy.reply_text == reply
    assert results[0].latency.to_dict()["backend_diagnostics"] == {
        "llm_server_requests": [{"model_load_ms": 0}]
    }
    assert len([event for event in events if event.event_type == StreamEventType.LLM_DIAGNOSTICS]) == 1
