from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from math import isfinite
from threading import Lock
from time import monotonic
from typing import Any, Callable, Dict, Iterable, Mapping, Protocol


@dataclass
class AudioChunk:
    pcm16: bytes
    sample_rate: int
    channels: int = 1
    timestamp_ms: int = 0
    is_final: bool = False
    session_id: str = ""
    turn_id: str = ""
    sequence_id: int = 0


@dataclass
class TextChunk:
    text: str
    is_final: bool = False
    timestamp_ms: int = 0
    session_id: str = ""
    turn_id: str = ""
    sequence_id: int = 0


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


class StreamEventType(str, Enum):
    """Stable event names shared by the V1.8 streaming pipeline."""

    TURN_STARTED = "turn_started"
    AUDIO_FRAME = "audio_frame"
    VAD_STARTED = "vad_started"
    VAD_STOPPED = "vad_stopped"
    ASR_PARTIAL = "asr_partial"
    ASR_FINAL = "asr_final"
    LLM_REQUEST_STARTED = "llm_request_started"
    LLM_TOKEN = "llm_token"
    SENTENCE_READY = "sentence_ready"
    TTS_STARTED = "tts_started"
    AUDIO_CHUNK_READY = "audio_chunk_ready"
    PLAYBACK_STARTED = "playback_started"
    PLAYBACK_FINISHED = "playback_finished"
    TURN_FINISHED = "turn_finished"
    TURN_CANCELLED = "turn_cancelled"
    ERROR = "error"


@dataclass(frozen=True)
class StreamEvent:
    """One ordered event emitted by a single conversation turn.

    ``timestamp`` is measured in monotonic seconds. It is suitable for latency
    calculations but intentionally is not a wall-clock value.
    """

    event_type: StreamEventType
    turn_id: str
    sequence: int
    timestamp: float
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            event_type = StreamEventType(self.event_type)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Unsupported stream event type: {self.event_type!r}") from exc
        if not self.turn_id:
            raise ValueError("Stream events require a non-empty turn_id")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise TypeError("Stream event sequence must be an integer")
        if self.sequence < 0:
            raise ValueError("Stream event sequence must be non-negative")
        if not isfinite(self.timestamp):
            raise ValueError("Stream event timestamp must be finite")
        object.__setattr__(self, "event_type", event_type)
        object.__setattr__(self, "payload", dict(self.payload))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "turn_id": self.turn_id,
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "payload": dict(self.payload),
        }


class StreamEventFactory:
    """Create monotonic, strictly sequenced events for one turn."""

    def __init__(
        self,
        turn_id: str,
        *,
        clock: Callable[[], float] | None = None,
        starting_sequence: int = 0,
    ) -> None:
        if not turn_id:
            raise ValueError("StreamEventFactory requires a non-empty turn_id")
        if isinstance(starting_sequence, bool) or not isinstance(starting_sequence, int):
            raise TypeError("starting_sequence must be an integer")
        if starting_sequence < 0:
            raise ValueError("starting_sequence must be non-negative")
        self.turn_id = turn_id
        self._clock = clock or monotonic
        self._next_sequence = starting_sequence
        self._last_timestamp: float | None = None
        self._lock = Lock()

    def emit(
        self,
        event_type: StreamEventType | str,
        payload: Mapping[str, Any] | None = None,
    ) -> StreamEvent:
        with self._lock:
            timestamp = float(self._clock())
            if not isfinite(timestamp):
                raise ValueError("Monotonic clock returned a non-finite value")
            if self._last_timestamp is not None and timestamp < self._last_timestamp:
                raise RuntimeError("Monotonic clock moved backwards")
            event = StreamEvent(
                event_type=StreamEventType(event_type),
                turn_id=self.turn_id,
                sequence=self._next_sequence,
                timestamp=timestamp,
                payload=payload or {},
            )
            self._next_sequence += 1
            self._last_timestamp = timestamp
            return event


