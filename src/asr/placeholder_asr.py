from __future__ import annotations


def transcribe_audio(audio_path: str) -> str:
    """Return a fixed local ASR placeholder result without loading any model."""
    if not audio_path.strip():
        raise ValueError("audio_path must not be empty.")

    return "这是语音识别占位结果"
