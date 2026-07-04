from pathlib import Path


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
