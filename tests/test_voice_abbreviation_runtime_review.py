"""Independent real-thread delivery tests; TTS/model are explicit local fakes."""

import threading
import wave

import pytest

from srtp_voice.config import AppConfig
from srtp_voice.streaming import TextChunk
from srtp_voice.streaming_runtime import StreamingResponseRuntime
from srtp_voice.types import EmotionResult


@pytest.mark.parametrize("natural", [False, True])
@pytest.mark.parametrize(
    "first,following",
    [("Use etc.", " Next step."), ("答案是A.", " 下一题。")],
)
def test_first_tts_starts_before_generator_completes_second_sentence(
    tmp_path, natural, first, following
):
    release_last_token = threading.Event()
    first_tts_started = threading.Event()
    calls, results, errors = [], [], []

    class GatedGenerator:
        def generate_stream(self, user_text, emotion, history, *, turn_id=""):
            yield TextChunk(first, turn_id=turn_id, sequence_id=0)
            yield TextChunk(following[:-1], turn_id=turn_id, sequence_id=1)
            if not release_last_token.wait(timeout=3):
                raise RuntimeError("test did not release generator")
            yield TextChunk(following[-1], turn_id=turn_id, sequence_id=2)

        @staticmethod
        def default_stream_action():
            return {"expression": "neutral_smile"}

    class SignallingTTS:
        def synthesize(self, text, path):
            calls.append(text)
            path.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(1000)
                audio.writeframes(b"\x01\x00" * 4)
            if text == first:
                first_tts_started.set()

    runtime = StreamingResponseRuntime(
        AppConfig(
            output_dir=tmp_path,
            stream_natural_boundaries=natural,
            stream_sentence_max_chars=100,
            stream_sentence_max_wait_seconds=1.0,
        ),
        GatedGenerator(),
        SignallingTTS(),
        playback_enabled=False,
        temp_parent=tmp_path,
        id_factory=lambda: "abbreviation-runtime-review",
    )
    handle = runtime.begin_turn()

    def respond():
        try:
            results.append(
                runtime.run_response(
                    handle,
                    user_text="public regression",
                    emotion=EmotionResult("neutral", 0.2, 0.8, {}),
                    history=[],
                    reply_audio=tmp_path / "reply.wav",
                )
            )
        except Exception as exc:
            errors.append(exc)

    response = threading.Thread(target=respond)
    response.start()
    try:
        assert first_tts_started.wait(timeout=2)
    finally:
        release_last_token.set()
        response.join(timeout=3)
        runtime.close()
    assert not response.is_alive()
    assert errors == []
    assert calls == [first, following.strip()]
    assert len(results) == 1
    assert results[0].strategy.reply_text == first + following
    assert results[0].audio_chunks == 2
    assert not runtime.worker_alive
