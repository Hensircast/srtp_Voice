from __future__ import annotations

import json
import stat
import subprocess
import types
from pathlib import Path

import pytest

import srtp_voice.config as config_module
import srtp_voice.tts as tts_module
from srtp_voice.config import AppConfig, default_piper_executable
from srtp_voice.streaming import AudioChunk, TextChunk
from srtp_voice.tts import TTSAdapter


def _piper_files(tmp_path):
    exe = tmp_path / "tools" / "piper" / "piper.exe"
    model = tmp_path / "models" / "piper" / "zh_CN-huayan-medium" / "model.onnx"
    exe.parent.mkdir(parents=True, exist_ok=True)
    model.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"exe")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    model.write_bytes(b"model")
    return exe, model


def _cfg(tmp_path, **overrides):
    exe, model = _piper_files(tmp_path)
    values = {
        "tts_backend": "piper",
        "tts_piper_exe": exe,
        "tts_piper_model": model,
        "tts_piper_timeout_seconds": 12,
    }
    values.update(overrides)
    return AppConfig(**values)


def _fake_run(monkeypatch, out_wav, returncode=0, stderr_bytes=b"", write_output=True, output_bytes=b"wav"):
    captured = {}

    def fake_run(cmd, input, stdout, stderr, timeout):
        captured["cmd"] = cmd
        captured["input"] = input
        captured["stdout"] = stdout
        captured["stderr"] = stderr
        captured["timeout"] = timeout
        if write_output:
            Path(out_wav).parent.mkdir(parents=True, exist_ok=True)
            Path(out_wav).write_bytes(output_bytes)
        return types.SimpleNamespace(returncode=returncode, stderr=stderr_bytes)

    monkeypatch.setattr(tts_module.subprocess, "run", fake_run)
    return captured


def test_mock_tts_still_generates_wav(tmp_path) -> None:
    out_wav = tmp_path / "reply.wav"
    TTSAdapter(AppConfig(tts_backend="mock")).synthesize("hello", out_wav)
    assert out_wav.exists()
    assert out_wav.stat().st_size > 0


def test_piper_command_and_utf8_stdin(monkeypatch, tmp_path) -> None:
    out_wav = tmp_path / "reply.wav"
    cfg = _cfg(tmp_path)
    captured = _fake_run(monkeypatch, out_wav)

    TTSAdapter(cfg).synthesize("  你好  ", out_wav)

    cmd = captured["cmd"]
    assert cmd[:5] == [str(cfg.tts_piper_exe), "--model", str(cfg.tts_piper_model), "--output_file", str(out_wav)]
    assert "--input-file" not in cmd
    assert captured["input"] == "你好\n".encode("utf-8")
    assert captured["timeout"] == 12


@pytest.mark.parametrize(
    ("system_name", "expected"),
    [
        ("Windows", Path("tools/piper/piper.exe")),
        ("Linux", Path("tools/piper/piper")),
        ("Darwin", Path("tools/piper/piper")),
    ],
)
def test_piper_platform_default_path(monkeypatch, system_name, expected) -> None:
    assert default_piper_executable(system_name) == expected

    monkeypatch.setattr(config_module.platform, "system", lambda: system_name)
    monkeypatch.setattr(config_module, "load_dotenv", None)
    monkeypatch.delenv("TTS_PIPER_EXE", raising=False)

    assert AppConfig().tts_piper_exe == expected
    assert AppConfig.from_env().tts_piper_exe == expected


@pytest.mark.parametrize("system_name", ["Windows", "Linux"])
def test_piper_explicit_executable_override_wins(monkeypatch, system_name) -> None:
    monkeypatch.setattr(config_module.platform, "system", lambda: system_name)
    monkeypatch.setattr(config_module, "load_dotenv", None)
    monkeypatch.setenv("TTS_PIPER_EXE", "custom/runtime/piper-custom")

    assert AppConfig.from_env().tts_piper_exe == Path("custom/runtime/piper-custom")


