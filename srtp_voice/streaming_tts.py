from __future__ import annotations

import queue
import tempfile
import threading
import wave
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Protocol

from .lip_sync import build_energy_lip_sync
from .streaming import AudioChunk, TextChunk


class SentenceSynthesizer(Protocol):
    def synthesize(self, text: str, out_wav: Path) -> None:
        ...


class CancellableWavPlayer:
    """sounddevice playback that can be stopped from a barge-in thread."""

    def __init__(self) -> None:
        self._sounddevice: Any | None = None
        self._lock = threading.Lock()

    def __call__(self, path: Path) -> None:
        try:
            import sounddevice as sounddevice
            import soundfile as soundfile
        except ImportError:
            print(f"      playback dependencies not installed; audio file generated: {path}")
            return
        data, sample_rate = soundfile.read(str(path), dtype="float32")
        with self._lock:
            self._sounddevice = sounddevice
        try:
            sounddevice.play(data, sample_rate)
            sounddevice.wait()
        finally:
            with self._lock:
                self._sounddevice = None

    def stop(self) -> None:
        with self._lock:
            sounddevice = self._sounddevice
        if sounddevice is not None:
            sounddevice.stop()


@dataclass(frozen=True)
class SynthesizedSpeechChunk:
    turn_id: str
    sequence: int
    text: str
    audio: AudioChunk
    lip_sync: Dict[str, Any]
    wav_path: Path


@dataclass(frozen=True)
class StreamingTTSFailure:
    turn_id: str
    sequence: int
    error: Exception


def attributed_lip_sync(
    lip_sync: Dict[str, Any],
    *,
    turn_id: str,
    chunk_sequence: int,
) -> Dict[str, Any]:
    """Attach turn/chunk ownership to both the envelope and every frame."""

    attributed = dict(lip_sync)
    attributed["turn_id"] = turn_id
    attributed["chunk_sequence"] = chunk_sequence
    frames = []
    for frame_sequence, frame in enumerate(lip_sync.get("frames", [])):
        item = dict(frame)
        item["turn_id"] = turn_id
        item["chunk_sequence"] = chunk_sequence
        item["sequence"] = frame_sequence
        frames.append(item)
    attributed["frames"] = frames
    return attributed


def _read_audio_chunk(path: Path, *, turn_id: str, sequence: int) -> AudioChunk:
    with wave.open(str(path), "rb") as wav_file:
        sample_width = wav_file.getsampwidth()
        if sample_width != 2:
            raise ValueError(
                f"Streaming TTS requires 16-bit PCM WAV output, got {sample_width * 8}-bit"
            )
        channels = wav_file.getnchannels()
        sample_rate = wav_file.getframerate()
        pcm16 = wav_file.readframes(wav_file.getnframes())
    if not pcm16:
        raise ValueError("Streaming TTS produced an empty WAV")
    return AudioChunk(
        pcm16=pcm16,
        sample_rate=sample_rate,
        channels=channels,
        turn_id=turn_id,
        sequence_id=sequence,
    )


