from __future__ import annotations

import importlib
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from .config import AppConfig
from .ser import SenseVoiceSERBackend


def _error_context(exc: Exception, limit: int = 240) -> str:
    message = " ".join(str(exc).split())
    if len(message) > limit:
        message = message[: limit - 3] + "..."
    return f"{type(exc).__name__}: {message or '<no message>'}"


def _warning_device(error: str, index: object = None) -> dict[str, Any]:
    return {
        "status": "warning",
        "index": index,
        "name": None,
        "channels": None,
        "sample_rate": None,
        "error": error,
    }


def _collect_device(sounddevice: Any, index: object, kind: str) -> dict[str, Any]:
    try:
        normalized_index = int(index) if index is not None else None
    except (TypeError, ValueError):
        normalized_index = index

    if normalized_index is None or (
        isinstance(normalized_index, int) and normalized_index < 0
    ):
        return _warning_device(f"No default {kind} audio device", normalized_index)

    try:
        info = sounddevice.query_devices(normalized_index, kind=kind)
    except Exception as exc:
        return _warning_device(_error_context(exc), normalized_index)

    channel_key = "max_input_channels" if kind == "input" else "max_output_channels"
    return {
        "status": "ok",
        "index": normalized_index,
        "name": info.get("name", "unknown"),
        "channels": info.get(channel_key),
        "sample_rate": info.get("default_samplerate"),
        "error": None,
    }


def _collect_audio_environment() -> dict[str, Any]:
    audio: dict[str, Any] = {}

    try:
        soundfile = importlib.import_module("soundfile")
    except Exception as exc:
        audio["soundfile"] = {
            "status": "warning",
            "available": False,
            "version": None,
            "error": _error_context(exc),
        }
    else:
        audio["soundfile"] = {
            "status": "ok",
            "available": True,
            "version": getattr(soundfile, "__version__", "unknown"),
            "error": None,
        }

    try:
        sounddevice = importlib.import_module("sounddevice")
    except Exception as exc:
        context = _error_context(exc)
        audio["sounddevice"] = {
            "status": "warning",
            "available": False,
            "version": None,
            "error": context,
        }
        audio["portaudio"] = {
            "status": "warning",
            "version": None,
            "text": None,
            "error": f"sounddevice unavailable: {context}",
        }
        audio["default_input"] = _warning_device(
            f"sounddevice unavailable: {context}"
        )
        audio["default_output"] = _warning_device(
            f"sounddevice unavailable: {context}"
        )
        return audio

    audio["sounddevice"] = {
        "status": "ok",
        "available": True,
        "version": getattr(sounddevice, "__version__", "unknown"),
        "error": None,
    }

    try:
        portaudio_version = sounddevice.get_portaudio_version()
        if isinstance(portaudio_version, tuple):
            version = portaudio_version[0] if portaudio_version else None
            version_text = portaudio_version[1] if len(portaudio_version) > 1 else None
        else:
            version = portaudio_version
            version_text = str(portaudio_version)
        audio["portaudio"] = {
            "status": "ok",
            "version": version,
            "text": version_text,
            "error": None,
        }
    except Exception as exc:
        audio["portaudio"] = {
            "status": "warning",
            "version": None,
            "text": None,
            "error": _error_context(exc),
        }

    try:
        default_devices = sounddevice.default.device
        input_index = default_devices[0]
        output_index = default_devices[1]
    except Exception as exc:
        context = _error_context(exc)
        audio["default_input"] = _warning_device(context)
        audio["default_output"] = _warning_device(context)
    else:
        audio["default_input"] = _collect_device(
            sounddevice,
            input_index,
            "input",
        )
        audio["default_output"] = _collect_device(
            sounddevice,
            output_index,
            "output",
        )

    return audio


def _ser_model_path(cfg: AppConfig) -> Path | None:
    if cfg.ser_model:
        return Path(cfg.ser_model)
    if cfg.ser_backend.strip().lower() == "sensevoice":
        return SenseVoiceSERBackend.DEFAULT_MODEL_PATH
    return None


