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

    assert "actions/checkout@v7" in text
    assert "actions/setup-python@v7" in text
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
        "python -m tools.workbench validate --profile full",
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


def test_ci_workflow_delegates_to_the_shared_validation_entry_point() -> None:
    from tools import workbench_validate

    commands = _run_commands(_workflow_text())

    assert commands[0] == "python -m pip install -r requirements.txt"

    entry = commands[1]
    assert entry == "python -m tools.workbench validate --profile full"
    prefix, _, profile = entry.partition("--profile ")
    assert prefix.endswith("validate ")
    assert profile.strip() == "full"

    plan = workbench_validate.build_plan(profile.strip())
    assert [argv[1:3] for argv in plan] == [
        ["-m", "compileall"],
        ["-m", "pytest"],
        ["-m", "pip"],
    ]
    assert plan[-1][2:4] == ["pip", "check"]
    assert all("install" not in " ".join(argv) for argv in plan)
