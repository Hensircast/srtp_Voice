"""Own contract tests for the optional ``validate --max-failures`` flag.

These assert the argv/fake contract only. They do not fabricate a real pytest
failure-limit result; the real control gate is Codex's independent check.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools import workbench_validate as validate


def _fake_runner(returncodes: list[int]):
    calls: list[dict] = []

    def runner(argv, **kwargs):
        calls.append({"argv": list(argv), "kwargs": kwargs})
        code = returncodes[min(len(calls) - 1, len(returncodes) - 1)]
        return subprocess.CompletedProcess(args=list(argv), returncode=code)

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def test_default_profiles_keep_the_original_argv() -> None:
    offline = validate.build_plan("offline")
    full = validate.build_plan("full")
    assert len(offline) == 2 and len(full) == 3
    assert offline[0][2:4] == ["compileall", "-q"]
    assert offline[1][2:] == ["pytest", "-q", "tests"]
    assert full[1][2:] == ["pytest", "-q", "tests"]
    assert full[2][2:] == ["pip", "check"]
    assert all("--maxfail" not in part for argv in full for part in argv)
    assert validate.build_plan("manual") == []


def test_only_pytest_grows_when_limited(tmp_path) -> None:
    (tmp_path / "tests").mkdir()
    target = tmp_path / "tests" / "test_sample.py"
    target.write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    offline = validate.build_plan("offline", root=tmp_path, max_failures=2)
    full = validate.build_plan("full", root=tmp_path, max_failures=2)
    targeted = validate.build_plan(
        "targeted", ["tests/test_sample.py"], root=tmp_path, max_failures=2
    )

    assert offline[0] == validate.build_plan(
        "offline", root=tmp_path
    )[0]  # compileall unchanged
    assert offline[1][2:] == ["pytest", "-q", "--maxfail=2", "tests"]
    assert full[1][2:] == ["pytest", "-q", "--maxfail=2", "tests"]
    assert full[2][2:] == ["pip", "check"]  # pip check unchanged
    assert targeted[0][2:] == ["pytest", "-q", "--maxfail=2", "tests/test_sample.py"]


@pytest.mark.parametrize("value", [0, -1, -5])
def test_non_positive_limits_are_rejected(value) -> None:
    with pytest.raises(validate.ValidationError):
        validate.build_plan("offline", max_failures=value)


@pytest.mark.parametrize("value", [2.5, "2", True, False, [2]])
def test_non_integer_limits_are_rejected(value) -> None:
    with pytest.raises(validate.ValidationError):
        validate.build_plan("offline", max_failures=value)
    with pytest.raises(validate.ValidationError):
        validate.run_validation("offline", max_failures=value, runner=_fake_runner([0]))


def test_manual_profile_rejects_a_limit_before_running() -> None:
    runner = _fake_runner([0])
    with pytest.raises(validate.ValidationError):
        validate.run_validation("manual", max_failures=3, runner=runner)
    assert runner.calls == []  # type: ignore[attr-defined]


def test_run_validation_forwards_the_limit_to_pytest_only() -> None:
    runner = _fake_runner([0, 0, 0])
    outcome = validate.run_validation("full", max_failures=4, runner=runner)

    assert outcome.status == "ok"
    calls = runner.calls  # type: ignore[attr-defined]
    assert len(calls) == 3
    assert "--maxfail=4" in calls[1]["argv"] and "--maxfail=4" not in calls[0]["argv"]
    assert "--maxfail=4" not in calls[2]["argv"]
    for call in calls:
        assert call["kwargs"]["shell"] is False
        assert Path(call["kwargs"]["cwd"]) == validate.PROJECT_ROOT


def test_exit_code_contract_is_unchanged() -> None:
    failed = validate.run_validation("full", runner=_fake_runner([0, 3, 0]))
    assert failed.status == "failed"
    assert validate.exit_code_for(failed) == 3

    limited = validate.run_validation("full", max_failures=1, runner=_fake_runner([0, 7, 0]))
    assert validate.exit_code_for(limited) == 7


def test_cli_accepts_default_and_rejects_invalid_values(monkeypatch, capsys) -> None:
    seen: list[dict] = []

    def fake_run_validation(profile, targets=(), **kwargs):
        seen.append({"profile": profile, "targets": list(targets), **kwargs})
        return validate.ValidationOutcome(profile=profile, status="ok", commands=[])

    monkeypatch.setattr(validate, "run_validation", fake_run_validation)

    assert validate._handle(
        type("A", (), {"profile": "offline", "tests": [], "max_failures": None})()
    ) == 0
    assert seen[-1]["max_failures"] is None

    # A legacy Namespace without the new attribute still works.
    assert validate._handle(type("A", (), {"profile": "offline", "tests": []})()) == 0
    assert seen[-1]["max_failures"] is None

    assert validate._handle(
        type("A", (), {"profile": "offline", "tests": [], "max_failures": 3})()
    ) == 0
    assert seen[-1]["max_failures"] == 3

    def raising(profile, targets=(), **kwargs):
        raise validate.ValidationError("synthetic invalid limit")

    monkeypatch.setattr(validate, "run_validation", raising)
    assert validate._handle(
        type("A", (), {"profile": "offline", "tests": [], "max_failures": 0})()
    ) == 2
    assert "validation error" in capsys.readouterr().out


def test_cli_registers_the_option_with_a_none_default() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    validate.register(subparsers)

    parse = getattr(validate, "register")
    assert callable(parse)
    defaults = parser.parse_args(["validate", "--profile", "offline"])
    assert getattr(defaults, "max_failures", None) is None
    limited = parser.parse_args(
        ["validate", "--profile", "offline", "--max-failures", "2"]
    )
    assert limited.max_failures == 2
