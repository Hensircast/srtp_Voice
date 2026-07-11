from __future__ import annotations

import re
import warnings
from pathlib import Path
from typing import Any, Protocol

from .audio_io import read_wav_pcm16_mono
from .config import AppConfig
from .types import EmotionResult


SER_LABELS = frozenset(
    {
        "neutral",
        "happy",
        "sad",
        "angry",
        "fear",
        "surprise",
        "disgust",
        "tired",
        "excited",
        "unknown",
    }
)

_LABEL_ALIASES = {
    "neutral": "neutral",
    "中性": "neutral",
    "happy": "happy",
    "高兴": "happy",
    "开心": "happy",
    "sad": "sad",
    "悲伤": "sad",
    "难过": "sad",
    "angry": "angry",
    "anger": "angry",
    "生气": "angry",
    "愤怒": "angry",
    "fear": "fear",
    "fearful": "fear",
    "害怕": "fear",
    "surprise": "surprise",
    "surprised": "surprise",
    "惊讶": "surprise",
    "disgust": "disgust",
    "disgusted": "disgust",
    "厌恶": "disgust",
    "tired": "tired",
    "疲惫": "tired",
    "excited": "excited",
    "兴奋": "excited",
    "unknown": "unknown",
    "未知": "unknown",
}

_SENSEVOICE_TAG = re.compile(r"<\|([^|]+)\|>")
_EMOTION_KEYS = ("emotion", "emotion_label", "emo", "label")


