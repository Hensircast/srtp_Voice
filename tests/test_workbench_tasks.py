"""Offline tests for the resumable workbench task runner.

Scratch directories live under ``outputs/workbench/pytest-local/`` so these
tests do not depend on the system temporary root being writable.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from tools import workbench, workbench_tasks

PROJECT_ROOT = workbench.PROJECT_ROOT


def _workdir(request, name: str = "root") -> Path:
    base = PROJECT_ROOT / "outputs" / "workbench" / "pytest-local" / uuid.uuid4().hex
    root = base / name
    root.mkdir(parents=True, exist_ok=True)
    request.addfinalizer(lambda: shutil.rmtree(base, ignore_errors=True))
    return root


def _state(root: Path, task_id: str) -> dict:
    path = root / "outputs" / "workbench" / "tasks" / task_id / "state.json"
    return json.loads(path.read_text(encoding="utf-8"))


class _FakeProcess:
    def __init__(self, pid: int, code: int) -> None:
        self.pid = pid
        self._code = code

    def poll(self):
        return self._code

    def wait(self, timeout=None) -> int:
        return self._code


class _FakeSpawn:
    def __init__(self, codes: list[int], pid: int = 4242) -> None:
        self.codes = list(codes)
        self.pid = pid
        self.calls: list[dict] = []

    def __call__(self, argv, cwd=None, shell=None, **kwargs):
        index = len(self.calls)
        self.calls.append({"argv": list(argv), "cwd": cwd, "shell": shell})
        code = self.codes[index] if index < len(self.codes) else 0
        return _FakeProcess(self.pid, code)


class _BlockingProcess:
    """Popen stand-in whose terminate() does not end the process."""

    def __init__(self, pid: int = 7777) -> None:
        self.pid = pid
        self.terminated = False

    def poll(self):
        return None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout=None) -> int:
        if timeout is not None:
            raise subprocess.TimeoutExpired("fake", timeout)
        return 0


class _InterruptingProcess:
    """Popen stand-in that raises KeyboardInterrupt while the parent waits."""

    def __init__(self, pid: int = 8888) -> None:
        self.pid = pid
        self.terminated = False
        self.poll_calls = 0

    def poll(self):
        self.poll_calls += 1
        return None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout=None) -> int:
        raise KeyboardInterrupt


# --------------------------------------------------------------------------- #
# command and id validation
# --------------------------------------------------------------------------- #
def test_prepare_task_command_accepts_only_workbench_argv() -> None:
    execution, displayed = workbench_tasks.prepare_task_command(
        ["python", "-m", "tools.workbench", "validate", "--profile", "offline"]
    )

    assert execution[0] == sys.executable
    assert displayed[0] == "python"
    assert sys.executable not in " ".join(displayed)
    assert execution[1:4] == ["-m", "tools.workbench", "validate"]

    for bad in (
        [],
        ["python", "-c", "print(1)"],
        ["python", "-m", "main"],
        ["python", "-m", "tools.workbench", "task", "run", "--id", "x"],
        ["python", "-m", "tools.workbench", "shell"],
        ["python", "-m", "pip", "install", "requests"],
        ["powershell", "-m", "tools.workbench", "doctor"],
    ):
        with pytest.raises(workbench_tasks.TaskError):
            workbench_tasks.prepare_task_command(bad)


def test_prepare_task_command_rejects_interpreter_options_and_unknown_flags() -> None:
    for bad in (
        ["python", "-cprint(1)", "-m", "tools.workbench", "doctor"],
        ["python", "-W", "ignore", "-m", "tools.workbench", "doctor"],
        ["python", "-X", "dev", "-m", "tools.workbench", "doctor"],
        ["python", "--", "-m", "tools.workbench", "doctor"],
        ["python", "-m", "tools.workbench", "doctor", "--token", "abc"],
        ["python", "-m", "tools.workbench", "doctor", "--api-key=abc"],
        ["python", "-m", "tools.workbench", "validate"],
        ["python", "-m", "tools.workbench", "snapshot", "--output", "outputs/x.json", "--token", "t"],
    ):
        with pytest.raises(workbench_tasks.TaskError):
            workbench_tasks.prepare_task_command(bad)


def test_rejected_arguments_never_reach_state_or_manifest(request) -> None:
    root = _workdir(request, "reject")

    with pytest.raises(workbench_tasks.TaskError):
        workbench_tasks.run_task(
            "reject-1",
            ["python", "-m", "tools.workbench", "doctor", "--token", "super-secret-value"],
            root=root,
            popen=_FakeSpawn([0]),
        )

    layout = workbench_tasks.task_layout("reject-1", root=root)
    assert not layout.manifest.exists()
    assert not layout.state.exists()


def test_task_id_must_be_plain_basename() -> None:
    for bad in ("", "..", "../escape", "a/b", "a\\b", "a b", "x" * 65):
        with pytest.raises(workbench_tasks.TaskError):
            workbench_tasks.validate_task_id(bad)
    assert workbench_tasks.validate_task_id("task-01.local") == "task-01.local"


def test_task_commands_reject_paths_outside_project(request) -> None:
    root = _workdir(request, "proj")
    with pytest.raises(workbench_tasks.TaskError):
        workbench_tasks.prepare_task_command(
            ["python", "-m", "tools.workbench", "baseline", "--metrics", "../outside.json"],
            root=root,
        )


# --------------------------------------------------------------------------- #
# run / duplicate / concurrency
# --------------------------------------------------------------------------- #
def test_run_duplicate_id_is_refused_without_overwriting_history(request) -> None:
    root = _workdir(request, "dup")
    spawn = _FakeSpawn([0])
    first = workbench_tasks.run_task(
        "dup-1", ["python", "-m", "tools.workbench", "doctor"], root=root, popen=spawn
    )
    assert first.status == "completed"
    assert first.exit_code == 0
    assert first.manifest["command"][0] == "python"

    with pytest.raises(workbench_tasks.TaskError):
        workbench_tasks.run_task(
            "dup-1", ["python", "-m", "tools.workbench", "doctor"], root=root, popen=spawn
        )
    assert _state(root, "dup-1")["attempt"] == 1


def test_run_concurrent_lock_is_rejected(request) -> None:
    root = _workdir(request, "busy")
    layout = workbench_tasks.task_layout("busy-1", root=root)
    layout.task_dir.mkdir(parents=True)
    layout.lock.write_text(json.dumps({"attempt": 1}), encoding="utf-8")

    with pytest.raises(workbench_tasks.TaskError):
        workbench_tasks.run_task(
            "busy-1",
            ["python", "-m", "tools.workbench", "doctor"],
            root=root,
            popen=_FakeSpawn([0]),
        )


def test_run_records_failure_exit_code_and_log(request) -> None:
    root = _workdir(request, "fail")
    spawn = _FakeSpawn([3])
    result = workbench_tasks.run_task(
        "fail-1",
        ["python", "-m", "tools.workbench", "validate", "--profile", "offline"],
        root=root,
        popen=spawn,
    )

    assert result.status == "failed"
    assert result.exit_code == 3
    assert spawn.calls[0]["shell"] is False
    assert Path(spawn.calls[0]["cwd"]) == root
    assert result.log_path.read_text(encoding="utf-8").startswith("$ python -m")


def test_run_process_start_failure_is_reported(request) -> None:
    root = _workdir(request, "startfail")

    def failing_spawn(*args, **kwargs):
        raise OSError("cannot start")

    result = workbench_tasks.run_task(
        "start-fail",
        ["python", "-m", "tools.workbench", "doctor"],
        root=root,
        popen=failing_spawn,
    )

    assert result.status == "failed"
    assert result.exit_code is None
    assert "OSError" in result.log_path.read_text(encoding="utf-8")


def test_run_interrupt_only_touches_own_child(request) -> None:
    """Interrupt handling with no child is interrupted; with a child it only
    terminates the Popen object this attempt created and records unknown."""

    root = _workdir(request, "interrupt")

    def interrupt_before_spawn(argv, cwd=None, shell=None, **kwargs):
        raise KeyboardInterrupt

    no_child = workbench_tasks.run_task(
        "interrupt-1",
        ["python", "-m", "tools.workbench", "doctor"],
        root=root,
        popen=interrupt_before_spawn,
    )
    assert no_child.status == "interrupted"
    assert no_child.exit_code is None

    blocking = _InterruptingProcess()

    def spawn_blocking(argv, cwd=None, shell=None, **kwargs):
        return blocking

    with_child = workbench_tasks.run_task(
        "interrupt-2",
        ["python", "-m", "tools.workbench", "doctor"],
        root=root,
        popen=spawn_blocking,
    )

    # The blocking Popen was terminated (proving _stop_own_child was used on
    # the object we created) and the outcome stays unknown because the whole
    # process tree could not be confirmed finished.
    assert blocking.terminated is True
    assert with_child.status == "unknown"
    assert with_child.exit_code is None
    assert not workbench_tasks.task_layout("interrupt-2", root=root).lock.exists()

# --------------------------------------------------------------------------- #
# status / resume
# --------------------------------------------------------------------------- #
def test_resume_refuses_success_unknown_and_running_then_succeeds(request, monkeypatch) -> None:
    root = _workdir(request, "resume")
    spawn = _FakeSpawn([1, 0])
    workbench_tasks.run_task(
        "res-1", ["python", "-m", "tools.workbench", "doctor"], root=root, popen=spawn
    )
    assert _state(root, "res-1")["status"] == "failed"

    monkeypatch.setattr(workbench_tasks, "process_status", lambda pid: "running")
    with pytest.raises(workbench_tasks.TaskError):
        workbench_tasks.resume_task("res-1", root=root, popen=spawn)

    monkeypatch.setattr(workbench_tasks, "process_status", lambda pid: "unknown")
    with pytest.raises(workbench_tasks.TaskError):
        workbench_tasks.resume_task("res-1", root=root, popen=spawn)

    monkeypatch.setattr(workbench_tasks, "process_status", lambda pid: "dead")
    resumed = workbench_tasks.resume_task("res-1", root=root, popen=spawn)
    assert resumed.attempt == 2
    assert resumed.status == "completed"

    with pytest.raises(workbench_tasks.TaskError):
        workbench_tasks.resume_task("res-1", root=root, popen=spawn)


def test_resume_recovers_recorded_spawn_failure(request, monkeypatch) -> None:
    """A spawn that never created a child is not blocked by unknown liveness."""

    root = _workdir(request, "spawnfail-resume")
    monkeypatch.setattr(workbench_tasks, "process_status", lambda pid: "unknown")

    def failing_spawn(*args, **kwargs):
        raise OSError("cannot start")

    first = workbench_tasks.run_task(
        "spawn-fail-1",
        ["python", "-m", "tools.workbench", "doctor"],
        root=root,
        popen=failing_spawn,
    )
    assert first.status == "failed"
    assert _state(root, "spawn-fail-1")["spawn_failed"] is True

    resumed = workbench_tasks.resume_task("spawn-fail-1", root=root, popen=_FakeSpawn([0]))
    assert resumed.attempt == 2
    assert resumed.status == "completed"


def test_status_reports_recorded_state_and_probe(request, monkeypatch) -> None:
    root = _workdir(request, "status")
    workbench_tasks.run_task(
        "stat-1",
        ["python", "-m", "tools.workbench", "doctor"],
        root=root,
        popen=_FakeSpawn([0]),
    )
    monkeypatch.setattr(workbench_tasks, "process_status", lambda pid: "dead")

    status = workbench_tasks.task_status("stat-1", root=root)

    assert status["status"] == "completed"
    assert status["process_probe"] == {"wrapper": "dead", "child": "dead"}
    with pytest.raises(workbench_tasks.TaskError):
        workbench_tasks.task_status("missing-1", root=root)


def test_cli_run_status_and_duplicate_exit_codes(request, monkeypatch, capsys) -> None:
    root = _workdir(request, "cli")
    monkeypatch.setattr(workbench_tasks, "PROJECT_ROOT", root)
    monkeypatch.setattr(workbench, "PROJECT_ROOT", root)
    spawn = _FakeSpawn([0])
    monkeypatch.setattr(workbench_tasks.subprocess, "Popen", spawn)

    code = workbench.main(
        ["task", "run", "--id", "cli-1", "--", "python", "-m", "tools.workbench", "doctor"]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["status"] == "completed"
    assert spawn.calls[0]["shell"] is False
    assert str(root) not in json.dumps(payload)

    assert workbench.main(["task", "status", "--id", "cli-1", "--json"]) == 0
    assert "completed" in capsys.readouterr().out

    duplicate = workbench.main(
        ["task", "run", "--id", "cli-1", "--", "python", "-m", "tools.workbench", "doctor"]
    )
    assert duplicate == 2
    assert "task error" in capsys.readouterr().out


def test_registered_task_subcommands_parse() -> None:
    parser = workbench.build_parser()
    assert parser.parse_args(["task", "status", "--id", "x"]).task_action == "status"
    parsed = parser.parse_args(
        ["task", "run", "--id", "x", "--", "python", "-m", "tools.workbench", "doctor"]
    )
    assert parsed.task_action == "run"
    assert parsed.id == "x"


def test_real_end_to_end_run_completes() -> None:
    """One real subprocess through the unified entry point (no recursion).

    Uses ``validate --profile manual`` so the child never re-runs pytest, and a
    unique UUID task id so no existing task directory is touched.
    """

    task_id = f"e2e-{uuid.uuid4().hex[:12]}"
    layout = workbench_tasks.task_layout(task_id, root=PROJECT_ROOT)
    assert not layout.task_dir.exists()
    try:
        result = workbench_tasks.run_task(
            task_id,
            ["python", "-m", "tools.workbench", "validate", "--profile", "manual"],
            root=PROJECT_ROOT,
        )

        log = result.log_path.read_text(encoding="utf-8", errors="replace")
        assert result.exit_code == 0, log[-300:]
        assert result.status == "completed", log[-300:]
        assert "status: manual" in log
        assert "pytest" not in log.lower()
        assert sys.executable not in log
    finally:
        shutil.rmtree(layout.task_dir, ignore_errors=True)
