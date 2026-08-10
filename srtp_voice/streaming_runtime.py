from __future__ import annotations

import threading
import uuid
import wave
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from time import monotonic
from typing import Any, Callable, Dict, Iterable, Mapping

from .asr import ASRAdapter
from .audio_io import NoSpeechDetectedError, write_pcm16_wav
from .config import AppConfig
from .llm import StrategyGenerator
from .streaming import (
    AudioChunk,
    LatencyTracker,
    SentenceChunker,
    StreamEvent,
    StreamEventFactory,
    StreamEventType,
    TextChunk,
    TurnTimingSnapshot,
    is_speakable_text,
)
from .streaming_asr import (
    IncrementalASRSession,
    MicrophoneFrameStream,
    PCM16ASRTranscriber,
    StreamingUtteranceCollector,
)
from .streaming_tts import (
    IncrementalTTSPlayer,
    StreamingTTSFailure,
    SynthesizedSpeechChunk,
)
from .tts import TTSAdapter
from .types import EmotionResult, StrategyResult


class TurnCancelledError(RuntimeError):
    pass


@dataclass(frozen=True)
class TurnHandle:
    turn_id: str
    cancelled: threading.Event


@dataclass(frozen=True)
class StreamingTurnResult:
    turn_id: str
    strategy: StrategyResult
    latency: TurnTimingSnapshot
    lip_sync: Dict[str, Any]
    audio_chunks: int
    backpressure_events: int


