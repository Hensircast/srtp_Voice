from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol


@dataclass
class AudioChunk:
    pcm16: bytes
    sample_rate: int
    channels: int = 1
    timestamp_ms: int = 0
    is_final: bool = False


@dataclass
class TextChunk:
    text: str
    is_final: bool = False
    timestamp_ms: int = 0


class AudioInputStream(Protocol):
    def chunks(self) -> Iterable[AudioChunk]:
        ...


class StreamingASRBackend(Protocol):
    def transcribe_stream(self, audio: AudioInputStream) -> Iterable[TextChunk]:
        ...


class StreamingLLMBackend(Protocol):
    def generate_stream(self, text: Iterable[TextChunk]) -> Iterable[TextChunk]:
        ...


class StreamingTTSBackend(Protocol):
    def synthesize_stream(self, text: Iterable[TextChunk]) -> Iterable[AudioChunk]:
        ...


class AudioOutputStream(Protocol):
    def write(self, chunk: AudioChunk) -> None:
        ...
