from pathlib import Path

from srtp_voice.config import AppConfig


def test_env_example_has_no_bom() -> None:
    data = Path(".env.example").read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf")
    assert data.startswith(b"SAMPLE_RATE=16000")


def test_env_example_first_line() -> None:
    first_line = Path(".env.example").read_text(encoding="utf-8").splitlines()[0]
    assert first_line == "SAMPLE_RATE=16000"


def test_env_example_leaves_chat_urls_unset() -> None:
    lines = [
        line.strip()
        for line in Path(".env.example").read_text(encoding="utf-8").splitlines()
    ]
    active = [line for line in lines if line and not line.startswith("#")]

    assert "LLM_OLLAMA_BASE_URL=http://localhost:11434" in active
    assert "LLM_LMSTUDIO_BASE_URL=http://localhost:1234" in active
    assert not any(line.startswith("LLM_OLLAMA_CHAT_URL=") for line in active)
    assert not any(line.startswith("LLM_LMSTUDIO_CHAT_URL=") for line in active)
    assert any(line.startswith("# LLM_OLLAMA_CHAT_URL=") for line in lines)
    assert any(line.startswith("# LLM_LMSTUDIO_CHAT_URL=") for line in lines)


def test_env_example_uses_qwen_instruct_model() -> None:
    lines = [
        line.strip()
        for line in Path(".env.example").read_text(encoding="utf-8").splitlines()
    ]
    active = [line for line in lines if line and not line.startswith("#")]

    assert "LLM_MODEL=qwen3:4b-instruct" in active
    assert "LLM_MODEL=qwen3:4b" not in active


def test_tts_async_is_not_exposed(monkeypatch) -> None:
    import srtp_voice.config as config_module

    text = Path(".env.example").read_text(encoding="utf-8")
    assert "TTS_ASYNC" not in text

    monkeypatch.setattr(config_module, "load_dotenv", None)
    monkeypatch.setenv("TTS_ASYNC", "1")
    cfg = AppConfig.from_env()
    assert not hasattr(cfg, "tts_async")
