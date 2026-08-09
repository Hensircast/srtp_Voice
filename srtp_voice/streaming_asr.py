from __future__ import annotations

import queue
import tempfile
import threading
from collections import deque
from dataclasses import dataclass
from math import ceil, isfinite
from pathlib import Path
from time import monotonic
from typing import Any, Callable, Iterator

from .asr import ASRAdapter
from .audio_io import write_pcm16_wav
from .config import AppConfig
from .streaming import AudioChunk, TextChunk
from .vad import EnergyVAD


class BoundedAudioFrameQueue:
    """Non-blocking callback queue with an explicit drop-newest policy."""

    def __init__(
        self,
        *,
        sample_rate: int,
        channels: int = 1,
        maxsize: int = 32,
        turn_id: str = "",
        clock: Callable[[], float] | None = None,
    ) -> None:
        if maxsize < 1:
            raise ValueError("audio frame queue maxsize must be at least 1")
        self.sample_rate = sample_rate
        self.channels = channels
        self.turn_id = turn_id
        self._clock = clock or monotonic
        self._queue: queue.Queue[AudioChunk] = queue.Queue(maxsize=maxsize)
        self._lock = threading.Lock()
        self._closed = False
        self._next_sequence = 0
        self.dropped_frames = 0

    def put(self, pcm16: bytes) -> bool:
        with self._lock:
            if self._closed:
                return False
            sequence = self._next_sequence
            self._next_sequence += 1
            timestamp = float(self._clock())
        if not isfinite(timestamp):
            raise ValueError("audio frame clock returned a non-finite value")
        chunk = AudioChunk(
            pcm16=bytes(pcm16),
            sample_rate=self.sample_rate,
            channels=self.channels,
            timestamp_ms=int(timestamp * 1000),
            turn_id=self.turn_id,
            sequence_id=sequence,
        )
        try:
            self._queue.put_nowait(chunk)
            return True
        except queue.Full:
            with self._lock:
                self.dropped_frames += 1
            return False

    def get(self, timeout: float | None = None) -> AudioChunk:
        return self._queue.get(timeout=timeout)

    def task_done(self) -> None:
        self._queue.task_done()

    def close(self) -> None:
        with self._lock:
            self._closed = True

    @property
    def empty(self) -> bool:
        return self._queue.empty()

    @property
    def capacity(self) -> int:
        return self._queue.maxsize


class MicrophoneFrameStream:
    """Capture raw PCM frames; the audio callback only copies and enqueues."""

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        frame_ms: int = 32,
        queue_maxsize: int = 32,
        turn_id: str = "",
        sounddevice_module: Any | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.frame_samples = max(1, int(sample_rate * frame_ms / 1000))
        self.frames = BoundedAudioFrameQueue(
            sample_rate=sample_rate,
            maxsize=queue_maxsize,
            turn_id=turn_id,
            clock=clock,
        )
        self._sounddevice = sounddevice_module
        self._stream: Any | None = None
        self._stopped = threading.Event()
        self.callback_calls = 0
        self.overflow_events = 0

    def start(self) -> None:
        if self._stream is not None:
            return
        if self._sounddevice is None:
            try:
                import sounddevice as sounddevice_module
            except ImportError as exc:
                raise RuntimeError(
                    "streaming microphone mode requires sounddevice: "
                    "python -m pip install sounddevice"
                ) from exc
            self._sounddevice = sounddevice_module
        self._stopped.clear()
        self._stream = self._sounddevice.RawInputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            blocksize=self.frame_samples,
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> None:
        stream = self._stream
        if stream is None:
            self.frames.close()
            self._stopped.set()
            return
        try:
            stream.stop()
        finally:
            stream.close()
            self._stream = None
            self.frames.close()
            self._stopped.set()

    def chunks(self, *, timeout: float = 0.1) -> Iterator[AudioChunk]:
        while not self._stopped.is_set() or not self.frames.empty:
            try:
                chunk = self.frames.get(timeout=timeout)
            except queue.Empty:
                continue
            try:
                yield chunk
            finally:
                self.frames.task_done()

    def _callback(self, indata, frame_count, time_info, status) -> None:
        del frame_count, time_info
        self.callback_calls += 1
        if status and getattr(status, "input_overflow", False):
            self.overflow_events += 1
        self.frames.put(bytes(indata))

    def __enter__(self) -> "MicrophoneFrameStream":
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop()


