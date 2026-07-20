from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_text(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        return default
    stripped = value.strip()
    return stripped if stripped else default


def derive_url(base_url: str, endpoint: str) -> str:
    return f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"


def is_windows_platform(system_name: str | None = None) -> bool:
    name = platform.system() if system_name is None else system_name
    return name.strip().lower() == "windows"


def default_piper_executable(system_name: str | None = None) -> Path:
    filename = "piper.exe" if is_windows_platform(system_name) else "piper"
    return Path("tools") / "piper" / filename


@dataclass
class AppConfig:
    """全局配置。

    - 音频采集：16 kHz / mono / 16-bit PCM
    - 对话控制：Idle -> Listening -> Thinking -> Speaking
    - VAD：EnergyVAD
    - ASR：mock / faster_whisper
    - SER：heuristic / sensevoice
    - TTS：mock / edge_tts / piper
    """

    sample_rate: int = 16000
    output_dir: Path = Path("outputs")
    memory_file: Path = Path("outputs/memory.json")
    state_file: Path = Path("outputs/emotion_state.json")
    max_history_turns: int = 3

    # VAD / 录音控制
    vad_backend: str = "energy"
    vad_threshold: float = 0.004
    frame_ms: int = 32
    min_speech_ms: int = 160
    silence_ms: int = 1000
    max_record_seconds: float = 15.0
    pre_roll_ms: int = 400
    vad_calibration_ms: int = 800
    vad_noise_multiplier: float = 3.0
    vad_release_ratio: float = 0.60
    vad_debug: bool = False

    # LLM uses local runtimes only. Ollama is the default runtime.
    llm_backend: str = "ollama"  # mock / ollama / lmstudio
    llm_model: str = "qwen3:4b-instruct"
    llm_ollama_base_url: str = "http://localhost:11434"
    llm_ollama_chat_url: str = "http://localhost:11434/api/chat"
    llm_lmstudio_base_url: str = "http://localhost:1234"
    llm_lmstudio_chat_url: str = "http://localhost:1234/v1/chat/completions"
    llm_fallback_to_mock: bool = False
    llm_temperature: float = 0.0
    llm_max_tokens: int = 512
    llm_context_tokens: int = 8192
    llm_timeout_seconds: int = 180

    # TTS: mock beep WAV, edge-tts, or local Piper.
    tts_backend: str = "mock"  # mock / edge_tts / piper
    tts_voice: str = "zh-CN-XiaoxiaoNeural"
    tts_piper_exe: Path = field(default_factory=default_piper_executable)
    tts_piper_model: Path = Path("models/piper/zh_CN-huayan-medium/model.onnx")
    tts_piper_config: Path | None = None
    tts_piper_timeout_seconds: int = 60
    tts_piper_extra_args: str | None = None
    tts_piper_espeak_data: Path | None = None
    tts_piper_use_json_input: bool = False

    # ASR: lightweight mock or local faster-whisper.
    asr_backend: str = "mock"  # mock / faster_whisper
    asr_model: str = "small"
    asr_device: str = "cpu"
    asr_compute_type: str = "int8"
    asr_language: str = "zh"
    asr_cpu_threads: int = 4
    asr_beam_size: int = 1
    asr_vad_filter: bool = True
    asr_min_silence_ms: int = 500
    asr_condition_on_previous_text: bool = False

    # SER: lightweight heuristic by default; SenseVoice uses a local model only.
    ser_backend: str = "heuristic"  # heuristic / sensevoice
    ser_model: Path | None = None
    ser_device: str = "cpu"
    ser_language: str = "zh"
    ser_fallback_to_heuristic: bool = True

    # 情绪平滑使用 EMA。
    emotion_smooth_alpha: float = 0.35

    @classmethod
    def from_env(cls) -> "AppConfig":
        if load_dotenv is not None:
            project_root = Path(__file__).resolve().parents[1]
            load_dotenv(project_root / ".env")

        ollama_base_url = env_text("LLM_OLLAMA_BASE_URL", "http://localhost:11434")
        ollama_chat_url = env_text("LLM_OLLAMA_CHAT_URL") or derive_url(ollama_base_url, "/api/chat")
        lmstudio_base_url = env_text("LLM_LMSTUDIO_BASE_URL", "http://localhost:1234")
        lmstudio_chat_url = env_text("LLM_LMSTUDIO_CHAT_URL") or derive_url(
            lmstudio_base_url,
            "/v1/chat/completions",
        )

        return cls(
            sample_rate=int(os.getenv("SAMPLE_RATE", "16000")),
            output_dir=Path(os.getenv("OUTPUT_DIR", "outputs")),
            memory_file=Path(os.getenv("MEMORY_FILE", "outputs/memory.json")),
            state_file=Path(os.getenv("STATE_FILE", "outputs/emotion_state.json")),
            max_history_turns=int(os.getenv("MAX_HISTORY_TURNS", "3")),
            vad_backend=os.getenv("VAD_BACKEND", "energy"),
            vad_threshold=max(float(os.getenv("VAD_THRESHOLD", "0.004")), 1e-9),
            frame_ms=int(os.getenv("FRAME_MS", "32")),
            min_speech_ms=max(1, int(os.getenv("MIN_SPEECH_MS", "160"))),
            silence_ms=max(1, int(os.getenv("SILENCE_MS", "1000"))),
            max_record_seconds=max(0.001, float(os.getenv("MAX_RECORD_SECONDS", "15"))),
            pre_roll_ms=max(0, int(os.getenv("PRE_ROLL_MS", "400"))),
            vad_calibration_ms=max(0, int(os.getenv("VAD_CALIBRATION_MS", "800"))),
            vad_noise_multiplier=max(1.0, float(os.getenv("VAD_NOISE_MULTIPLIER", "3.0"))),
            vad_release_ratio=min(1.0, max(1e-9, float(os.getenv("VAD_RELEASE_RATIO", "0.60")))),
            vad_debug=env_bool("VAD_DEBUG", False),
            llm_backend=os.getenv("LLM_BACKEND", "ollama"),
            llm_model=os.getenv("LLM_MODEL", "qwen3:4b-instruct"),
            llm_ollama_base_url=ollama_base_url,
            llm_ollama_chat_url=ollama_chat_url,
            llm_lmstudio_base_url=lmstudio_base_url,
            llm_lmstudio_chat_url=lmstudio_chat_url,
            llm_fallback_to_mock=env_bool("LLM_FALLBACK_TO_MOCK", False),
            llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0")),
            llm_max_tokens=int(os.getenv("LLM_MAX_TOKENS", "512")),
            llm_context_tokens=int(os.getenv("LLM_CONTEXT_TOKENS", "8192")),
            llm_timeout_seconds=int(os.getenv("LLM_TIMEOUT_SECONDS", "180")),
            tts_backend=os.getenv("TTS_BACKEND", "mock"),
            tts_voice=os.getenv("TTS_VOICE", "zh-CN-XiaoxiaoNeural"),
            tts_piper_exe=Path(
                env_text("TTS_PIPER_EXE", str(default_piper_executable()))
            ),
            tts_piper_model=Path(env_text("TTS_PIPER_MODEL", "models/piper/zh_CN-huayan-medium/model.onnx")),
            tts_piper_config=Path(value) if (value := env_text("TTS_PIPER_CONFIG")) else None,
            tts_piper_timeout_seconds=max(1, int(os.getenv("TTS_PIPER_TIMEOUT_SECONDS", "60"))),
            tts_piper_extra_args=env_text("TTS_PIPER_EXTRA_ARGS"),
            tts_piper_espeak_data=Path(value) if (value := env_text("TTS_PIPER_ESPEAK_DATA")) else None,
            tts_piper_use_json_input=env_bool("TTS_PIPER_USE_JSON_INPUT", False),
            asr_backend=os.getenv("ASR_BACKEND", "mock"),
            asr_model=env_text("ASR_MODEL", "small"),
            asr_device=env_text("ASR_DEVICE", "cpu"),
            asr_compute_type=env_text("ASR_COMPUTE_TYPE", "int8"),
            asr_language=env_text("ASR_LANGUAGE", "zh"),
            asr_cpu_threads=max(1, int(os.getenv("ASR_CPU_THREADS", "4"))),
            asr_beam_size=max(1, int(os.getenv("ASR_BEAM_SIZE", "1"))),
            asr_vad_filter=env_bool("ASR_VAD_FILTER", True),
            asr_min_silence_ms=max(1, int(os.getenv("ASR_MIN_SILENCE_MS", "500"))),
            asr_condition_on_previous_text=env_bool("ASR_CONDITION_ON_PREVIOUS_TEXT", False),
            ser_backend=env_text("SER_BACKEND", "heuristic"),
            ser_model=Path(value) if (value := env_text("SER_MODEL")) else None,
            ser_device=env_text("SER_DEVICE", "cpu"),
            ser_language=env_text("SER_LANGUAGE", "zh"),
            ser_fallback_to_heuristic=env_bool("SER_FALLBACK_TO_HEURISTIC", True),
            emotion_smooth_alpha=float(os.getenv("EMOTION_SMOOTH_ALPHA", "0.35")),
        )
