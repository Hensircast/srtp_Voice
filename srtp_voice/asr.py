from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import AppConfig


SUPPORTED_ASR_BACKENDS = frozenset({"mock", "faster_whisper"})


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
        kwargs = {
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

        try:
            segments, _info = self.model.transcribe(str(wav_path), **kwargs)
            return "".join(segment.text for segment in segments).strip()
        except Exception as exc:
            raise RuntimeError(
                f"ASR backend 'faster_whisper' failed for audio '{wav_path}'."
            ) from exc