def test_piper_non_windows_requires_execute_permission(monkeypatch, tmp_path) -> None:
    out_wav = tmp_path / "reply.wav"
    cfg = _cfg(tmp_path)
    access_calls = []

    monkeypatch.setattr(tts_module, "is_windows_platform", lambda: False)

    def fake_access(path, mode):
        access_calls.append((path, mode))
        return False

    monkeypatch.setattr(tts_module.os, "access", fake_access)
    monkeypatch.setattr(
        tts_module.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("Piper must not run without execute permission"),
    )

    with pytest.raises(RuntimeError) as exc_info:
        TTSAdapter(cfg).synthesize("hello", out_wav)

    message = str(exc_info.value)
    assert str(cfg.tts_piper_exe) in message
    assert f"chmod +x {cfg.tts_piper_exe}" in message
    assert access_calls == [(cfg.tts_piper_exe, tts_module.os.X_OK)]


def test_piper_windows_skips_posix_execute_check(monkeypatch, tmp_path) -> None:
    out_wav = tmp_path / "reply.wav"
    cfg = _cfg(tmp_path)
    captured = _fake_run(monkeypatch, out_wav)

    monkeypatch.setattr(tts_module, "is_windows_platform", lambda: True)
    monkeypatch.setattr(
        tts_module.os,
        "access",
        lambda *args, **kwargs: pytest.fail("Windows must not perform a POSIX execute check"),
    )

    TTSAdapter(cfg).synthesize("hello", out_wav)

    assert captured["cmd"][0] == str(cfg.tts_piper_exe)


def test_piper_removes_stale_output_before_run(monkeypatch, tmp_path) -> None:
    out_wav = tmp_path / "reply.wav"
    out_wav.write_bytes(b"stale")
    cfg = _cfg(tmp_path)
    captured = {}

    def fake_run(cmd, input, stdout, stderr, timeout):
        captured["stale_exists_during_run"] = out_wav.exists()
        out_wav.write_bytes(b"fresh")
        return types.SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(tts_module.subprocess, "run", fake_run)

    TTSAdapter(cfg).synthesize("hello", out_wav)

    assert captured["stale_exists_during_run"] is False
    assert out_wav.read_bytes() == b"fresh"


def test_piper_json_input(monkeypatch, tmp_path) -> None:
    out_wav = tmp_path / "reply.wav"
    cfg = _cfg(tmp_path, tts_piper_use_json_input=True)
    captured = _fake_run(monkeypatch, out_wav)

    TTSAdapter(cfg).synthesize("你好", out_wav)

    assert "--json-input" in captured["cmd"]
    assert captured["input"] == (json.dumps({"text": "你好"}, ensure_ascii=False) + "\n").encode("utf-8")


def test_piper_explicit_config(monkeypatch, tmp_path) -> None:
    out_wav = tmp_path / "reply.wav"
    config = tmp_path / "explicit.onnx.json"
    cfg = _cfg(tmp_path, tts_piper_config=config)
    captured = _fake_run(monkeypatch, out_wav)

    TTSAdapter(cfg).synthesize("hello", out_wav)

    assert "--config" in captured["cmd"]
    assert str(config) in captured["cmd"]


def test_piper_auto_config(monkeypatch, tmp_path) -> None:
    out_wav = tmp_path / "reply.wav"
    cfg = _cfg(tmp_path)
    auto_config = Path(str(cfg.tts_piper_model) + ".json")
    auto_config.write_bytes(b"{}")
    captured = _fake_run(monkeypatch, out_wav)

    TTSAdapter(cfg).synthesize("hello", out_wav)

    assert "--config" in captured["cmd"]
    assert str(auto_config) in captured["cmd"]


def test_piper_no_missing_auto_config(monkeypatch, tmp_path) -> None:
    out_wav = tmp_path / "reply.wav"
    cfg = _cfg(tmp_path)
    captured = _fake_run(monkeypatch, out_wav)

    TTSAdapter(cfg).synthesize("hello", out_wav)

    assert "--config" not in captured["cmd"]


def test_piper_espeak_and_extra_args(monkeypatch, tmp_path) -> None:
    out_wav = tmp_path / "reply.wav"
    espeak = tmp_path / "tools" / "piper" / "espeak-ng-data"
    cfg = _cfg(
        tmp_path,
        tts_piper_espeak_data=espeak,
        tts_piper_extra_args='--length_scale 1.1 --speaker "0"',
    )
    captured = _fake_run(monkeypatch, out_wav)

    TTSAdapter(cfg).synthesize("hello", out_wav)

    cmd = captured["cmd"]
    assert cmd[cmd.index("--espeak_data") + 1] == str(espeak)
    assert "--length_scale" in cmd
    assert "1.1" in cmd
    assert "--speaker" in cmd
    assert "0" in cmd


