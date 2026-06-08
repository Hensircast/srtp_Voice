from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AppConfig:
    """全局配置。

    V2 对齐学长定稿：
    - 音频采集：16 kHz / mono / 16-bit PCM
    - 对话控制：Idle -> Listening -> Thinking -> Speaking
    - VAD：默认 energy 占位；后续替换 Silero VAD
    - ASR：默认 mock；后续替换 SenseVoiceSmall ONNX INT8
    - TTS：默认 mock；可临时 edge-tts；后续替换 MOSS-TTS-Nano ONNX / Piper
    """

    sample_rate: int = 16000
    output_dir: Path = Path("outputs")
    memory_file: Path = Path("outputs/memory.json")
    state_file: Path = Path("outputs/emotion_state.json")
    max_history_turns: int = 6

    # VAD / 录音控制
    vad_backend: str = "energy"  # energy / silero
    vad_threshold: float = 0.018
    frame_ms: int = 32
    min_speech_ms: int = 400
    silence_ms: int = 800
    max_record_seconds: float = 8.0
    pre_roll_ms: int = 300

    # LLM：默认不用网络，走本地规则模板。
    llm_backend: str = "mock"  # mock / openai_compatible
    llm_base_url: str = "https://api.deepseek.com/v1/chat/completions"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"

    # TTS：默认生成 beep wav，保证代码能跑通。
    # edge_tts 用于联网演示；moss_tts_onnx/piper 为本地模型占位。
    tts_backend: str = "mock"  # mock / edge_tts / moss_tts_onnx / piper
    tts_voice: str = "zh-CN-XiaoxiaoNeural"
    tts_async: bool = False

    # ASR：默认需要手动输入识别文本，后续替换为 SenseVoice / FunASR / Whisper。
    asr_backend: str = "mock"  # mock / sensevoice_onnx / sensevoice / funasr / whisper_cpp

    # 情绪平滑：用简化 Kalman/EMA 占位，接口对齐学长方案。
    emotion_smooth_alpha: float = 0.35

    @classmethod
    def from_env(cls) -> "AppConfig":
        return cls(
            sample_rate=int(os.getenv("SAMPLE_RATE", "16000")),
            output_dir=Path(os.getenv("OUTPUT_DIR", "outputs")),
            memory_file=Path(os.getenv("MEMORY_FILE", "outputs/memory.json")),
            state_file=Path(os.getenv("STATE_FILE", "outputs/emotion_state.json")),
            max_history_turns=int(os.getenv("MAX_HISTORY_TURNS", "6")),
            vad_backend=os.getenv("VAD_BACKEND", "energy"),
            vad_threshold=float(os.getenv("VAD_THRESHOLD", "0.018")),
            frame_ms=int(os.getenv("FRAME_MS", "32")),
            min_speech_ms=int(os.getenv("MIN_SPEECH_MS", "400")),
            silence_ms=int(os.getenv("SILENCE_MS", "800")),
            max_record_seconds=float(os.getenv("MAX_RECORD_SECONDS", "8.0")),
            pre_roll_ms=int(os.getenv("PRE_ROLL_MS", "300")),
            llm_backend=os.getenv("LLM_BACKEND", "mock"),
            llm_base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1/chat/completions"),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_model=os.getenv("LLM_MODEL", "deepseek-chat"),
            tts_backend=os.getenv("TTS_BACKEND", "mock"),
            tts_voice=os.getenv("TTS_VOICE", "zh-CN-XiaoxiaoNeural"),
            tts_async=os.getenv("TTS_ASYNC", "0") in {"1", "true", "True", "yes"},
            asr_backend=os.getenv("ASR_BACKEND", "mock"),
            emotion_smooth_alpha=float(os.getenv("EMOTION_SMOOTH_ALPHA", "0.35")),
        )
