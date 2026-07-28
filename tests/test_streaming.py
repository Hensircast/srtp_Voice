from __future__ import annotations

from srtp_voice.streaming import AudioChunk, TextChunk


def test_audio_chunk_legacy_positional_constructor_is_compatible() -> None:
    chunk = AudioChunk(b"pcm", 16000, 1, 407, True)

    assert chunk.pcm16 == b"pcm"
    assert chunk.sample_rate == 16000
    assert chunk.channels == 1
    assert chunk.timestamp_ms == 407
    assert chunk.is_final is True
    assert chunk.session_id == ""
    assert chunk.turn_id == ""
    assert chunk.sequence_id == 0


def test_text_chunk_legacy_positional_constructor_is_compatible() -> None:
    chunk = TextChunk("hello", True, 407)

    assert chunk.text == "hello"
    assert chunk.is_final is True
    assert chunk.timestamp_ms == 407
    assert chunk.session_id == ""
    assert chunk.turn_id == ""
    assert chunk.sequence_id == 0


def test_future_stream_identifiers_are_optional_metadata() -> None:
    audio = AudioChunk(
        b"pcm",
        16000,
        session_id="session-1",
        turn_id="turn-2",
        sequence_id=3,
    )
    text = TextChunk(
        "hello",
        session_id="session-1",
        turn_id="turn-2",
        sequence_id=4,
    )

    assert (audio.session_id, audio.turn_id, audio.sequence_id) == (
        "session-1",
        "turn-2",
        3,
    )
    assert (text.session_id, text.turn_id, text.sequence_id) == (
        "session-1",
        "turn-2",
        4,
    )