def test_piper_missing_exe_and_model(tmp_path) -> None:
    exe, model = _piper_files(tmp_path)
    exe.unlink()
    try:
        TTSAdapter(AppConfig(tts_backend="piper", tts_piper_exe=exe, tts_piper_model=model)).synthesize("hi", tmp_path / "a.wav")
    except FileNotFoundError as exc:
        assert str(exe) in str(exc)
    else:
        raise AssertionError("missing exe should raise")

    exe, model = _piper_files(tmp_path)
    model.unlink()
    try:
        TTSAdapter(AppConfig(tts_backend="piper", tts_piper_exe=exe, tts_piper_model=model)).synthesize("hi", tmp_path / "a.wav")
    except FileNotFoundError as exc:
        assert str(model) in str(exc)
    else:
        raise AssertionError("missing model should raise")


def test_piper_nonzero_timeout_and_bad_output(monkeypatch, tmp_path) -> None:
    out_wav = tmp_path / "reply.wav"
    cfg = _cfg(tmp_path)
    _fake_run(monkeypatch, out_wav, returncode=2, stderr_bytes=b"bad stderr", write_output=False)
    try:
        TTSAdapter(cfg).synthesize("hello", out_wav)
    except RuntimeError as exc:
        message = str(exc)
        assert "piper" in message
        assert str(cfg.tts_piper_exe) in message
        assert str(cfg.tts_piper_model) in message
        assert "returncode=2" in message
        assert "bad stderr" in message
    else:
        raise AssertionError("nonzero piper should raise")

    def timeout_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=1)

    monkeypatch.setattr(tts_module.subprocess, "run", timeout_run)
    try:
        TTSAdapter(cfg).synthesize("hello", out_wav)
    except RuntimeError as exc:
        assert "timed out" in str(exc)
    else:
        raise AssertionError("timeout should raise")

    out_wav.write_bytes(b"stale")
    _fake_run(monkeypatch, out_wav, write_output=False)
    try:
        TTSAdapter(cfg).synthesize("hello", out_wav)
    except RuntimeError as exc:
        assert "non-empty WAV" in str(exc)
    else:
        raise AssertionError("missing output should raise")

    _fake_run(monkeypatch, out_wav, write_output=True, output_bytes=b"")
    try:
        TTSAdapter(cfg).synthesize("hello", out_wav)
    except RuntimeError as exc:
        assert "non-empty WAV" in str(exc)
    else:
        raise AssertionError("empty output should raise")


def test_piper_forbids_output_control_extra_args(tmp_path) -> None:
    forbidden_args = [
        "--output_file other.wav",
        "--output-file other.wav",
        "-f other.wav",
        "--output_dir another",
        "--output-dir another",
        "-d another",
        "--output_raw",
        "--output-raw",
    ]
    for extra_args in forbidden_args:
        cfg = _cfg(tmp_path, tts_piper_extra_args=extra_args)
        try:
            TTSAdapter(cfg).synthesize("hello", tmp_path / "reply.wav")
        except ValueError as exc:
            message = str(exc)
            assert "TTS_PIPER_EXTRA_ARGS" in message
            assert "output path is managed by TTSAdapter" in message
        else:
            raise AssertionError(f"forbidden extra args should raise: {extra_args}")


def test_piper_allows_safe_extra_args(monkeypatch, tmp_path) -> None:
    out_wav = tmp_path / "reply.wav"
    cfg = _cfg(tmp_path, tts_piper_extra_args="--length_scale 1.1 --noise_scale 0.6 --noise_w 0.8 --sentence_silence 0.2")
    captured = _fake_run(monkeypatch, out_wav)

    TTSAdapter(cfg).synthesize("hello", out_wav)

    cmd = captured["cmd"]
    for item in ["--length_scale", "1.1", "--noise_scale", "0.6", "--noise_w", "0.8", "--sentence_silence", "0.2"]:
        assert item in cmd


