from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AppConfig:
    sample_rate: int = 16000
    output_dir: Path = Path("outputs")
    memory_file: Path = Path("outputs/memory.json")
    max_history_turns: int = 6

    # LLM 占位配置：默认不用网络，走本地规则模板。
    llm_backend: str = "mock"  # mock / openai_compatible
    llm_base_url: str = "https://api.deepseek.com/v1/chat/completions"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"

    # TTS 占位配置：默认生成 beep wav，保证代码能跑通。
    tts_backend: str = "edge_tts"  # mock / edge_tts / piper
    tts_voice: str = "zh-CN-XiaoxiaoNeural"

    # ASR 占位配置：默认需要手动输入识别文本，后续替换为 SenseVoice / FunASR / Whisper。
    asr_backend: str = "mock"  # mock / sensevoice / funasr / whisper_cpp

    @classmethod
    def from_env(cls) -> "AppConfig":
        return cls(
            sample_rate=int(os.getenv("SAMPLE_RATE", "16000")),
            output_dir=Path(os.getenv("OUTPUT_DIR", "outputs")),
            memory_file=Path(os.getenv("MEMORY_FILE", "outputs/memory.json")),
            max_history_turns=int(os.getenv("MAX_HISTORY_TURNS", "6")),
            llm_backend=os.getenv("LLM_BACKEND", "mock"),
            llm_base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1/chat/completions"),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_model=os.getenv("LLM_MODEL", "deepseek-chat"),
            tts_backend=os.getenv("TTS_BACKEND", "edge_tts"),
            tts_voice=os.getenv("TTS_VOICE", "zh-CN-XiaoxiaoNeural"),
            asr_backend=os.getenv("ASR_BACKEND", "mock"),
        )
