from __future__ import annotations

import argparse
import sys
import types
from pathlib import Path

import pytest

import srtp_voice.diagnostics as diagnostics
from srtp_voice.config import AppConfig


def _fake_soundfile():
    return types.SimpleNamespace(__version__="0.test")


def _patch_audio_imports(monkeypatch, *, sounddevice=None, sounddevice_error=None):
    def fake_import(name: str):
        if name == "soundfile":
            return _fake_soundfile()
        if name == "sounddevice":
            if sounddevice_error is not None:
                raise sounddevice_error
            return sounddevice
        raise AssertionError(f"unexpected import: {name}")

    monkeypatch.setattr(diagnostics.importlib, "import_module", fake_import)


def _fake_sounddevice(default_devices=(1, 2), query_error=None):
    devices = {
        1: {
            "name": "Fake Microphone",
            "max_input_channels": 2,
            "default_samplerate": 48000.0,
        },
        2: {
            "name": "Fake Speakers",
            "max_output_channels": 2,
            "default_samplerate": 44100.0,
        },
    }

    def query_devices(index, kind=None):
        if query_error is not None:
            raise query_error
        return devices[index]

    return types.SimpleNamespace(
        __version__="0.5.test",
        default=types.SimpleNamespace(device=default_devices),
        get_portaudio_version=lambda: (1246720, "PortAudio V19.7.0-test"),
        query_devices=query_devices,
    )


def test_parse_args_diagnose_defaults_false(monkeypatch) -> None:
    import main

    monkeypatch.setattr(sys, "argv", ["main.py"])
    assert main.parse_args().diagnose is False

    monkeypatch.setattr(sys, "argv", ["main.py", "--diagnose"])
    assert main.parse_args().diagnose is True


def test_diagnose_short_circuits_workflow_and_does_not_create_outputs(
    monkeypatch,
    tmp_path,
) -> None:
    import main as main_module

    output_dir = tmp_path / "outputs"
    cfg = AppConfig(output_dir=output_dir)
    args = argparse.Namespace(diagnose=True)
    events = []

    monkeypatch.setattr(main_module, "parse_args", lambda: args)
    monkeypatch.setattr(
        main_module.AppConfig,
        "from_env",
        classmethod(lambda cls: cfg),
    )
    monkeypatch.setattr(
        main_module,
        "collect_diagnostics",
        lambda received_cfg: events.append(("collect", received_cfg)) or {"ok": True},
    )
    monkeypatch.setattr(
        main_module,
        "print_diagnostics",
        lambda data: events.append(("print", data)),
    )

    def fail_workflow(*args, **kwargs):
        raise AssertionError("diagnostics must not initialize the voice workflow")

    for name in [
        "ensure_dir",
        "DialogueStateMachine",
        "SpeechEmotionRecognizer",
        "ASRAdapter",
        "StrategyGenerator",
        "TTSAdapter",
        "EmotionStateSmoother",
        "JsonMemory",
    ]:
        monkeypatch.setattr(main_module, name, fail_workflow)

    main_module.main()

    assert events == [("collect", cfg), ("print", {"ok": True})]
    assert not output_dir.exists()


def test_collects_fake_default_audio_devices(monkeypatch) -> None:
    _patch_audio_imports(monkeypatch, sounddevice=_fake_sounddevice())
    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: None)

    data = diagnostics.collect_diagnostics(AppConfig())
    audio = data["audio"]

    assert audio["sounddevice"]["status"] == "ok"
    assert audio["sounddevice"]["version"] == "0.5.test"
    assert audio["soundfile"]["status"] == "ok"
    assert audio["portaudio"]["text"] == "PortAudio V19.7.0-test"
    assert audio["default_input"] == {
        "status": "ok",
        "index": 1,
        "name": "Fake Microphone",
        "channels": 2,
        "sample_rate": 48000.0,
        "error": None,
    }
    assert audio["default_output"] == {
        "status": "ok",
        "index": 2,
        "name": "Fake Speakers",
        "channels": 2,
        "sample_rate": 44100.0,
        "error": None,
    }


