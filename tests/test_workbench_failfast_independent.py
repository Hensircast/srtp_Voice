"""Independent validation-budget contracts, no model/device/network."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from tools import workbench, workbench_validate as validation


@pytest.mark.parametrize("profile", ["offline", "full", "targeted"])
def test_only_pytest_changes_when_limit_enabled(profile):
    targets = ["tests/test_workbench.py"] if profile == "targeted" else []
    original = validation.build_plan(profile, targets)
    limited = validation.build_plan(profile, targets, max_failures=2)
    assert len(original) == len(limited)
    for before, after in zip(original, limited):
        if before[1:3] == ["-m", "pytest"]:
            assert after.count("--maxfail=2") == 1
            assert [part for part in after if part != "--maxfail=2"] == before
        else:
            assert after == before
    assert validation.build_plan(profile, targets, max_failures=None) == original


@pytest.mark.parametrize("value", [True, False, 0, -1, 1.0, "1", float("nan"), float("inf")])
def test_bad_limit_never_calls_runner(value):
    called = []
    with pytest.raises(validation.ValidationError):
        validation.run_validation("full", max_failures=value, runner=lambda *a, **k: called.append(a))
    assert called == []


def test_manual_limit_rejected_without_execution():
    with pytest.raises(validation.ValidationError):
        validation.run_validation("manual", max_failures=1, runner=lambda *a, **k: pytest.fail("must not run"))


def test_failure_code_cwd_shell_and_stop_contract_remain_unchanged(tmp_path):
    calls = []
    def runner(argv, *, cwd, shell):
        calls.append((argv, cwd, shell))
        return subprocess.CompletedProcess(argv, 0 if len(calls) == 1 else 3)

    outcome = validation.run_validation("full", root=tmp_path, max_failures=1, runner=runner)
    assert len(calls) == 2
    assert all(cwd == str(tmp_path) and shell is False for _, cwd, shell in calls)
    assert "--maxfail=1" in calls[1][0]
    assert validation.exit_code_for(outcome) == 3


def test_cli_routes_limit_without_spawning_real_pytest(monkeypatch):
    calls = []
    def run(profile, targets=(), **kwargs):
        calls.append((profile, targets, kwargs))
        return validation.ValidationOutcome(profile, "ok")
    monkeypatch.setattr(validation, "run_validation", run)
    assert workbench.main(["validate", "--profile", "targeted", "--max-failures", "2", "tests/test_workbench.py"]) == 0
    assert calls == [("targeted", ["tests/test_workbench.py"], {"max_failures": 2})]


def test_real_pytest_stops_after_one_failure_and_default_runs_both(tmp_path):
    """Actual pytest on two intentional failing probes, not product test weakening."""
    probe = tmp_path / "test_failfast_probe.py"
    probe.write_text("def test_first():\n    assert False\n\ndef test_second():\n    assert False\n", encoding="utf-8")
    argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=", str(probe)]
    child_environment = dict(os.environ)
    child_environment.pop("PYTEST_ADDOPTS", None)
    limited = subprocess.run(argv + ["--maxfail=1"], cwd=tmp_path, shell=False, capture_output=True, text=True,
                             timeout=30, env=child_environment)
    unlimited = subprocess.run(argv, cwd=tmp_path, shell=False, capture_output=True, text=True,
                               timeout=30, env=child_environment)
    assert limited.returncode == unlimited.returncode == 1
    assert "1 failed" in limited.stdout
    assert "2 failed" in unlimited.stdout
