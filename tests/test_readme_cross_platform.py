from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
README_PATH = PROJECT_ROOT / "README.md"


def _readme() -> str:
    return README_PATH.read_text(encoding="utf-8")


def test_readme_covers_windows_and_ubuntu_setup() -> None:
    text = _readme()

    assert "Windows PowerShell" in text
    assert "原生 Ubuntu Bash" in text
    assert r".\.venv\Scripts\Activate.ps1" in text
    assert "source .venv/bin/activate" in text
    assert "portaudio19-dev" in text
    assert "libsndfile1" in text
    assert "ffmpeg" in text.lower()
    assert "--diagnose" in text
    assert "Ubuntu 主机上的真实麦克风、扬声器、串口和模型推理仍待验证" in text


def test_readme_covers_current_modes_and_backends() -> None:
    text = _readme()

    for mode in ("console", "file", "mic", "vad"):
        assert f"--mode {mode}" in text
    assert "--continuous" in text
    assert "--no-play" in text

    for backend in (
        "faster-whisper",
        "SenseVoice",
        "Ollama",
        "LM Studio",
        "edge-tts",
        "Piper",
    ):
        assert backend in text

    assert "| VAD | `energy` |" in text
    assert "| ASR | `mock`、`faster_whisper` |" in text
    assert "| SER | `heuristic`、`sensevoice` |" in text
    assert "| LLM | `mock`、`ollama`、`lmstudio` |" in text
    assert "| TTS | `mock`、`edge_tts`、`piper` |" in text


def test_readme_covers_cross_platform_piper_and_ci() -> None:
    text = _readme()

    assert "tools/piper/piper.exe" in text
    assert "tools/piper/piper" in text
    assert "chmod +x tools/piper/piper" in text
    assert "windows-latest" in text
    assert "ubuntu-latest" in text
    assert "Python 3.11" in text
    assert "CI 不运行真实麦克风" in text
    assert "Windows/Ubuntu CI 离线测试均已覆盖" in text
    assert "CI 通过不能代替真实音频设备和模型工作流验收" in text


def test_readme_preserves_lip_sync_and_serial_boundaries() -> None:
    text = _readme()

    assert "默认每 40 ms" in text
    assert "outputs/serial_packet.json" in text
    assert "main.py` 只生成并保存" in text
    assert "尚未实现 STM32 舵机闭环" in text
    assert "COM3" in text
    assert "/dev/ttyACM0" in text
    assert "/dev/ttyUSB0" in text


def test_readme_documents_v16_emotion_fusion_boundaries() -> None:
    text = _readme()

    assert "V1.6" in text
    assert "`confidence` 表示融合证据强度，不是 SenseVoice 模型概率" in text
    assert "不能可靠区分 `happy/angry` 或 `sad/tired`" in text
    assert "EMOTION_DECAY_HALF_LIFE_SECONDS" in text
    assert "EMOTION_MAX_STEP" in text
    assert "未实现真实流式流水线" in text


def test_readme_does_not_restore_removed_backends_or_settings() -> None:
    text = _readme()
    removed = (
        "sensevoice_onnx",
        "whisper_cpp",
        "ASR_BACKEND=sensevoice",
        "ASR_BACKEND=funasr",
        "SER_BACKEND=custom",
        "TTS_ASYNC",
        "SER_TIMEOUT_SECONDS",
        "SERIAL_ENABLE=",
        "SERIAL_PORT=",
        "SERIAL_BAUDRATE=",
    )

    assert all(value not in text for value in removed)
    assert "STM32 舵机闭环已完成" not in text