_TIMING_MARKS = {
    StreamEventType.TURN_STARTED: "turn_started",
    StreamEventType.VAD_STARTED: "vad_started",
    StreamEventType.VAD_STOPPED: "vad_stopped",
    StreamEventType.ASR_PARTIAL: "asr_first_partial",
    StreamEventType.ASR_FINAL: "asr_final",
    StreamEventType.LLM_REQUEST_STARTED: "llm_request_started",
    StreamEventType.LLM_TOKEN: "llm_first_token",
    StreamEventType.SENTENCE_READY: "first_sentence_ready",
    StreamEventType.TTS_STARTED: "tts_started",
    StreamEventType.AUDIO_CHUNK_READY: "first_audio_chunk_ready",
    StreamEventType.PLAYBACK_STARTED: "playback_started",
    StreamEventType.PLAYBACK_FINISHED: "playback_finished",
    StreamEventType.TURN_FINISHED: "turn_finished",
    StreamEventType.TURN_CANCELLED: "turn_cancelled",
}


@dataclass(frozen=True)
class TurnTimingSnapshot:
    turn_id: str
    marks: Mapping[str, float]
    latencies_ms: Mapping[str, float]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "marks": dict(self.marks),
            "latencies_ms": dict(self.latencies_ms),
        }


class TurnTiming:
    """Record the first occurrence of latency-relevant events for one turn."""

    def __init__(self, turn_id: str) -> None:
        if not turn_id:
            raise ValueError("TurnTiming requires a non-empty turn_id")
        self.turn_id = turn_id
        self._marks: Dict[str, float] = {}
        self._last_sequence = -1
        self._last_timestamp: float | None = None

    def observe(self, event: StreamEvent) -> None:
        if event.turn_id != self.turn_id:
            raise ValueError(
                f"Event turn_id {event.turn_id!r} does not match {self.turn_id!r}"
            )
        if event.sequence <= self._last_sequence:
            raise ValueError("Stream event sequence must be strictly increasing")
        if self._last_timestamp is not None and event.timestamp < self._last_timestamp:
            raise ValueError("Stream event timestamps must be monotonic")
        self._last_sequence = event.sequence
        self._last_timestamp = event.timestamp
        mark = _TIMING_MARKS.get(event.event_type)
        if mark is not None:
            if mark == "playback_finished":
                # Multi-sentence playback must end at the LAST finished chunk,
                # while playback_started keeps the earliest occurrence.
                self._marks[mark] = event.timestamp
            else:
                self._marks.setdefault(mark, event.timestamp)

    def snapshot(self) -> TurnTimingSnapshot:
        marks = dict(self._marks)
        latencies: Dict[str, float] = {}

        def add(name: str, start: str, end: str) -> None:
            if start in marks and end in marks:
                latencies[name] = round((marks[end] - marks[start]) * 1000.0, 3)

        add("vad_start_ms", "turn_started", "vad_started")
        add("vad_duration_ms", "vad_started", "vad_stopped")
        add("asr_first_partial_ms", "vad_started", "asr_first_partial")
        add("asr_final_ms", "vad_stopped", "asr_final")
        add("post_asr_setup_ms", "asr_final", "llm_request_started")
        add("endpoint_to_first_audio_ms", "vad_stopped", "first_audio_chunk_ready")
        add("endpoint_to_playback_ms", "vad_stopped", "playback_started")
        add("llm_first_token_ms", "llm_request_started", "llm_first_token")
        add("first_sentence_ms", "llm_request_started", "first_sentence_ready")
        add("tts_first_chunk_ms", "first_sentence_ready", "first_audio_chunk_ready")
        add("time_to_first_token_ms", "turn_started", "llm_first_token")
        add("time_to_first_sentence_ms", "turn_started", "first_sentence_ready")
        add("time_to_first_audio_ms", "turn_started", "first_audio_chunk_ready")
        add("time_to_playback_ms", "turn_started", "playback_started")
        add("playback_duration_ms", "playback_started", "playback_finished")
        if "turn_finished" in marks:
            add("turn_total_ms", "turn_started", "turn_finished")
        elif "playback_finished" in marks:
            add("turn_total_ms", "turn_started", "playback_finished")
        elif "turn_cancelled" in marks:
            add("turn_total_ms", "turn_started", "turn_cancelled")
        return TurnTimingSnapshot(self.turn_id, marks, latencies)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("Cannot calculate a percentile for an empty sample")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