def _path_diagnostic(path: Path | None) -> dict[str, Any]:
    return {
        "path": str(path) if path is not None else None,
        "exists": path.exists() if path is not None else False,
    }


def collect_diagnostics(cfg: AppConfig) -> dict[str, Any]:
    """Collect read-only platform, backend, path, and audio diagnostics."""
    ffmpeg = shutil.which("ffmpeg")
    return {
        "system": {
            "os": platform.system(),
            "platform": platform.platform(),
            "python": sys.version.split()[0],
        },
        "config": {
            "sample_rate": cfg.sample_rate,
            "vad_backend": cfg.vad_backend,
            "asr_backend": cfg.asr_backend,
            "asr_model": cfg.asr_model,
            "ser_backend": cfg.ser_backend,
            "llm_backend": cfg.llm_backend,
            "llm_model": cfg.llm_model,
            "tts_backend": cfg.tts_backend,
        },
        "paths": {
            "ser_model": _path_diagnostic(_ser_model_path(cfg)),
            "piper_executable": _path_diagnostic(Path(cfg.tts_piper_exe)),
            "piper_model": _path_diagnostic(Path(cfg.tts_piper_model)),
        },
        "ffmpeg": {
            "status": "ok" if ffmpeg else "warning",
            "path": ffmpeg,
        },
        "audio": _collect_audio_environment(),
    }


def _print_dependency(label: str, data: dict[str, Any]) -> None:
    status = data["status"].upper()
    if data["status"] == "ok":
        print(f"[{status}] {label}: version={data.get('version')}")
    else:
        print(f"[{status}] {label}: {data.get('error')}")


def _print_device(label: str, data: dict[str, Any]) -> None:
    status = data["status"].upper()
    if data["status"] == "ok":
        print(
            f"[{status}] {label}: index={data['index']}, name={data['name']}, "
            f"channels={data['channels']}, sample_rate={data['sample_rate']}"
        )
    else:
        print(f"[{status}] {label}: index={data.get('index')}, {data.get('error')}")


def print_diagnostics(data: dict[str, Any]) -> None:
    """Print collected diagnostics without exposing environment secrets."""
    system = data["system"]
    config = data["config"]
    paths = data["paths"]
    audio = data["audio"]

    print("=== SRTP Voice Diagnostics ===")
    print(f"[INFO] OS: {system['os']}")
    print(f"[INFO] Platform: {system['platform']}")
    print(f"[INFO] Python: {system['python']}")
    print(f"[INFO] Sample rate: {config['sample_rate']} Hz")
    print(f"[INFO] VAD: {config['vad_backend']}")
    print(f"[INFO] ASR: {config['asr_backend']} / {config['asr_model']}")
    print(
        f"[INFO] SER: {config['ser_backend']} / "
        f"{paths['ser_model']['path'] or '<not configured>'} "
        f"(exists={paths['ser_model']['exists']})"
    )
    print(f"[INFO] LLM: {config['llm_backend']} / {config['llm_model']}")
    print(f"[INFO] TTS: {config['tts_backend']}")
    print(
        f"[INFO] Piper executable: {paths['piper_executable']['path']} "
        f"(exists={paths['piper_executable']['exists']})"
    )
    print(
        f"[INFO] Piper model: {paths['piper_model']['path']} "
        f"(exists={paths['piper_model']['exists']})"
    )
    ffmpeg = data["ffmpeg"]
    print(f"[{ffmpeg['status'].upper()}] FFmpeg: {ffmpeg['path'] or 'not found'}")
    _print_dependency("sounddevice", audio["sounddevice"])
    _print_dependency("soundfile", audio["soundfile"])

    portaudio = audio["portaudio"]
    if portaudio["status"] == "ok":
        print(
            f"[OK] PortAudio: version={portaudio['version']}, "
            f"text={portaudio['text']}"
        )
    else:
        print(f"[WARNING] PortAudio: {portaudio['error']}")

    _print_device("Default input device", audio["default_input"])
    _print_device("Default output device", audio["default_output"])
