from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _run_commands(text: str) -> list[str]:
    return [
        line.strip().removeprefix("run: ")
        for line in text.splitlines()
        if line.strip().startswith("run: ")
    ]


def test_ci_workflow_has_expected_triggers_and_matrix() -> None:
    text = _workflow_text()

    assert "pull_request:" in text
    assert "push:" in text
    assert "workflow_dispatch:" in text
    assert "- main" in text
    assert "- v1.5-code-maintenance" in text
    assert "windows-latest" in text
    assert "ubuntu-latest" in text
    assert 'python-version: "3.11"' in text
    assert "fail-fast: false" in text
    assert "timeout-minutes: 15" in text
    assert "permissions:" in text
    assert "contents: read" in text


def test_ci_workflow_uses_supported_actions_and_environment() -> None:
    text = _workflow_text()

    assert "actions/checkout@v4" in text
    assert "actions/setup-python@v5" in text
    assert 'PIP_DISABLE_PIP_VERSION_CHECK: "1"' in text
    assert 'PIP_NO_CACHE_DIR: "1"' in text
    assert 'PYTHONUTF8: "1"' in text
    assert "cache:" not in text
    assert "upload-artifact" not in text
    assert "--cov" not in text


def test_ci_workflow_runs_only_offline_base_test_commands() -> None:
    text = _workflow_text()
    commands = _run_commands(text)

    assert commands == [
        "python -m pip install -r requirements.txt",
        "python -m compileall -q main.py srtp_voice tests",
        "python -m pytest -q",
        "python -m pip check",
    ]

    command_text = "\n".join(commands).lower()
    forbidden = (
        "requirements-asr.txt",
        "requirements-ser.txt",
        "torch",
        "torchaudio",
        "funasr",
        "modelscope",
        "faster-whisper",
        "ollama",
        "piper",
        "apt install",
        "apt-get",
        "curl ",
        "wget ",
        "arecord",
        "aplay",
        "ffplay",
        "serial",
        "main.py --mode",
        "main.py --diagnose",
    )
    assert all(token not in command_text for token in forbidden)