class LatencyTracker:
    """Collect per-turn latency snapshots and aggregate p50/p95 metrics."""

    def __init__(self, *, max_samples: int = 1000) -> None:
        if max_samples < 1:
            raise ValueError("max_samples must be at least 1")
        self._active: Dict[str, TurnTiming] = {}
        self._completed: deque[TurnTimingSnapshot] = deque(maxlen=max_samples)
        self._completed_count = 0
        self._samples: Dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=max_samples)
        )
        self._lock = Lock()

    def observe(self, event: StreamEvent) -> None:
        with self._lock:
            timing = self._active.setdefault(event.turn_id, TurnTiming(event.turn_id))
            timing.observe(event)

    def finish_turn(self, turn_id: str) -> TurnTimingSnapshot:
        with self._lock:
            try:
                timing = self._active.pop(turn_id)
            except KeyError as exc:
                raise KeyError(f"No active timing data for turn {turn_id!r}") from exc
            snapshot = timing.snapshot()
            self._completed.append(snapshot)
            self._completed_count += 1
            for name, value in snapshot.latencies_ms.items():
                self._samples[name].append(value)
            return snapshot

    @property
    def completed_turns(self) -> int:
        with self._lock:
            return self._completed_count

    def summary(self) -> Dict[str, Dict[str, float | int]]:
        with self._lock:
            result: Dict[str, Dict[str, float | int]] = {}
            for name, values in sorted(self._samples.items()):
                sample = list(values)
                result[name] = {
                    "count": len(sample),
                    "min": round(min(sample), 3),
                    "p50": round(_percentile(sample, 0.50), 3),
                    "p95": round(_percentile(sample, 0.95), 3),
                    "max": round(max(sample), 3),
                }
            return result

    def history(self) -> list[Dict[str, Any]]:
        """Bounded chronological timing-only history, independent of audio events."""
        with self._lock:
            return [snapshot.to_dict() for snapshot in self._completed]


_SENTENCE_HARD_PUNCTUATION = frozenset("。！？；….!?;")
# Natural mode keeps the same hard set: enumeration marks and colons that
# introduce a list are soft boundaries and are only used by the bounded
# timeout/length fallback, so a list keeps its prosody.
_SENTENCE_STRONG_PUNCTUATION = _SENTENCE_HARD_PUNCTUATION
_SENTENCE_SOFT_PUNCTUATION = frozenset("：，、,:")
_ABBREVIATIONS = frozenset(
    {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "no", "vs", "etc", "e.g", "i.e"}
)


def _protected_point(text: str, index: int) -> bool:
    """True when the period at ``index`` is not a sentence boundary.

    Covers decimals (``3.14``), version-like model names (``v1.2``), English
    abbreviations (``e.g.``, ``Dr.``) and short initials, so a
    terminator is never synthesized as its own chunk.
    """

    if not (0 <= index < len(text)) or text[index] != ".":
        return False
    previous = text[index - 1] if index > 0 else ""
    following = text[index + 1] if index + 1 < len(text) else ""
    if following.isdigit():
        return True
    if previous.isdigit():
        return False  # A known non-digit follows, or lookahead is unresolved.
    start = index - 1
    while start >= 0 and (
        (text[start].isascii() and text[start].isalnum()) or text[start] == "."
    ):
        start -= 1
    token = text[start + 1 : index].strip(".").lower()
    if token and token in _ABBREVIATIONS:
        return True
    if len(token) == 1 and token.isascii() and token.isalpha():
        return True  # Initials and the first dot in a cross-token e.g./i.e.
    if token and len(token) <= 2 and token.isalpha() and following.isalpha():
        return True
    return False
_SENTENCE_PUNCTUATION = _SENTENCE_HARD_PUNCTUATION | _SENTENCE_SOFT_PUNCTUATION
_SENTENCE_CLOSERS = frozenset("”’\"'）)]】}》〉」』")


def is_speakable_text(text: str) -> bool:
    """Return whether text contains at least one letter or number for TTS."""

    return isinstance(text, str) and any(character.isalnum() for character in text)