def _known_label(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    key = value.strip().lower().replace("-", "_").replace(" ", "_")
    return _LABEL_ALIASES.get(key)


def normalize_emotion_label(value: object) -> str:
    """Map supported Chinese, English, and SenseVoice labels to one label set."""
    if isinstance(value, str):
        for tag in _SENSEVOICE_TAG.findall(value):
            label = _known_label(tag)
            if label is not None:
                return label
        label = _known_label(value)
        if label is not None:
            return label
    return "unknown"


def _extract_sensevoice_label(result: object) -> str:
    def find(value: object, allow_plain_string: bool = False) -> str | None:
        if isinstance(value, dict):
            for key in _EMOTION_KEYS:
                if key in value:
                    label = find(value[key], allow_plain_string=True)
                    if label is not None:
                        return label
            for nested in value.values():
                label = find(nested)
                if label is not None:
                    return label
            return None

        if isinstance(value, (list, tuple)):
            for nested in value:
                label = find(nested)
                if label is not None:
                    return label
            return None

        if not isinstance(value, str):
            return None

        for tag in _SENSEVOICE_TAG.findall(value):
            label = _known_label(tag)
            if label is not None:
                return label
        if allow_plain_string:
            return _known_label(value)
        return None

    label = find(result, allow_plain_string=True)
    if label is None:
        raise ValueError("SenseVoice output does not contain a supported emotion label")
    return label


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


class SERBackend(Protocol):
    def predict(self, wav_path: Path) -> EmotionResult:
        ...


class SERBackendError(RuntimeError):
    def __init__(self, stage: str, message: str, cause: BaseException):
        super().__init__(message)
        self.stage = stage
        self.cause_type = type(cause).__name__


class HeuristicSERBackend:
    """Lightweight RMS/ZCR backend used by default and as an explicit fallback."""

    def predict(self, wav_path: Path) -> EmotionResult:
        path = Path(wav_path)
        if not path.is_file():
            raise FileNotFoundError(f"SER audio file does not exist: {path}")

        sr, samples = read_wav_pcm16_mono(path)
        if not samples:
            return EmotionResult(
                label="neutral",
                intensity=0.30,
                confidence=0.30,
                features={"rms": 0.0, "zcr": 0.0, "duration": 0.0},
            )

        duration = len(samples) / max(sr, 1)
        rms = (sum(s * s for s in samples) / len(samples)) ** 0.5 / 32768.0
        zc = sum(1 for a, b in zip(samples, samples[1:]) if (a >= 0) != (b >= 0))
        zcr = zc / max(len(samples) - 1, 1)

        # Acoustic statistics cannot reliably separate anger from excitement or
        # tiredness from sadness, so the heuristic emits conservative single labels.
        if rms > 0.12 and zcr > 0.08:
            label = "excited"
            intensity = 0.55 + rms * 2.5
            confidence = 0.45
        elif rms < 0.025:
            label = "tired"
            intensity = 0.45
            confidence = 0.40
        else:
            label = "neutral"
            intensity = 0.35
            confidence = 0.50

        return EmotionResult(
            label=label,
            intensity=_clamp01(intensity),
            confidence=_clamp01(confidence),
            features={"rms": float(rms), "zcr": float(zcr), "duration": float(duration)},
        )


class SenseVoiceSERBackend:
    """Local SenseVoice backend with lazy FunASR import and model loading."""

    DEFAULT_MODEL_PATH = Path("models/ser/SenseVoiceSmall")

    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self._model: Any | None = None

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model

        model_path = Path(self.cfg.ser_model) if self.cfg.ser_model else self.DEFAULT_MODEL_PATH
        if not model_path.exists():
            error = FileNotFoundError(
                f"local SenseVoice model was not found at {model_path}; "
                "set SER_MODEL to a downloaded local model directory"
            )
            raise SERBackendError("model loading", str(error), error) from error

        try:
            from funasr import AutoModel
        except (ImportError, ModuleNotFoundError) as exc:
            raise SERBackendError(
                "dependency import",
                "SenseVoice requires FunASR; run: python -m pip install -r requirements-ser.txt",
                exc,
            ) from exc

        try:
            self._model = AutoModel(
                model=str(model_path),
                trust_remote_code=True,
                device=self.cfg.ser_device,
            )
        except Exception as exc:
            raise SERBackendError(
                "model loading",
                f"failed to load local SenseVoice model at {model_path}: {exc}",
                exc,
            ) from exc
        return self._model

    def predict(self, wav_path: Path) -> EmotionResult:
        path = Path(wav_path)
        if not path.is_file():
            error = FileNotFoundError(f"SER audio file does not exist: {path}")
            raise SERBackendError("input validation", str(error), error) from error

        model = self._load_model()
        try:
            result = model.generate(
                input=str(path),
                language=self.cfg.ser_language,
                use_itn=True,
            )
        except Exception as exc:
            raise SERBackendError(
                "inference",
                f"SenseVoice inference failed for {path}: {exc}",
                exc,
            ) from exc

        try:
            label = _extract_sensevoice_label(result)
        except Exception as exc:
            raise SERBackendError(
                "output parsing",
                f"failed to parse SenseVoice emotion output for {path}: {exc}",
                exc,
            ) from exc

        # SenseVoice rich-transcription output does not provide a consistently
        # documented emotion probability, so confidence remains conservative.
        return EmotionResult(
            label=label,
            intensity=0.50,
            confidence=0.50,
            features={},
        )


class SpeechEmotionRecognizer:
    """Configuration-driven entry point for speech emotion recognition."""

    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        backend_name = cfg.ser_backend.strip().lower()
        self.backend_name = backend_name
        self._fallback = HeuristicSERBackend()

        if backend_name == "heuristic":
            self.backend: SERBackend = self._fallback
        elif backend_name == "sensevoice":
            self.backend = SenseVoiceSERBackend(cfg)
        elif backend_name == "custom":
            raise NotImplementedError(
                "SER_BACKEND=custom is reserved for a future explicit backend implementation"
            )
        else:
            raise ValueError(
                f"unsupported SER_BACKEND={cfg.ser_backend!r}; "
                "expected heuristic, sensevoice, or custom"
            )

    def predict(self, wav_path: Path) -> EmotionResult:
        try:
            result = self.backend.predict(Path(wav_path))
        except SERBackendError as exc:
            if self.backend_name != "sensevoice" or not self.cfg.ser_fallback_to_heuristic:
                raise RuntimeError(
                    f"SER backend 'sensevoice' failed during {exc.stage} "
                    f"({exc.cause_type}) for audio '{wav_path}': {exc}"
                ) from exc
            warnings.warn(
                f"SER backend 'sensevoice' failed during {exc.stage} "
                f"({exc.cause_type}); falling back to heuristic: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )
            result = self._fallback.predict(Path(wav_path))

        return EmotionResult(
            label=normalize_emotion_label(result.label),
            intensity=_clamp01(result.intensity),
            confidence=_clamp01(result.confidence),
            features=result.features,
        )
