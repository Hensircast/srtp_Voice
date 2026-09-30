from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import AppConfig


SUPPORTED_ASR_BACKENDS = frozenset({"mock", "faster_whisper"})

# The in-memory path only exists for exactly 16 kHz mono PCM16; anything else
# must keep the caller's WAV fallback instead of being silently resampled.
IN_MEMORY_SAMPLE_RATE = 16000


def _supports_in_memory_rate(sample_rate: object) -> bool:
    return (
        isinstance(sample_rate, int)
        and not isinstance(sample_rate, bool)
        and sample_rate == IN_MEMORY_SAMPLE_RATE
    )


class ASRNoSpeechError(RuntimeError):
    pass


class ASRAdapter:
    """ASR adapter.

    The default mock backend stays lightweight. Real faster-whisper support is
    imported lazily and loads the model once when the adapter is created.
    """

    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.backend = cfg.asr_backend.strip().lower()
        if self.backend not in SUPPORTED_ASR_BACKENDS:
            raise ValueError(
                f"unsupported ASR_BACKEND={cfg.asr_backend!r}; "
                "expected mock or faster_whisper"
            )
        self.model: Any | None = None
        if self.backend == "faster_whisper":
            self.model = self._load_faster_whisper_model()

    def transcribe_pcm16(self, pcm16: bytes, *, sample_rate: int) -> str | None:
        """Transcribe in-memory 16 kHz mono PCM16 without a temporary WAV.

        Returns ``None`` when this backend or rate cannot use the in-memory
        path (the caller keeps its existing WAV fallback); an empty input is a
        valid empty transcript and never reaches the model. The rate must be
        exactly the integer 16000: floats such as ``16000.9`` and strings such
        as ``"16000"`` are treated as unsupported rather than coerced, so no
        silent resampling can happen. The samples are converted with the same
        normalisation faster-whisper uses for decoded audio and are never
        trimmed or resampled.
        """

        if self.backend != "faster_whisper" or not _supports_in_memory_rate(sample_rate):
            return None
        if not pcm16:
            return ""
        if len(pcm16) % 2 != 0:
            raise ValueError("PCM16 audio must contain complete 16-bit samples")
        try:
            import numpy as np
        except ImportError as exc:
            raise RuntimeError(
                "numpy 未安装，无法使用内存 PCM16 直传；"
                "python -m pip install numpy"
            ) from exc
        samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32)
        samples = samples / 32768.0
        return self._transcribe_audio_source(samples, source_label="<pcm16-memory>")

    def transcribe(self, wav_path: Path) -> str:
        if self.backend == "mock":
            print("      [ASR mock] current backend returns placeholder text.")
            return "这是语音识别占位结果"

        audio_path = Path(wav_path)
        if not audio_path.is_file():
            raise FileNotFoundError(f"ASR backend '{self.backend}' audio file not found: {audio_path}")

        if self.backend == "faster_whisper":
            return self._transcribe_faster_whisper(audio_path)

        raise RuntimeError(f"ASR backend dispatch failed: {self.backend}")

    def _load_faster_whisper_model(self):
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper 未安装，请运行：\n"
                "python -m pip install faster-whisper"
            ) from exc

        try:
            return WhisperModel(
                self.cfg.asr_model,
                device=self.cfg.asr_device,
                compute_type=self.cfg.asr_compute_type,
                cpu_threads=self.cfg.asr_cpu_threads,
            )
        except Exception as exc:
            raise RuntimeError(
                "Failed to initialize ASR backend 'faster_whisper' "
                f"with model '{self.cfg.asr_model}'."
            ) from exc

    def _transcribe_faster_whisper(self, wav_path: Path) -> str:
        return self._transcribe_audio_source(str(wav_path), source_label=str(wav_path))

    def _asr_kwargs(self) -> dict[str, Any]:
        """One shared option set for the file and in-memory paths."""

        kwargs: dict[str, Any] = {
            "language": self.cfg.asr_language,
            "task": "transcribe",
            "beam_size": self.cfg.asr_beam_size,
            "vad_filter": self.cfg.asr_vad_filter,
            "condition_on_previous_text": self.cfg.asr_condition_on_previous_text,
        }
        if self.cfg.asr_vad_filter:
            kwargs["vad_parameters"] = {
                "min_silence_duration_ms": self.cfg.asr_min_silence_ms,
            }
        return kwargs

    def _transcribe_audio_source(self, source: Any, *, source_label: str) -> str:
        """Shared generator consumption and error wrapping for both inputs."""

        try:
            segments, _info = self.model.transcribe(source, **self._asr_kwargs())
            return "".join(segment.text for segment in segments).strip()
        except Exception as exc:
            raise RuntimeError(
                f"ASR backend 'faster_whisper' failed for audio '{source_label}'."
            ) from exc
