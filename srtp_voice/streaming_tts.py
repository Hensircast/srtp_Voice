from __future__ import annotations

import queue
import tempfile
import threading
from time import monotonic
import wave
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Protocol

from .lip_sync import build_energy_lip_sync
from .streaming import AudioChunk, TextChunk, is_speakable_text


class SentenceSynthesizer(Protocol):
    def synthesize(self, text: str, out_wav: Path) -> None:
        ...


class CancellableWavPlayer:
    """sounddevice playback that can be stopped from a barge-in thread."""

    def __init__(self) -> None:
        self._sounddevice: Any | None = None
        self._stop_requested = False
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
            if self._stop_requested:
                self._stop_requested = False
                return
            self._sounddevice = sounddevice
            try:
                sounddevice.play(data, sample_rate)
            except Exception:
                self._sounddevice = None
                raise
        try:
            sounddevice.wait()
        finally:
            with self._lock:
                self._sounddevice = None

    def stop(self) -> None:
        with self._lock:
            sounddevice = self._sounddevice
            if sounddevice is None:
                self._stop_requested = True
                return
        if sounddevice is not None:
            sounddevice.stop()

    def clear_stop_request(self) -> None:
        with self._lock:
            self._stop_requested = False


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
    """Two stages with real work accounting: one synthesis, one playback thread.

    ``submit`` feeds the bounded text queue; the synthesis thread turns text into
    audio and hands it to a single-slot prepared queue, so at most one chunk
    waits ahead of the playing one. ``join`` waits until both queues are drained
    and every playback callback has returned - never on idle worker threads.
    """

    _PREPARED_CAPACITY = 1
    _POLL = 0.05

    def __init__(
        self,
        synthesizer: SentenceSynthesizer,
        *,
        queue_maxsize: int = 4,
        temp_parent: Path | None = None,
        player: Callable[[Path], None] | None = None,
        playback_enabled: bool = True,
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
        self.playback_enabled = playback_enabled
        self.lip_sync_builder = lip_sync_builder
        self.on_audio_ready = on_audio_ready
        self.on_playback_started = on_playback_started
        self.on_playback_finished = on_playback_finished
        self.on_error = on_error
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=queue_maxsize)
        self._prepared: queue.Queue[Any] = queue.Queue(maxsize=self._PREPARED_CAPACITY)
        # One synthesis slot: a chunk is only prepared after the previous one is
        # already playing, which keeps the look-ahead strictly bounded.
        self._synthesis_slot = threading.BoundedSemaphore(1)
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
        self._player_stop_condition = threading.Condition(self._lock)
        self._player_stop_calls_in_progress = 0
        self._synth_thread: threading.Thread | None = None
        self._play_thread: threading.Thread | None = None
        self._close_event = threading.Event()
        self._synth_done = threading.Event()
        self._close_lock = threading.Lock()
        self._accepting = True
        self._closed = False
        self._file_sequence = 0
        self._active_turn_id: str | None = None
        self.dropped_chunks = 0
        self.backpressure_events = 0

    # -- lifecycle --------------------------------------------------------- #
    def start(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("IncrementalTTSPlayer is closed")
            if self._synth_thread is not None:
                return
            synth = threading.Thread(
                target=self._synth_run, name="srtp-streaming-tts-synth", daemon=False
            )
            play = threading.Thread(
                target=self._play_run, name="srtp-streaming-tts-play", daemon=False
            )
            self._synth_thread = synth
            self._play_thread = play
        try:
            synth.start()
            play.start()
        except Exception:
            self.close(drain=False)
            raise

    def submit(
        self,
        chunk: TextChunk,
        *,
        block: bool = False,
        timeout: float | None = None,
    ) -> bool:
        if not isinstance(chunk, TextChunk):
            raise TypeError("submit requires a TextChunk")
        if not is_speakable_text(chunk.text):
            return True
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be non-negative")
        deadline = None if timeout is None else monotonic() + timeout
        counted_backpressure = False
        while True:
            # Admission and shutdown are atomic. A blocking queue.put outside
            # this lock could accept work after close had drained the queue.
            with self._lock:
                if not self._accepting:
                    raise RuntimeError("IncrementalTTSPlayer is closed")
                try:
                    self._queue.put_nowait(chunk)
                    return True
                except queue.Full:
                    if not block or (deadline is not None and monotonic() >= deadline):
                        self.dropped_chunks += 1
                        return False
                    if not counted_backpressure:
                        self.backpressure_events += 1
                        counted_backpressure = True
            remaining = self._POLL if deadline is None else min(self._POLL, max(0, deadline - monotonic()))
            self._close_event.wait(remaining)

    def cancel_turn(self, turn_id: str) -> None:
        if not turn_id:
            return
        with self._lock:
            if turn_id not in self._cancelled_turns:
                if len(self._cancelled_turn_order) >= self._cancelled_turn_limit:
                    expired = self._cancelled_turn_order.popleft()
                    self._cancelled_turns.discard(expired)
                self._cancelled_turns.add(turn_id)
                self._cancelled_turn_order.append(turn_id)
            stop = (
                getattr(self.player, "stop", None)
                if self._active_turn_id == turn_id
                else None
            )
            if callable(stop):
                self._player_stop_calls_in_progress += 1
        if callable(stop):
            try:
                stop()
            finally:
                with self._player_stop_condition:
                    self._player_stop_calls_in_progress -= 1
                    self._player_stop_condition.notify_all()

    def join(self) -> None:
        """Wait for real work: both queues drained and callbacks returned."""

        self._queue.join()
        self._prepared.join()

    def close(self, *, drain: bool = True, timeout: float = 15.0) -> None:
        if threading.current_thread() in (self._synth_thread, self._play_thread):
            raise RuntimeError("close must be called outside a TTS worker callback")
        with self._close_lock:
            with self._lock:
                if self._closed:
                    return
                self._accepting = False
                synth, play = self._synth_thread, self._play_thread
                active_turn = self._active_turn_id
            if not drain or synth is None:
                self._close_event.set()
                self._drain_queue(self._queue)
                self._drain_queue(self._prepared)
                if active_turn is not None:
                    if active_turn:
                        self.cancel_turn(active_turn)
                    else:
                        stop = getattr(self.player, "stop", None)
                        if callable(stop):
                            stop()
            deadline = monotonic() + max(0.1, float(timeout))
            self._finish_close(synth, play, deadline)

    def _finish_close(
        self,
        synth: threading.Thread | None,
        play: threading.Thread | None,
        deadline: float,
    ) -> None:
        for thread in (synth, play):
            if thread is not None and thread.is_alive():
                thread.join(timeout=max(0, deadline - monotonic()))
        leftover = [thread for thread in (synth, play) if thread is not None and thread.is_alive()]
        with self._lock:
            self._closed = not leftover
            if not leftover:
                self._active_turn_id = None
                self._synth_thread = None
                self._play_thread = None
        if leftover:
            self._close_timeout = True
            self._close_event.set()
            raise TimeoutError("TTS workers did not finish within the shutdown timeout")
        self._close_timeout = False
        self._drain_queue(self._queue)
        self._drain_queue(self._prepared)
        clear = getattr(self.player, "clear_stop_request", None)
        if callable(clear):
            try:
                clear()
            except Exception:  # noqa: BLE001
                pass
        self._temp_dir.cleanup()

    # -- workers ----------------------------------------------------------- #
    def _synth_run(self) -> None:
        try:
            while True:
                try:
                    item = self._queue.get(timeout=self._POLL)
                except queue.Empty:
                    with self._lock:
                        if not self._accepting:
                            return
                    continue
                try:
                    if not self._close_event.is_set():
                        self._synthesize(item)
                finally:
                    self._queue.task_done()
        finally:
            self._synth_done.set()

    def _synthesize(self, chunk: TextChunk) -> None:
        if self._is_cancelled(chunk.turn_id):
            return
        with self._lock:
            file_sequence = self._file_sequence
            self._file_sequence += 1
        wav_path = self._temp_path / f"chunk-{file_sequence:08d}.wav"
        while not self._synthesis_slot.acquire(timeout=self._POLL):
            if self._close_event.is_set() or self._is_cancelled(chunk.turn_id):
                return
        transferred = False
        try:
            if self._close_event.is_set() or self._is_cancelled(chunk.turn_id):
                return
            self.synthesizer.synthesize(chunk.text, wav_path)
            if self._is_cancelled(chunk.turn_id) or self._close_event.is_set():
                self._unlink(wav_path)
                return
            audio = _read_audio_chunk(
                wav_path, turn_id=chunk.turn_id, sequence=chunk.sequence_id
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
            while not self._close_event.is_set() and not self._is_cancelled(chunk.turn_id):
                try:
                    self._prepared.put(result, block=True, timeout=self._POLL)
                    transferred = True
                    return
                except queue.Full:
                    continue
            # Close arrived while waiting for the prepared slot: never leak the
            # WAV this thread still owns, and free the synthesis slot.
            self._unlink(wav_path)
        except Exception as exc:
            self._report_failure(chunk.turn_id, chunk.sequence_id, exc)
        finally:
            if not transferred:
                self._unlink(wav_path)
                self._synthesis_slot.release()

    def _play_run(self) -> None:
        while True:
            try:
                item = self._prepared.get(timeout=self._POLL)
            except queue.Empty:
                if self._synth_done.is_set():
                    return
                continue
            try:
                self._play(item)
            finally:
                # Work is reported done only after every callback returned.
                self._prepared.task_done()

    def _play(self, result: SynthesizedSpeechChunk) -> None:
        if self._close_event.is_set() or self._is_cancelled(result.turn_id):
            self._unlink(result.wav_path)
            self._synthesis_slot.release()
            return
        if not self.playback_enabled:
            self._unlink(result.wav_path)
            self._synthesis_slot.release()
            return
        with self._lock:
            if self._close_event.is_set() or result.turn_id in self._cancelled_turns:
                cancelled = True
            else:
                cancelled = False
                self._active_turn_id = result.turn_id
        if cancelled:
            self._unlink(result.wav_path)
            self._synthesis_slot.release()
            return
        # Playback starts now: allow exactly one more sentence to be prepared
        # while this one plays (the prepared queue still serializes the rest).
        self._synthesis_slot.release()
        try:
            if self.on_playback_started is not None:
                self.on_playback_started(result)
            self.player(result.wav_path)
            if (
                not self._is_cancelled(result.turn_id)
                and not self._close_event.is_set()
                and self.on_playback_finished is not None
            ):
                self.on_playback_finished(result)
        except Exception as exc:
            self._report_failure(result.turn_id, result.sequence, exc)
        finally:
            clear_stop_request: Any = None
            with self._player_stop_condition:
                while self._player_stop_calls_in_progress:
                    self._player_stop_condition.wait()
                if self._active_turn_id == result.turn_id:
                    self._active_turn_id = None
                    clear_stop_request = getattr(
                        self.player, "clear_stop_request", None
                    )
            if callable(clear_stop_request):
                clear_stop_request()
            self._unlink(result.wav_path)

    def _report_failure(self, turn_id: str, sequence: int, error: Exception) -> None:
        failure = StreamingTTSFailure(turn_id, sequence, error)
        with self._lock:
            self._failures.append(failure)
        try:
            self.cancel_turn(turn_id)
            if self.on_error is not None:
                self.on_error(failure)
        except Exception as callback_error:
            with self._lock:
                self._failures.append(StreamingTTSFailure(turn_id, sequence, callback_error))

    # -- helpers ----------------------------------------------------------- #
    def _drain_queue(self, target: queue.Queue) -> None:
        while True:
            try:
                item = target.get_nowait()
            except queue.Empty:
                return
            if isinstance(item, SynthesizedSpeechChunk):
                self._unlink(item.wav_path)
                self._synthesis_slot.release()
            target.task_done()

    @staticmethod
    def _unlink(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _is_cancelled(self, turn_id: str) -> bool:
        with self._lock:
            return turn_id in self._cancelled_turns

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
            synth = self._synth_thread
            play = self._play_thread
        return bool(
            (synth is not None and synth.is_alive())
            or (play is not None and play.is_alive())
        )

    @property
    def close_timed_out(self) -> bool:
        return bool(getattr(self, "_close_timeout", False))