@dataclass(frozen=True)
class VADStreamUpdate:
    vad_started: bool = False
    vad_stopped: bool = False
    completed_pcm16: bytes | None = None


class StreamingUtteranceCollector:
    """Apply calibrated EnergyVAD with pre-roll to queued audio frames."""

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        frame_ms: int = 32,
        threshold: float = 0.004,
        min_speech_ms: int = 160,
        silence_ms: int = 1000,
        pre_roll_ms: int = 400,
        calibration_ms: int = 800,
        noise_multiplier: float = 3.0,
        release_ratio: float = 0.60,
    ) -> None:
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.threshold = threshold
        self.min_speech_frames = max(1, ceil(min_speech_ms / frame_ms))
        self.silence_frames_needed = max(1, ceil(silence_ms / frame_ms))
        self.pre_roll_frames = max(0, ceil(pre_roll_ms / frame_ms))
        self.calibration_frames = max(0, ceil(calibration_ms / frame_ms))
        self.noise_multiplier = max(1.0, noise_multiplier)
        self.release_ratio = min(1.0, max(1e-9, release_ratio))
        self._calibration_rms: list[float] = []
        self._pre_roll: deque[bytes] = deque(
            maxlen=self.pre_roll_frames + self.min_speech_frames
        )
        self._recorded: list[bytes] = []
        self._speech_frames = 0
        self._silence_frames = 0
        self._started = False
        self._completed = False
        self._start_vad: EnergyVAD | None = None
        self._release_vad: EnergyVAD | None = None
        if self.calibration_frames == 0:
            self._configure_vad()

    @classmethod
    def from_config(cls, cfg: AppConfig) -> "StreamingUtteranceCollector":
        return cls(
            sample_rate=cfg.sample_rate,
            frame_ms=cfg.frame_ms,
            threshold=cfg.vad_threshold,
            min_speech_ms=cfg.min_speech_ms,
            silence_ms=cfg.silence_ms,
            pre_roll_ms=cfg.pre_roll_ms,
            calibration_ms=cfg.vad_calibration_ms,
            noise_multiplier=cfg.vad_noise_multiplier,
            release_ratio=cfg.vad_release_ratio,
        )

    def feed(self, frame: AudioChunk) -> VADStreamUpdate:
        if self._completed:
            return VADStreamUpdate()
        if frame.sample_rate != self.sample_rate or frame.channels != 1:
            raise ValueError("VAD stream requires mono audio at the configured sample rate")

        if self._start_vad is None or self._release_vad is None:
            self._calibration_rms.append(EnergyVAD(0.0).predict(frame.pcm16).rms)
            if len(self._calibration_rms) >= self.calibration_frames:
                self._configure_vad()
            return VADStreamUpdate()

        result = (
            self._release_vad.predict(frame.pcm16)
            if self._started
            else self._start_vad.predict(frame.pcm16)
        )
        if not self._started:
            self._pre_roll.append(frame.pcm16)
            if result.is_speech:
                self._speech_frames += 1
                if self._speech_frames >= self.min_speech_frames:
                    self._started = True
                    self._recorded.extend(self._pre_roll)
                    self._silence_frames = 0
                    return VADStreamUpdate(vad_started=True)
            else:
                self._speech_frames = max(0, self._speech_frames - 1)
            return VADStreamUpdate()

        self._recorded.append(frame.pcm16)
        if result.is_speech:
            self._silence_frames = 0
        else:
            self._silence_frames += 1
            if self._silence_frames >= self.silence_frames_needed:
                self._completed = True
                return VADStreamUpdate(
                    vad_stopped=True,
                    completed_pcm16=b"".join(self._recorded),
                )
        return VADStreamUpdate()

    def finish(self) -> VADStreamUpdate:
        if self._completed or not self._started:
            return VADStreamUpdate()
        self._completed = True
        return VADStreamUpdate(
            vad_stopped=True,
            completed_pcm16=b"".join(self._recorded),
        )

    @property
    def pcm16(self) -> bytes:
        return b"".join(self._recorded)

    def _configure_vad(self) -> None:
        ordered = sorted(self._calibration_rms)
        if ordered:
            index = max(0, min(len(ordered) - 1, ceil(len(ordered) * 0.95) - 1))
            noise_floor = ordered[index]
        else:
            noise_floor = 0.0
        start_threshold = max(self.threshold, noise_floor * self.noise_multiplier)
        self._start_vad = EnergyVAD(start_threshold)
        self._release_vad = EnergyVAD(start_threshold * self.release_ratio)