def test_piper_empty_text_and_default_backend(tmp_path) -> None:
    assert AppConfig().tts_backend == "mock"
    cfg = _cfg(tmp_path)
    try:
        TTSAdapter(cfg).synthesize("   ", tmp_path / "reply.wav")
    except ValueError as exc:
        assert "must not be empty" in str(exc)
    else:
        raise AssertionError("empty text should raise")


def test_piper_env_config_parsing(monkeypatch) -> None:
    monkeypatch.setattr(config_module, "load_dotenv", None)
    monkeypatch.setattr(config_module.platform, "system", lambda: "Windows")
    for name in [
        "TTS_BACKEND",
        "TTS_PIPER_EXE",
        "TTS_PIPER_MODEL",
        "TTS_PIPER_CONFIG",
        "TTS_PIPER_TIMEOUT_SECONDS",
        "TTS_PIPER_EXTRA_ARGS",
        "TTS_PIPER_ESPEAK_DATA",
        "TTS_PIPER_USE_JSON_INPUT",
    ]:
        monkeypatch.delenv(name, raising=False)

    cfg = AppConfig.from_env()
    assert cfg.tts_backend == "mock"
    assert cfg.tts_piper_exe == Path("tools/piper/piper.exe")
    assert cfg.tts_piper_model == Path("models/piper/zh_CN-huayan-medium/model.onnx")
    assert cfg.tts_piper_config is None

    monkeypatch.setenv("TTS_BACKEND", "piper")
    monkeypatch.setenv("TTS_PIPER_EXE", "custom/piper.exe")
    monkeypatch.setenv("TTS_PIPER_MODEL", "custom/model.onnx")
    monkeypatch.setenv("TTS_PIPER_CONFIG", "custom/model.onnx.json")
    monkeypatch.setenv("TTS_PIPER_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("TTS_PIPER_EXTRA_ARGS", "--speaker 0")
    monkeypatch.setenv("TTS_PIPER_ESPEAK_DATA", "custom/espeak")
    monkeypatch.setenv("TTS_PIPER_USE_JSON_INPUT", "1")
    cfg = AppConfig.from_env()

    assert cfg.tts_backend == "piper"
    assert cfg.tts_piper_exe == Path("custom/piper.exe")
    assert cfg.tts_piper_model == Path("custom/model.onnx")
    assert cfg.tts_piper_config == Path("custom/model.onnx.json")
    assert cfg.tts_piper_timeout_seconds == 1
    assert cfg.tts_piper_extra_args == "--speaker 0"
    assert cfg.tts_piper_espeak_data == Path("custom/espeak")
    assert cfg.tts_piper_use_json_input is True


def test_streaming_interfaces_and_tts_streaming_placeholder() -> None:
    audio = AudioChunk(pcm16=b"abc", sample_rate=16000)
    text = TextChunk(text="hello")
    assert audio.channels == 1
    assert text.is_final is False

    for backend in ("mock", "edge_tts", "piper"):
        adapter = TTSAdapter(AppConfig(tts_backend=backend))
        assert adapter.supports_streaming() is False
        with pytest.raises(
            NotImplementedError,
            match="does not support streaming TTS",
        ):
            adapter.synthesize_stream("hello")


def test_env_example_and_gitignore_for_piper() -> None:
    env_bytes = Path(".env.example").read_bytes()
    assert not env_bytes.startswith(b"\xef\xbb\xbf")
    env_text = env_bytes.decode("utf-8")
    env_lines = [line.strip() for line in env_text.splitlines()]
    assert "TTS_BACKEND=mock" in env_text
    assert "# TTS_BACKEND=piper" in env_text
    assert "# TTS_PIPER_EXE=tools/piper/piper.exe" in env_lines
    assert "# TTS_PIPER_EXE=tools/piper/piper" in env_lines
    assert "TTS_PIPER_MODEL=models/piper/zh_CN-huayan-medium/your-model.onnx" in env_text
    active_lines = [
        line for line in env_lines if line and not line.startswith("#")
    ]
    assert not any(line.startswith("TTS_PIPER_EXE=") for line in active_lines)
    assert not any(line.startswith("TTS_PIPER_CONFIG=") for line in active_lines)
    assert "# TTS_PIPER_CONFIG=models/piper/zh_CN-huayan-medium/your-model.onnx.json" in env_text

    gitignore = Path(".gitignore").read_text(encoding="utf-8")
    assert "tools/piper/" in gitignore