class IncrementalTTSPlayer:
    """Synthesize and play sentence chunks on one bounded worker thread."""

    _STOP = object()

    def __init__(
        self,
        synthesizer: SentenceSynthesizer,
        *,
        queue_maxsize: int = 4,
        temp_parent: Path | None = None,
        player: Callable[[Path], None] | None = None,
        lip_sync_builder: Callable[[Path], Dict[str, Any]] = build_energy_lip_sync,
        on_audio_ready: Callable[[SynthesizedSpeechChunk], None] | None = None,
        on_playback_started: Callable[[SynthesizedSpeechChunk], None] | None = None,
        on_playback_finished: Callable[[SynthesizedSpeechChunk], None] | None = None,
        on_error: Callable[[StreamingTTSFailure], None] | None = None,
    ) -> None:
        if isinstance(queue_maxsize, bool) or not isinstance(queue_maxsize, int):
            raise TypeError("queue_maxsize must be an integer")
        if queue_maxsize < 1:
            raise ValueError("queue_maxsize must be at least 1")
        self.synthesizer = synthesizer
        self.player = player or CancellableWavPlayer()
        self.lip_sync_builder = lip_sync_builder
        self.on_audio_ready = on_audio_ready
        self.on_playback_started = on_playback_started
        self.on_playback_finished = on_playback_finished
        self.on_error = on_error
        self._queue: queue.Queue[TextChunk | object] = queue.Queue(maxsize=queue_maxsize)
        self._temp_dir = tempfile.TemporaryDirectory(
            prefix="srtp-voice-stream-",
            dir=str(temp_parent) if temp_parent is not None else None,
        )
        self._temp_path = Path(self._temp_dir.name)
        self._cancelled_turns: set[str] = set()
        self._cancelled_turn_order: deque[str] = deque()
        self._cancelled_turn_limit = max(256, queue_maxsize * 4)
        self._failures: deque[StreamingTTSFailure] = deque(maxlen=256)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._file_sequence = 0
        self._active_turn_id: str | None = None
        self.dropped_chunks = 0
        self.backpressure_events = 0

    def start(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("IncrementalTTSPlayer is closed")
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._run,
                name="srtp-streaming-tts",
                daemon=False,
            )
            self._thread.start()

    def submit(
        self,
        chunk: TextChunk,
        *,
        block: bool = False,
        timeout: float | None = None,
    ) -> bool:
        if not isinstance(chunk, TextChunk):
            raise TypeError("submit requires a TextChunk")
        if not chunk.text:
            return True
        with self._lock:
            if self._closed:
                raise RuntimeError("IncrementalTTSPlayer is closed")
        try:
            if block:
                if self._queue.full():
                    with self._lock:
                        self.backpressure_events += 1
                if timeout is None:
                    self._queue.put(chunk, block=True)
                else:
                    self._queue.put(chunk, block=True, timeout=timeout)
            else:
                self._queue.put_nowait(chunk)
            return True
        except queue.Full:
            with self._lock:
                self.dropped_chunks += 1
            return False

    def cancel_turn(self, turn_id: str) -> None:
        if not turn_id:
            return
        stop = None
        with self._lock:
            if turn_id not in self._cancelled_turns:
                if len(self._cancelled_turn_order) >= self._cancelled_turn_limit:
                    expired = self._cancelled_turn_order.popleft()
                    self._cancelled_turns.discard(expired)
                self._cancelled_turns.add(turn_id)
                self._cancelled_turn_order.append(turn_id)
            if self._active_turn_id == turn_id:
                stop = getattr(self.player, "stop", None)
        if callable(stop):
            stop()

    def join(self) -> None:
        self._queue.join()

    def close(self, *, drain: bool = True) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            thread = self._thread

        if thread is not None:
            if drain:
                self._queue.join()
            else:
                self._discard_pending()
            self._queue.put(self._STOP)
            thread.join()
        else:
            self._discard_pending()
        self._temp_dir.cleanup()

    @property
    def failures(self) -> tuple[StreamingTTSFailure, ...]:
        with self._lock:
            return tuple(self._failures)

    @property
    def queue_capacity(self) -> int:
        return self._queue.maxsize

    @property
    def is_alive(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is self._STOP:
                    return
                if not isinstance(item, TextChunk):
                    continue
                self._process(item)
            finally:
                self._queue.task_done()

    def _process(self, chunk: TextChunk) -> None:
        if self._is_cancelled(chunk.turn_id):
            return
        with self._lock:
            file_sequence = self._file_sequence
            self._file_sequence += 1
        wav_path = self._temp_path / f"chunk-{file_sequence:08d}.wav"
        try:
            self.synthesizer.synthesize(chunk.text, wav_path)
            if self._is_cancelled(chunk.turn_id):
                return
            audio = _read_audio_chunk(
                wav_path,
                turn_id=chunk.turn_id,
                sequence=chunk.sequence_id,
            )
            lip_sync = attributed_lip_sync(
                self.lip_sync_builder(wav_path),
                turn_id=chunk.turn_id,
                chunk_sequence=chunk.sequence_id,
            )
            result = SynthesizedSpeechChunk(
                turn_id=chunk.turn_id,
                sequence=chunk.sequence_id,
                text=chunk.text,
                audio=audio,
                lip_sync=lip_sync,
                wav_path=wav_path,
            )
            if self.on_audio_ready is not None:
                self.on_audio_ready(result)
            if self._is_cancelled(chunk.turn_id):
                return
            if self.on_playback_started is not None:
                self.on_playback_started(result)
            with self._lock:
                self._active_turn_id = chunk.turn_id
            try:
                self.player(wav_path)
            finally:
                with self._lock:
                    if self._active_turn_id == chunk.turn_id:
                        self._active_turn_id = None
            if not self._is_cancelled(chunk.turn_id) and self.on_playback_finished is not None:
                self.on_playback_finished(result)
        except Exception as exc:
            failure = StreamingTTSFailure(chunk.turn_id, chunk.sequence_id, exc)
            with self._lock:
                self._failures.append(failure)
            if self.on_error is not None:
                self.on_error(failure)
        finally:
            try:
                wav_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _is_cancelled(self, turn_id: str) -> bool:
        with self._lock:
            return turn_id in self._cancelled_turns

    def _discard_pending(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return
            else:
                self._queue.task_done()