class StreamingTurnController:
    """Own the active turn, event sequencing, cancellation, and latency data."""

    def __init__(
        self,
        *,
        event_sink: Callable[[StreamEvent], None] | None = None,
        cancel_hook: Callable[[str], None] | None = None,
        history_maxsize: int = 2048,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        if history_maxsize < 1:
            raise ValueError("history_maxsize must be at least 1")
        self.event_sink = event_sink
        self.cancel_hook = cancel_hook
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self._history: deque[StreamEvent] = deque(maxlen=history_maxsize)
        self._tracker = LatencyTracker()
        self._lock = threading.Lock()
        self._active: TurnHandle | None = None
        self._factories: Dict[str, StreamEventFactory] = {}
        self._last_turn_snapshot: TurnTimingSnapshot | None = None
        self.late_events = 0
        self.dropped_history_events = 0
        self.sink_failures = 0

    def start_turn(self) -> TurnHandle:
        self.cancel_active(reason="superseded")
        turn_id = self._id_factory()
        if not turn_id:
            raise ValueError("turn id factory returned an empty identifier")
        handle = TurnHandle(turn_id, threading.Event())
        with self._lock:
            self._active = handle
            self._factories[turn_id] = StreamEventFactory(turn_id)
        event = self.emit(turn_id, StreamEventType.TURN_STARTED)
        if event is None:  # pragma: no cover - only possible under a concurrent start
            raise TurnCancelledError(f"Turn {turn_id} was superseded while starting")
        return handle

    def emit(
        self,
        turn_id: str,
        event_type: StreamEventType | str,
        payload: Mapping[str, Any] | None = None,
    ) -> StreamEvent | None:
        with self._lock:
            handle = self._active
            if handle is None or handle.turn_id != turn_id or handle.cancelled.is_set():
                self.late_events += 1
                return None
            factory = self._factories[turn_id]
            event = factory.emit(event_type, payload)
            self._tracker.observe(event)
        self._store_and_publish(event)
        return event

    def cancel_active(
        self,
        *,
        reason: str = "cancelled",
        expected_turn_id: str | None = None,
    ) -> TurnTimingSnapshot | None:
        with self._lock:
            handle = self._active
            if handle is None:
                return None
            if expected_turn_id is not None and handle.turn_id != expected_turn_id:
                return None
            factory = self._factories[handle.turn_id]
            handle.cancelled.set()
            event = factory.emit(StreamEventType.TURN_CANCELLED, {"reason": reason})
            self._tracker.observe(event)
            self._active = None
            self._factories.pop(handle.turn_id, None)
        self._store_and_publish(event)
        if self.cancel_hook is not None:
            self.cancel_hook(handle.turn_id)
        snapshot = self._tracker.finish_turn(handle.turn_id)
        with self._lock:
            self._last_turn_snapshot = snapshot
        return snapshot

    def fail_turn(self, turn_id: str, exc: Exception) -> TurnTimingSnapshot | None:
        event = self.emit(
            turn_id,
            StreamEventType.ERROR,
            {"error_type": type(exc).__name__, "message": str(exc)},
        )
        if event is None:
            return None
        return self.cancel_active(reason="error", expected_turn_id=turn_id)

    def finish_turn(self, turn_id: str) -> TurnTimingSnapshot:
        event = self.emit(turn_id, StreamEventType.TURN_FINISHED)
        if event is None:
            raise TurnCancelledError(f"Turn {turn_id} is no longer active")
        with self._lock:
            handle = self._active
            if handle is None or handle.turn_id != turn_id:
                raise TurnCancelledError(f"Turn {turn_id} was superseded before completion")
            self._active = None
            self._factories.pop(turn_id, None)
        snapshot = self._tracker.finish_turn(turn_id)
        with self._lock:
            self._last_turn_snapshot = snapshot
        return snapshot

    def is_active(self, turn_id: str) -> bool:
        with self._lock:
            return (
                self._active is not None
                and self._active.turn_id == turn_id
                and not self._active.cancelled.is_set()
            )

    @property
    def active_turn_id(self) -> str | None:
        with self._lock:
            return self._active.turn_id if self._active is not None else None

    @property
    def history(self) -> tuple[StreamEvent, ...]:
        with self._lock:
            return tuple(self._history)

    def latency_summary(self) -> Dict[str, Dict[str, float | int]]:
        return self._tracker.summary()

    @property
    def last_turn_snapshot(self) -> TurnTimingSnapshot | None:
        with self._lock:
            return self._last_turn_snapshot

    def _store_and_publish(self, event: StreamEvent) -> None:
        with self._lock:
            if len(self._history) == self._history.maxlen:
                self.dropped_history_events += 1
            self._history.append(event)
        if self.event_sink is not None:
            try:
                self.event_sink(event)
            except Exception:
                self.sink_failures += 1


class StreamingResponseRuntime:
    """Connect LLM tokens, sentence chunks, incremental TTS, and playback."""

    def __init__(
        self,
        cfg: AppConfig,
        generator: StrategyGenerator,
        tts: TTSAdapter,
        *,
        event_sink: Callable[[StreamEvent], None] | None = None,
        player: Callable[[Path], None] | None = None,
        playback_enabled: bool = True,
        temp_parent: Path | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.cfg = cfg
        self.generator = generator
        self.tts = tts
        self._archive_lock = threading.Lock()
        self._audio_by_turn: Dict[str, list[AudioChunk]] = {}
        self._lip_sync_by_turn: Dict[str, list[Dict[str, Any]]] = {}
        self.controller = StreamingTurnController(
            event_sink=event_sink,
            cancel_hook=self._cancel_tts_turn,
            id_factory=id_factory,
        )
        self._tts_worker = IncrementalTTSPlayer(
            tts,
            queue_maxsize=cfg.stream_tts_queue_size,
            temp_parent=temp_parent,
            player=player,
            playback_enabled=playback_enabled,
            on_audio_ready=self._on_audio_ready,
            on_playback_started=self._on_playback_started,
            on_playback_finished=self._on_playback_finished,
            on_error=self._on_tts_error,
        )
        self._tts_worker.start()
        self._closed = False

    def begin_turn(self) -> TurnHandle:
        if self._closed:
            raise RuntimeError("StreamingResponseRuntime is closed")
        handle = self.controller.start_turn()
        with self._archive_lock:
            self._audio_by_turn[handle.turn_id] = []
            self._lip_sync_by_turn[handle.turn_id] = []
        return handle

    def emit(
        self,
        handle: TurnHandle,
        event_type: StreamEventType | str,
        payload: Mapping[str, Any] | None = None,
    ) -> StreamEvent | None:
        return self.controller.emit(handle.turn_id, event_type, payload)

    def run_response(
        self,
        handle: TurnHandle,
        *,
        user_text: str,
        emotion: EmotionResult,
        history: list[Dict[str, Any]],
        reply_audio: Path,
        action: Dict[str, Any] | None = None,
    ) -> StreamingTurnResult:
        if not self.controller.is_active(handle.turn_id):
            raise TurnCancelledError(f"Turn {handle.turn_id} is not active")
        raw_parts: list[str] = []
        sentence_parts: list[str] = []
        chunker = SentenceChunker(
            turn_id=handle.turn_id,
            min_chars=self.cfg.stream_sentence_min_chars,
            max_chars=self.cfg.stream_sentence_max_chars,
            max_wait_seconds=self.cfg.stream_sentence_max_wait_seconds,
        )
        backpressure_start = self._tts_worker.backpressure_events
        speech_sequence = 0
        previewed_sentence_sequences: set[int] = set()

        def submit_speech(sentence: TextChunk) -> bool:
            nonlocal speech_sequence
            clean_text = sentence.text.strip()
            if not is_speakable_text(clean_text):
                return False
            speakable = TextChunk(
                text=clean_text,
                is_final=sentence.is_final,
                timestamp_ms=sentence.timestamp_ms,
                turn_id=sentence.turn_id,
                sequence_id=speech_sequence,
            )
            speech_sequence += 1
            self._submit_sentence(handle, speakable)
            return True

        def queue_sentence(sentence: TextChunk) -> None:
            sentence_parts.append(sentence.text)
            if sentence.sequence_id in previewed_sentence_sequences:
                return
            submit_speech(sentence)

        def queue_hard_boundary_preview(sentence: TextChunk) -> None:
            if sentence.sequence_id in previewed_sentence_sequences:
                return
            if submit_speech(sentence):
                previewed_sentence_sequences.add(sentence.sequence_id)

        try:
            self.emit(handle, StreamEventType.LLM_REQUEST_STARTED)
            for token in self.generator.generate_stream(
                user_text,
                emotion,
                history,
                turn_id=handle.turn_id,
            ):
                self._raise_if_cancelled(handle)
                if token.text:
                    raw_parts.append(token.text)
                    self.emit(
                        handle,
                        StreamEventType.LLM_TOKEN,
                        {"text": token.text, "token_sequence": token.sequence_id},
                    )
                    for sentence in chunker.feed(token.text):
                        queue_sentence(sentence)
                    preview = chunker.peek_pending_hard_boundary()
                    if preview is not None:
                        queue_hard_boundary_preview(preview)

            reply_text = "".join(raw_parts)
            if not reply_text.strip():
                raise RuntimeError("Streaming LLM produced no speakable reply text")
            for sentence in chunker.finish():
                queue_sentence(sentence)
            if "".join(sentence_parts) != reply_text:
                raise RuntimeError("Sentence chunks do not reconstruct the final reply text")
            if speech_sequence == 0:
                raise RuntimeError("Streaming LLM produced no TTS-speakable reply text")

            self._tts_worker.join()
            self._raise_if_cancelled(handle)
            failures = [
                failure
                for failure in self._tts_worker.failures
                if failure.turn_id == handle.turn_id
            ]
            if failures:
                raise RuntimeError(
                    "Incremental TTS failed: "
                    + "; ".join(str(failure.error) for failure in failures)
                )

            audio_chunks, lip_sync_chunks = self._turn_media(handle.turn_id)
            if not audio_chunks:
                raise RuntimeError("Incremental TTS produced no audio chunks")
            _write_combined_wav(reply_audio, audio_chunks)
            lip_sync = _merge_lip_sync(
                handle.turn_id,
                audio_chunks,
                lip_sync_chunks,
            )
            final_action = self.generator.default_stream_action()
            if action:
                final_action.update(action)
            final_action["lip_sync"] = lip_sync
            strategy = StrategyResult(reply_text=reply_text, action=final_action)
            latency = self.controller.finish_turn(handle.turn_id)
            return StreamingTurnResult(
                turn_id=handle.turn_id,
                strategy=strategy,
                latency=latency,
                lip_sync=lip_sync,
                audio_chunks=len(audio_chunks),
                backpressure_events=(
                    self._tts_worker.backpressure_events - backpressure_start
                ),
            )
        except TurnCancelledError:
            raise
        except Exception as exc:
            if self.controller.is_active(handle.turn_id):
                self.controller.fail_turn(handle.turn_id, exc)
            raise
        finally:
            if not self.controller.is_active(handle.turn_id):
                with self._archive_lock:
                    self._audio_by_turn.pop(handle.turn_id, None)
                    self._lip_sync_by_turn.pop(handle.turn_id, None)

    def cancel_current(self, *, reason: str = "user_cancelled") -> None:
        self.controller.cancel_active(reason=reason)

    @property
    def worker_alive(self) -> bool:
        return self._tts_worker.is_alive

    @property
    def tts_queue_capacity(self) -> int:
        return self._tts_worker.queue_capacity

    @property
    def tts_failures(self) -> tuple[StreamingTTSFailure, ...]:
        return self._tts_worker.failures

    @property
    def tts_backpressure_events(self) -> int:
        return self._tts_worker.backpressure_events

    def close(self, *, drain: bool = False) -> None:
        if self._closed:
            return
        self.cancel_current(reason="runtime_closed")
        self._tts_worker.close(drain=drain)
        self._closed = True
        with self._archive_lock:
            self._audio_by_turn.clear()
            self._lip_sync_by_turn.clear()

    def _submit_sentence(self, handle: TurnHandle, sentence: TextChunk) -> None:
        self._raise_if_cancelled(handle)
        if not is_speakable_text(sentence.text):
            return
        self.emit(
            handle,
            StreamEventType.SENTENCE_READY,
            {"text": sentence.text, "chunk_sequence": sentence.sequence_id},
        )
        self.emit(
            handle,
            StreamEventType.TTS_STARTED,
            {"chunk_sequence": sentence.sequence_id},
        )
        if not self._tts_worker.submit(sentence, block=True):  # pragma: no cover
            raise RuntimeError("Incremental TTS queue rejected a sentence chunk")

    @staticmethod
    def _raise_if_cancelled(handle: TurnHandle) -> None:
        if handle.cancelled.is_set():
            raise TurnCancelledError(f"Turn {handle.turn_id} was cancelled")

    def _cancel_tts_turn(self, turn_id: str) -> None:
        worker = getattr(self, "_tts_worker", None)
        if worker is not None:
            worker.cancel_turn(turn_id)
        archive_lock = getattr(self, "_archive_lock", None)
        if archive_lock is not None:
            with archive_lock:
                self._audio_by_turn.pop(turn_id, None)
                self._lip_sync_by_turn.pop(turn_id, None)

    def _on_audio_ready(self, result: SynthesizedSpeechChunk) -> None:
        event = self.controller.emit(
            result.turn_id,
            StreamEventType.AUDIO_CHUNK_READY,
            {"chunk_sequence": result.sequence, "pcm_bytes": len(result.audio.pcm16)},
        )
        if event is None:
            return
        with self._archive_lock:
            self._audio_by_turn.setdefault(result.turn_id, []).append(result.audio)
            self._lip_sync_by_turn.setdefault(result.turn_id, []).append(result.lip_sync)

    def _on_playback_started(self, result: SynthesizedSpeechChunk) -> None:
        self.controller.emit(
            result.turn_id,
            StreamEventType.PLAYBACK_STARTED,
            {"chunk_sequence": result.sequence},
        )

    def _on_playback_finished(self, result: SynthesizedSpeechChunk) -> None:
        self.controller.emit(
            result.turn_id,
            StreamEventType.PLAYBACK_FINISHED,
            {"chunk_sequence": result.sequence},
        )

    def _on_tts_error(self, failure: StreamingTTSFailure) -> None:
        self.controller.emit(
            failure.turn_id,
            StreamEventType.ERROR,
            {
                "stage": "tts",
                "chunk_sequence": failure.sequence,
                "error_type": type(failure.error).__name__,
                "message": str(failure.error),
            },
        )

    def _turn_media(
        self,
        turn_id: str,
    ) -> tuple[list[AudioChunk], list[Dict[str, Any]]]:
        with self._archive_lock:
            audio = list(self._audio_by_turn.get(turn_id, []))
            lip_sync = list(self._lip_sync_by_turn.get(turn_id, []))
        audio.sort(key=lambda chunk: chunk.sequence_id)
        lip_sync.sort(key=lambda item: int(item.get("chunk_sequence", 0)))
        return audio, lip_sync


def capture_streaming_microphone(
    cfg: AppConfig,
    asr: ASRAdapter,
    runtime: StreamingResponseRuntime,
    handle: TurnHandle,
    output_wav: Path,
    *,
    sounddevice_module: Any | None = None,
) -> str:
    """Capture one VAD-delimited utterance and return only its final ASR text."""

    collector = StreamingUtteranceCollector.from_config(cfg)
    transcriber = PCM16ASRTranscriber(
        asr,
        sample_rate=cfg.sample_rate,
        temp_parent=cfg.output_dir,
    )
    asr_session = IncrementalASRSession(
        transcriber,
        turn_id=handle.turn_id,
        partial_interval_seconds=cfg.stream_asr_partial_interval_seconds,
    )
    microphone = MicrophoneFrameStream(
        sample_rate=cfg.sample_rate,
        frame_ms=cfg.frame_ms,
        queue_maxsize=cfg.stream_audio_queue_size,
        turn_id=handle.turn_id,
        sounddevice_module=sounddevice_module,
    )
    max_frames = max(1, ceil(cfg.max_record_seconds * 1000 / cfg.frame_ms))
    completed_pcm16: bytes | None = None
    partial_future: Future[TextChunk | None] | None = None
    last_partial_submit = float("-inf")

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="srtp-streaming-asr") as executor:
        with microphone:
            for frame_number, frame in enumerate(microphone.chunks(), start=1):
                if handle.cancelled.is_set():
                    raise TurnCancelledError(f"Turn {handle.turn_id} was cancelled")
                runtime.emit(
                    handle,
                    StreamEventType.AUDIO_FRAME,
                    {"frame_sequence": frame.sequence_id, "pcm_bytes": len(frame.pcm16)},
                )
                update = collector.feed(frame)
                if update.vad_started:
                    runtime.emit(handle, StreamEventType.VAD_STARTED)
                if partial_future is not None and partial_future.done():
                    partial = partial_future.result()
                    partial_future = None
                    if partial is not None:
                        runtime.emit(
                            handle,
                            StreamEventType.ASR_PARTIAL,
                            {"text": partial.text, "asr_sequence": partial.sequence_id},
                        )
                now = monotonic()
                if (
                    collector.pcm16
                    and partial_future is None
                    and now - last_partial_submit
                    >= cfg.stream_asr_partial_interval_seconds
                ):
                    partial_future = executor.submit(
                        asr_session.maybe_partial,
                        collector.pcm16,
                    )
                    last_partial_submit = now
                if update.vad_stopped:
                    runtime.emit(handle, StreamEventType.VAD_STOPPED)
                    completed_pcm16 = update.completed_pcm16
                    break
                if frame_number >= max_frames:
                    final_update = collector.finish()
                    if final_update.vad_stopped:
                        runtime.emit(handle, StreamEventType.VAD_STOPPED)
                        completed_pcm16 = final_update.completed_pcm16
                    break

        if partial_future is not None:
            partial = partial_future.result()
            if partial is not None:
                runtime.emit(
                    handle,
                    StreamEventType.ASR_PARTIAL,
                    {"text": partial.text, "asr_sequence": partial.sequence_id},
                )

    if not completed_pcm16:
        raise NoSpeechDetectedError("流式 VAD 未检测到有效语音")
    write_pcm16_wav(output_wav, completed_pcm16, sample_rate=cfg.sample_rate)
    final = asr_session.finalize(completed_pcm16)
    if final is None:  # pragma: no cover - one finalize call in this workflow
        raise RuntimeError("Streaming ASR did not produce a final result")
    runtime.emit(
        handle,
        StreamEventType.ASR_FINAL,
        {"text": final.text, "asr_sequence": final.sequence_id},
    )
    return final.text


def _write_combined_wav(path: Path, chunks: Iterable[AudioChunk]) -> None:
    ordered = list(chunks)
    if not ordered:
        raise ValueError("Cannot write a combined WAV without audio chunks")
    sample_rate = ordered[0].sample_rate
    channels = ordered[0].channels
    if any(
        chunk.sample_rate != sample_rate or chunk.channels != channels
        for chunk in ordered
    ):
        raise ValueError("Incremental TTS audio chunks use incompatible WAV formats")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        for chunk in ordered:
            wav_file.writeframes(chunk.pcm16)


def _merge_lip_sync(
    turn_id: str,
    audio_chunks: list[AudioChunk],
    lip_sync_chunks: list[Dict[str, Any]],
) -> Dict[str, Any]:
    if len(audio_chunks) != len(lip_sync_chunks):
        raise ValueError("Audio and lip-sync chunk counts do not match")
    frames: list[Dict[str, Any]] = []
    offset_ms = 0.0
    stream_sequence = 0
    for audio, lip_sync in zip(audio_chunks, lip_sync_chunks):
        for frame in lip_sync.get("frames", []):
            item = dict(frame)
            item["t_ms"] = round(offset_ms + float(item.get("t_ms", 0)), 3)
            item["stream_sequence"] = stream_sequence
            frames.append(item)
            stream_sequence += 1
        frame_count = len(audio.pcm16) / max(1, 2 * audio.channels)
        offset_ms += frame_count / audio.sample_rate * 1000.0
    return {
        "method": "streaming_short_time_energy",
        "turn_id": turn_id,
        "chunks": lip_sync_chunks,
        "frames": frames,
    }