def test_collects_warning_when_no_default_audio_devices(monkeypatch) -> None:
    _patch_audio_imports(
        monkeypatch,
        sounddevice=_fake_sounddevice(default_devices=(-1, -1)),
    )
    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: None)

    audio = diagnostics.collect_diagnostics(AppConfig())["audio"]

    assert audio["default_input"]["status"] == "warning"
    assert audio["default_output"]["status"] == "warning"
    assert "No default input audio device" in audio["default_input"]["error"]
    assert "No default output audio device" in audio["default_output"]["error"]


def test_collects_warning_when_sounddevice_import_fails(monkeypatch, capsys) -> None:
    _patch_audio_imports(
        monkeypatch,
        sounddevice_error=ModuleNotFoundError("sounddevice is unavailable"),
    )
    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: None)

    data = diagnostics.collect_diagnostics(AppConfig())
    diagnostics.print_diagnostics(data)

    error = data["audio"]["sounddevice"]["error"]
    assert "ModuleNotFoundError" in error
    assert "sounddevice is unavailable" in error
    output = capsys.readouterr().out
    assert "[WARNING] sounddevice" in output
    assert "Traceback" not in output


def test_portaudio_device_query_failure_keeps_error_context(monkeypatch) -> None:
    class FakePortAudioError(RuntimeError):
        pass

    _patch_audio_imports(
        monkeypatch,
        sounddevice=_fake_sounddevice(
            query_error=FakePortAudioError("device query failed"),
        ),
    )
    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: None)

    audio = diagnostics.collect_diagnostics(AppConfig())["audio"]

    assert audio["default_input"]["status"] == "warning"
    assert "FakePortAudioError: device query failed" in audio["default_input"]["error"]
    assert audio["default_output"]["status"] == "warning"
    assert "FakePortAudioError: device query failed" in audio["default_output"]["error"]


@pytest.mark.parametrize(
    ("create_paths", "ffmpeg_path", "expected_exists", "ffmpeg_status"),
    [
        (True, "/fake/bin/ffmpeg", True, "ok"),
        (False, None, False, "warning"),
    ],
)
def test_reports_piper_paths_and_ffmpeg(
    monkeypatch,
    tmp_path,
    create_paths,
    ffmpeg_path,
    expected_exists,
    ffmpeg_status,
) -> None:
    piper_exe = tmp_path / "tools" / "piper" / "piper"
    piper_model = tmp_path / "models" / "piper" / "model.onnx"
    if create_paths:
        piper_exe.parent.mkdir(parents=True)
        piper_model.parent.mkdir(parents=True)
        piper_exe.write_bytes(b"fake executable")
        piper_model.write_bytes(b"fake model")

    _patch_audio_imports(
        monkeypatch,
        sounddevice=_fake_sounddevice(default_devices=(-1, -1)),
    )
    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: ffmpeg_path)
    cfg = AppConfig(
        tts_piper_exe=piper_exe,
        tts_piper_model=piper_model,
    )

    data = diagnostics.collect_diagnostics(cfg)

    assert data["paths"]["piper_executable"] == {
        "path": str(piper_exe),
        "exists": expected_exists,
    }
    assert data["paths"]["piper_model"] == {
        "path": str(piper_model),
        "exists": expected_exists,
    }
    assert data["ffmpeg"] == {
        "status": ffmpeg_status,
        "path": ffmpeg_path,
    }


def test_diagnostic_output_does_not_include_sensitive_environment(
    monkeypatch,
    capsys,
) -> None:
    secret = "do-not-print-this-secret"
    monkeypatch.setenv("LLM_API_KEY", secret)
    monkeypatch.setenv("ACCESS_TOKEN", secret)
    _patch_audio_imports(
        monkeypatch,
        sounddevice=_fake_sounddevice(default_devices=(-1, -1)),
    )
    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: None)

    diagnostics.print_diagnostics(diagnostics.collect_diagnostics(AppConfig()))

    output = capsys.readouterr().out
    assert secret not in output
    assert "LLM_API_KEY" not in output
    assert "ACCESS_TOKEN" not in output