class SentenceChunker:
    """Incrementally split LLM text without dropping or rewriting characters."""

    def __init__(
        self,
        *,
        turn_id: str = "",
        min_chars: int = 1,
        max_chars: int = 80,
        max_wait_seconds: float = 0.8,
        prefer_sentence_boundaries: bool = False,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if isinstance(min_chars, bool) or not isinstance(min_chars, int):
            raise TypeError("min_chars must be an integer")
        if min_chars < 1:
            raise ValueError("min_chars must be at least 1")
        if isinstance(max_chars, bool) or not isinstance(max_chars, int):
            raise TypeError("max_chars must be an integer")
        if max_chars < 1:
            raise ValueError("max_chars must be at least 1")
        if isinstance(max_wait_seconds, bool) or not isinstance(
            max_wait_seconds, (int, float)
        ):
            raise TypeError("max_wait_seconds must be numeric")
        if not isfinite(max_wait_seconds) or max_wait_seconds <= 0:
            raise ValueError("max_wait_seconds must be finite and greater than zero")
        if not isinstance(prefer_sentence_boundaries, bool):
            raise TypeError("prefer_sentence_boundaries must be a boolean")
        self.turn_id = turn_id
        self.min_chars = min_chars
        self.max_chars = max_chars
        self.max_wait_seconds = float(max_wait_seconds)
        self.prefer_sentence_boundaries = prefer_sentence_boundaries
        self._hard_punctuation = (
            _SENTENCE_STRONG_PUNCTUATION
            if self.prefer_sentence_boundaries
            else _SENTENCE_HARD_PUNCTUATION
        )
        self._clock = clock or monotonic
        self._buffer: list[str] = []
        self._buffer_started_at: float | None = None
        self._last_now: float | None = None
        self._pending_boundary = False
        self._pending_hard_boundary = False
        # A digit-period at a token edge is unresolved: the next character
        # decides between a decimal (3.14) and a real sentence end (42. Next).
        self._candidate_period = False
        self._next_sequence = 0

    def feed(self, text: str, *, now: float | None = None) -> list[TextChunk]:
        if not isinstance(text, str):
            raise TypeError("SentenceChunker.feed requires text")
        timestamp = self._now(now)
        # A delayed decimal continuation must be incorporated before timeout
        # flushing can irrevocably emit the preceding digit-period.
        if self._candidate_period and text:
            self._candidate_period = False
            self._pending_boundary = not text[0].isdigit()
            self._pending_hard_boundary = self._pending_boundary
            chunks: list[TextChunk] = []
        else:
            chunks = self._flush_due_at(timestamp)

        combined = "".join(self._buffer) + text
        base = len(self._buffer)
        for offset, character in enumerate(text):
            index = base + offset
            # A candidate is unresolved only at the very edge of this token; by
            # the next character the lookahead is known.
            candidate = (
                character == "."
                and index == len(combined) - 1
                and index > 0
                and combined[index - 1].isdigit()
            )
            protected = character == "." and _protected_point(combined, index)
            if self._pending_boundary:
                if character in _SENTENCE_CLOSERS or character in _SENTENCE_PUNCTUATION:
                    pass
                else:
                    chunks.append(self._emit(timestamp))

            if not self._buffer:
                self._buffer_started_at = timestamp
            self._buffer.append(character)

            if candidate:
                self._candidate_period = True
                self._pending_boundary = False
                self._pending_hard_boundary = False
            elif protected:
                # A decimal or abbreviation period never becomes a boundary.
                self._pending_boundary = False
                self._pending_hard_boundary = False
            elif character in self._hard_punctuation:
                self._pending_boundary = True
                self._pending_hard_boundary = True
            elif character in _SENTENCE_SOFT_PUNCTUATION:
                if self.prefer_sentence_boundaries:
                    # Enumeration and comma marks keep the sentence going;
                    # they are only used as a fallback break below.
                    self._pending_boundary = False
                    self._pending_hard_boundary = False
                else:
                    self._pending_boundary = self._speakable_count() >= self.min_chars
                    self._pending_hard_boundary = False
            elif self._pending_boundary and character in _SENTENCE_CLOSERS:
                pass
            else:
                self._pending_boundary = False
                self._pending_hard_boundary = False

            if len(self._buffer) >= self.max_chars and not self._pending_boundary:
                if self.prefer_sentence_boundaries:
                    chunks.extend(self._emit_natural_max(timestamp))
                else:
                    chunks.append(self._emit(timestamp))

        return chunks

    def _emit_natural_max(self, timestamp: float) -> list[TextChunk]:
        """Prefer a complete clause/word when a hard length bound is reached."""

        return [self._emit(timestamp, keep=self._word_boundary_split())]

    def flush_due(self, *, now: float | None = None) -> list[TextChunk]:
        return self._flush_due_at(self._now(now))

    def finish(self, *, now: float | None = None) -> list[TextChunk]:
        timestamp = self._now(now)
        if not self._buffer:
            return []
        return [self._emit(timestamp, is_final=True)]

    @property
    def buffered_text(self) -> str:
        return "".join(self._buffer)

    def peek_pending_hard_boundary(self) -> TextChunk | None:
        """Preview a complete hard-boundary sentence without consuming closers."""

        if (
            not self._buffer
            or not self._pending_boundary
            or not self._pending_hard_boundary
            or self._last_now is None
        ):
            return None
        buffered = "".join(self._buffer)
        if self._candidate_period:
            # Unresolved digit-period: previewing could be irreversible.
            return None
        if buffered.endswith(".") and _protected_point(buffered, len(buffered) - 1):
            # "3." at a token edge is not yet a decided sentence end.
            return None
        return TextChunk(
            text=buffered,
            timestamp_ms=int(self._last_now * 1000),
            turn_id=self.turn_id,
            sequence_id=self._next_sequence,
        )

    def _flush_due_at(self, timestamp: float) -> list[TextChunk]:
        if (
            self._buffer
            and self._buffer_started_at is not None
            and timestamp - self._buffer_started_at >= self.max_wait_seconds
            and self._speakable_count() >= self.min_chars
            and not self._pending_hard_boundary
            and not self._candidate_period
        ):
            split = None
            if self.prefer_sentence_boundaries:
                # Never emit a partial English word on the wait deadline.
                split = self._word_boundary_split()
            return [self._emit(timestamp, keep=split)]
        return []

    def _word_boundary_split(self) -> int | None:
        """Last complete clause/word with enough content; avoid tiny fragments.

        Enumeration marks are not breakpoints: they belong to the list. When
        no safe boundary exists, the configured deadline/length still wins.
        """

        buffer = self._buffer
        for position in range(len(buffer) - 1, 0, -1):
            if (
                buffer[position - 1] in "，：,: \t\n"
                and sum(character.isalnum() for character in buffer[:position]) >= self.min_chars
            ):
                return position
        return None

    def _emit(
        self,
        timestamp: float,
        *,
        is_final: bool = False,
        keep: int | None = None,
    ) -> TextChunk:
        if keep is None:
            text = "".join(self._buffer)
            remainder: list[str] = []
        else:
            if keep <= 0 or keep > len(self._buffer):
                raise ValueError("keep must select a non-empty prefix of the buffer")
            text = "".join(self._buffer[:keep])
            remainder = list(self._buffer[keep:])
        if not text:
            raise RuntimeError("Cannot emit an empty sentence chunk")
        chunk = TextChunk(
            text=text,
            is_final=is_final,
            timestamp_ms=int(timestamp * 1000),
            turn_id=self.turn_id,
            sequence_id=self._next_sequence,
        )
        self._next_sequence += 1
        self._buffer.clear()
        if remainder:
            self._buffer.extend(remainder)
        self._buffer_started_at = timestamp if remainder else None
        self._pending_boundary = False
        self._pending_hard_boundary = False
        self._candidate_period = False
        return chunk

    def _speakable_count(self) -> int:
        return sum(character.isalnum() for character in self._buffer)

    def _now(self, value: float | None) -> float:
        timestamp = float(self._clock() if value is None else value)
        if not isfinite(timestamp):
            raise ValueError("SentenceChunker clock value must be finite")
        if self._last_now is not None and timestamp < self._last_now:
            raise RuntimeError("SentenceChunker clock moved backwards")
        self._last_now = timestamp
        return timestamp