class PCM16ASRTranscriber:
    """Adapt the existing whole-WAV ASR implementation to PCM snapshots."""

    def __init__(
        self,
        adapter: ASRAdapter,
        *,
        sample_rate: int = 16000,
        temp_parent: Path | None = None,
    ) -> None:
        self.adapter = adapter
        self.sample_rate = sample_rate
        self.temp_parent = temp_parent

    def __call__(self, pcm16: bytes) -> str:
        if not pcm16:
            return ""
        if self.temp_parent is not None:
            self.temp_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="srtp-voice-asr-",
            dir=str(self.temp_parent) if self.temp_parent is not None else None,
        ) as temp_dir:
            wav_path = Path(temp_dir) / "snapshot.wav"
            write_pcm16_wav(wav_path, pcm16, sample_rate=self.sample_rate)
            return self.adapter.transcribe(wav_path)


class IncrementalASRSession:
    """Emit changing partial hypotheses and exactly one final transcript."""

    def __init__(
        self,
        transcribe_pcm16: Callable[[bytes], str],
        *,
        turn_id: str,
        partial_interval_seconds: float = 0.8,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not turn_id:
            raise ValueError("IncrementalASRSession requires a non-empty turn_id")
        if partial_interval_seconds <= 0 or not isfinite(partial_interval_seconds):
            raise ValueError("partial_interval_seconds must be finite and greater than zero")
        self.transcribe_pcm16 = transcribe_pcm16
        self.turn_id = turn_id
        self.partial_interval_seconds = partial_interval_seconds
        self._clock = clock or monotonic
        self._last_partial_at: float | None = None
        self._last_partial_text = ""
        self._next_sequence = 0
        self._finalized = False
        self._last_timestamp: float | None = None

    def maybe_partial(
        self,
        pcm16: bytes,
        *,
        now: float | None = None,
    ) -> TextChunk | None:
        if self._finalized or not pcm16:
            return None
        timestamp = self._timestamp(now)
        if (
            self._last_partial_at is not None
            and timestamp - self._last_partial_at < self.partial_interval_seconds
        ):
            return None
        self._last_partial_at = timestamp
        text = self.transcribe_pcm16(pcm16).strip()
        if not text or text == self._last_partial_text:
            return None
        self._last_partial_text = text
        return self._chunk(text, is_final=False, timestamp=timestamp)

    def finalize(
        self,
        pcm16: bytes,
        *,
        now: float | None = None,
    ) -> TextChunk | None:
        if self._finalized:
            return None
        timestamp = self._timestamp(now)
        text = self.transcribe_pcm16(pcm16).strip() if pcm16 else ""
        self._finalized = True
        return self._chunk(text, is_final=True, timestamp=timestamp)

    def _chunk(self, text: str, *, is_final: bool, timestamp: float) -> TextChunk:
        chunk = TextChunk(
            text=text,
            is_final=is_final,
            timestamp_ms=int(timestamp * 1000),
            turn_id=self.turn_id,
            sequence_id=self._next_sequence,
        )
        self._next_sequence += 1
        return chunk

    def _timestamp(self, value: float | None) -> float:
        timestamp = float(self._clock() if value is None else value)
        if not isfinite(timestamp):
            raise ValueError("ASR clock returned a non-finite value")
        if self._last_timestamp is not None and timestamp < self._last_timestamp:
            raise RuntimeError("ASR clock moved backwards")
        self._last_timestamp = timestamp
        return timestamp
