from __future__ import annotations

from pathlib import Path

import srtp_voice.config as config_module
from srtp_voice.config import AppConfig


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _disable_dotenv(monkeypatch) -> None:
    monkeypatch.setattr(config_module, "load_dotenv", None)


def _set_path_environment(monkeypatch, paths: dict[str, Path]) -> None:
    for name, path in paths.items():
        monkeypatch.setenv(name, str(path))


def test_from_env_loads_project_dotenv_outside_current_directory(
    monkeypatch,
    tmp_path,
) -> None:
    outside = tmp_path / "outside-project"
    outside.mkdir()
    monkeypatch.chdir(outside)
    calls = []

    def fake_load_dotenv(dotenv_path, *args, **kwargs):
        calls.append((dotenv_path, args, kwargs))
        return True

    monkeypatch.setattr(config_module, "load_dotenv", fake_load_dotenv)

    AppConfig.from_env()

    assert len(calls) == 1
    assert calls[0][0] == PROJECT_ROOT / ".env"
    assert calls[0][1] == ()
    assert calls[0][2].get("override", False) is False
    assert isinstance(calls[0][0], Path)
    assert Path.cwd() == outside
    assert not (outside / ".env").exists()
    assert not (outside / "outputs").exists()


def test_shell_environment_keeps_priority_over_dotenv(monkeypatch) -> None:
    shell_output = Path("shell-config") / "outputs"
    dotenv_output = Path("dotenv-config") / "outputs"
    monkeypatch.setenv("OUTPUT_DIR", str(shell_output))
    calls = []

    def fake_load_dotenv(dotenv_path, *args, **kwargs):
        calls.append((dotenv_path, kwargs))
        if kwargs.get("override", False) or "OUTPUT_DIR" not in config_module.os.environ:
            config_module.os.environ["OUTPUT_DIR"] = str(dotenv_output)
        return True

    monkeypatch.setattr(config_module, "load_dotenv", fake_load_dotenv)

    cfg = AppConfig.from_env()

    assert cfg.output_dir == shell_output
    assert calls[0][0] == PROJECT_ROOT / ".env"
    assert calls[0][1].get("override", False) is False


def test_from_env_without_python_dotenv_uses_os_environment(monkeypatch) -> None:
    _disable_dotenv(monkeypatch)
    monkeypatch.setenv("SAMPLE_RATE", "22050")
    monkeypatch.setenv("OUTPUT_DIR", str(Path("runtime") / "audio-output"))
    monkeypatch.setenv("SER_MODEL", str(Path("models") / "ser" / "local-model"))

    cfg = AppConfig.from_env()

    assert cfg.sample_rate == 22050
    assert cfg.output_dir == Path("runtime") / "audio-output"
    assert cfg.ser_model == Path("models") / "ser" / "local-model"


def test_relative_local_paths_remain_path_objects(monkeypatch) -> None:
    _disable_dotenv(monkeypatch)
    configured_paths = {
        "OUTPUT_DIR": Path("runtime") / "outputs",
        "MEMORY_FILE": Path("runtime") / "state" / "memory.json",
        "STATE_FILE": Path("runtime") / "state" / "emotion.json",
        "TTS_PIPER_EXE": Path("tools") / "piper" / "piper",
        "TTS_PIPER_MODEL": Path("models") / "piper" / "voice.onnx",
        "TTS_PIPER_CONFIG": Path("models") / "piper" / "voice.onnx.json",
        "TTS_PIPER_ESPEAK_DATA": Path("tools") / "piper" / "espeak-ng-data",
        "SER_MODEL": Path("models") / "ser" / "SenseVoiceSmall",
    }
    _set_path_environment(monkeypatch, configured_paths)

    cfg = AppConfig.from_env()
    actual_paths = {
        "OUTPUT_DIR": cfg.output_dir,
        "MEMORY_FILE": cfg.memory_file,
        "STATE_FILE": cfg.state_file,
        "TTS_PIPER_EXE": cfg.tts_piper_exe,
        "TTS_PIPER_MODEL": cfg.tts_piper_model,
        "TTS_PIPER_CONFIG": cfg.tts_piper_config,
        "TTS_PIPER_ESPEAK_DATA": cfg.tts_piper_espeak_data,
        "SER_MODEL": cfg.ser_model,
    }

    assert actual_paths == configured_paths
    assert all(isinstance(path, Path) for path in actual_paths.values())
    assert all(not path.is_absolute() for path in actual_paths.values())


def test_absolute_local_paths_remain_absolute_and_loading_creates_nothing(
    monkeypatch,
    tmp_path,
) -> None:
    _disable_dotenv(monkeypatch)
    configured_paths = {
        "OUTPUT_DIR": tmp_path / "absolute-output",
        "MEMORY_FILE": tmp_path / "absolute-state" / "memory.json",
        "STATE_FILE": tmp_path / "absolute-state" / "emotion.json",
        "TTS_PIPER_EXE": tmp_path / "tools" / "piper" / "piper",
        "TTS_PIPER_MODEL": tmp_path / "models" / "piper" / "voice.onnx",
        "TTS_PIPER_CONFIG": tmp_path / "models" / "piper" / "voice.onnx.json",
        "TTS_PIPER_ESPEAK_DATA": tmp_path / "tools" / "piper" / "espeak-ng-data",
        "SER_MODEL": tmp_path / "models" / "ser" / "SenseVoiceSmall",
    }
    _set_path_environment(monkeypatch, configured_paths)

    cfg = AppConfig.from_env()
    actual_paths = [
        cfg.output_dir,
        cfg.memory_file,
        cfg.state_file,
        cfg.tts_piper_exe,
        cfg.tts_piper_model,
        cfg.tts_piper_config,
        cfg.tts_piper_espeak_data,
        cfg.ser_model,
    ]

    assert actual_paths == list(configured_paths.values())
    assert all(path.is_absolute() for path in actual_paths)
    assert not any(path.exists() for path in actual_paths)


def test_model_names_and_urls_remain_strings(monkeypatch) -> None:
    _disable_dotenv(monkeypatch)
    monkeypatch.setenv("ASR_MODEL", "small")
    monkeypatch.setenv("LLM_MODEL", "qwen3:4b-instruct")
    monkeypatch.setenv("LLM_OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("LLM_OLLAMA_CHAT_URL", "http://localhost:11434/api/chat")

    cfg = AppConfig.from_env()

    assert cfg.asr_model == "small"
    assert cfg.llm_model == "qwen3:4b-instruct"
    assert cfg.llm_ollama_base_url == "http://localhost:11434"
    assert cfg.llm_ollama_chat_url == "http://localhost:11434/api/chat"
    assert isinstance(cfg.asr_model, str)
    assert isinstance(cfg.llm_model, str)
    assert isinstance(cfg.llm_ollama_base_url, str)
    assert isinstance(cfg.llm_ollama_chat_url, str)
