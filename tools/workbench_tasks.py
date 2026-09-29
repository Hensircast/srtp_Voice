"""``workbench task``: resumable, non-shell task runner for this workbench only.

Allowed commands are limited to ``python -m tools.workbench <doctor|validate|
baseline|snapshot> ...``. No shell, no ``python -c``, no installs, no nested
tasks. Every attempt is recorded under ``outputs/workbench/tasks/<ID>/`` with an
atomic state file, so a later session can resume without guessing.

Windows note: liveness is probed read-only through ``OpenProcess`` /
``GetExitCodeProcess``; ``os.kill(pid, 0)`` is never used there because it can
terminate the target.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .workbench import PROJECT_ROOT, is_within_project, resolve_project_path
from . import workbench_validate

TASKS_RELATIVE = Path("outputs") / "workbench" / "tasks"
ALLOWED_SUBCOMMANDS: tuple[str, ...] = ("doctor", "validate", "baseline", "snapshot")
TASK_MODULE = "tools.workbench"
STATE_TERMINAL_OK = {"completed"}
STATE_TERMINAL_BAD = {"failed", "interrupted"}
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SAFE_STATUS = {
    "running": "running",
    "dead": "dead",
    "unknown": "unknown",
}


class TaskError(ValueError):
    """Raised for invalid task requests; messages never echo foreign values."""


@dataclass
class TaskLayout:
    root: Path
    task_dir: Path
    manifest: Path
    state: Path
    lock: Path
    attempt_logs: Path


@dataclass
class RunResult:
    task_id: str
    attempt: int
    status: str
    exit_code: int | None
    wrapper_pid: int | None
    child_pid: int | None
    log_path: Path
    manifest: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# layout and validation
# --------------------------------------------------------------------------- #
def validate_task_id(task_id: str, *, root: Path | None = None) -> str:
    if not isinstance(task_id, str) or not _ID_PATTERN.fullmatch(task_id):
        raise TaskError("task id must be 1-64 characters of letters, digits, dot, dash or underscore")
    if task_id in {".", ".."}:
        raise TaskError("task id must be a plain basename")
    return task_id


def task_layout(task_id: str, *, root: Path | None = None) -> TaskLayout:
    base = (Path(root) if root is not None else PROJECT_ROOT).resolve()
    safe_id = validate_task_id(task_id, root=base)
    tasks_root = (base / TASKS_RELATIVE).resolve()
    if tasks_root != base and base not in tasks_root.parents:
        raise TaskError("tasks root escapes the project root")
    task_dir = (tasks_root / safe_id).resolve()
    if task_dir != tasks_root and tasks_root not in task_dir.parents:
        raise TaskError("task directory escapes the workbench tasks root")
    return TaskLayout(
        root=base,
        task_dir=task_dir,
        manifest=task_dir / "manifest.json",
        state=task_dir / "state.json",
        lock=task_dir / "lock.json",
        attempt_logs=task_dir / "attempts",
    )


def _normalize_display(argv: Sequence[str], python: str) -> list[str]:
    displayed = [str(token) for token in argv]
    for index, token in enumerate(displayed):
        if token == python:
            displayed[index] = "python"
        elif token.endswith(("python.exe", "python3", "python")):
            displayed[index] = "python"
    return displayed


_DENIED_FLAGS = ("--token", "--api-key", "--apikey", "--secret", "--password", "--cookie")
_SUBCOMMAND_FLAGS: dict[str, tuple[str, ...]] = {
    "doctor": ("--online", "--json"),
    "validate": ("--profile",),
    "baseline": ("--metrics", "--output", "--compare", "--measurement", "--label", "--recording-metadata"),
    "snapshot": ("--output", "--label"),
}
_DENIED_ARG_TOKENS = ("http://", "https://")


def _check_project_path(token: str, base: Path) -> None:
    candidate = Path(token)
    if candidate.is_absolute():
        raise TaskError("task command paths must stay inside the project")
    resolved = (base / candidate).resolve()
    if resolved != base and base not in resolved.parents:
        raise TaskError("task command path escapes the project root")


def validate_subcommand_arguments(
    rest: Sequence[str],
    *,
    root: Path | None = None,
) -> list[str]:
    """Validate one subcommand's argv; only known flags and project paths pass."""

    base = (Path(root) if root is not None else PROJECT_ROOT).resolve()
    if not rest:
        raise TaskError("a workbench subcommand is required")
    subcommand = rest[0]
    if subcommand not in ALLOWED_SUBCOMMANDS:
        raise TaskError("only doctor, validate, baseline and snapshot are allowed")
    if subcommand == "validate" and "--profile" not in rest:
        raise TaskError("validate requires an explicit --profile")
    allowed = _SUBCOMMAND_FLAGS[subcommand]
    normalized = list(rest)
    index = 1
    while index < len(rest):
        token = rest[index]
        lowered = token.lower()
        if any(denied in lowered for denied in _DENIED_FLAGS):
            raise TaskError("secret-like flags are not allowed in task commands")
        if any(bad in lowered for bad in _DENIED_ARG_TOKENS):
            raise TaskError("network or install tokens are not allowed in task commands")
        if token.startswith("-"):
            if token not in allowed:
                raise TaskError("unknown flag for this workbench subcommand")
            if token in ("--online", "--json"):
                index += 1
                continue
            if index + 1 >= len(rest):
                raise TaskError("flag is missing its value")
            value = rest[index + 1]
            if value.startswith("-"):
                raise TaskError("flag value must not be another flag")
            if token == "--profile":
                if value not in workbench_validate.PROFILES:
                    raise TaskError("unknown validation profile")
            elif token == "--measurement":
                if value not in {"real", "simulated", "unknown"}:
                    raise TaskError("unknown measurement kind")
            elif token == "--label":
                if not _ID_PATTERN.fullmatch(value):
                    raise TaskError("label must be a plain safe identifier")
            else:
                _check_project_path(value, base)
                resolved = (base / value).resolve()
                if resolved.suffix != ".json":
                    raise TaskError("evidence and output paths must be JSON files")
                if token == "--output" and base / "outputs" not in resolved.parents:
                    raise TaskError("output must live under outputs")
                normalized[index + 1] = resolved.relative_to(base).as_posix()
            index += 2
            continue
        _check_project_path(token, base)
        normalized[index] = (base / token).resolve().relative_to(base).as_posix()
        index += 1
    # Reuse the actual CLI grammar, suppressing argparse's raw-value echo.
    from .workbench import build_parser
    with contextlib.redirect_stderr(io.StringIO()):
        try:
            args = build_parser().parse_args(normalized)
        except SystemExit as exc:
            raise TaskError("invalid workbench arguments") from exc
    if subcommand == "validate":
        try:
            workbench_validate.build_plan(args.profile, args.tests, root=base)
        except workbench_validate.ValidationError as exc:
            raise TaskError("invalid validation targets") from exc
    return normalized


def prepare_task_command(
    argv: Sequence[str],
    *,
    python: str | None = None,
    root: Path | None = None,
) -> tuple[list[str], list[str]]:
    """Validate a workbench-only command; return (execution argv, display argv).

    Only the exact prefix ``python -m tools.workbench <subcommand>`` is
    accepted: no interpreter options may appear before ``-m``, and subcommand
    arguments are validated before anything is written or started.
    """

    if not argv:
        raise TaskError("a task command is required")
    tokens = [str(token) for token in argv]
    interpreter = python or sys.executable
    if Path(tokens[0]).name.lower().startswith("python"):
        tokens[0] = interpreter
    else:
        raise TaskError("task commands must start with the python interpreter")
    if len(tokens) < 2:
        raise TaskError("task commands must use python -m <module>")
    # No interpreter option (for example -c, -W, -X, --) may precede -m.
    if tokens[1] != "-m":
        raise TaskError("python options before -m are not allowed")
    if len(tokens) < 3:
        raise TaskError("task commands must use python -m <module>")
    module = tokens[2]
    if module != TASK_MODULE:
        raise TaskError("only the workbench module may be started")
    rest = tokens[3:]
    if "task" in rest:
        raise TaskError("nested task commands are not allowed")
    tokens[3:] = validate_subcommand_arguments(rest, root=root)
    return tokens, _normalize_display(tokens, interpreter)


# --------------------------------------------------------------------------- #
# process liveness (read-only)
# --------------------------------------------------------------------------- #
def process_status(pid: Any) -> str:
    """Return running/dead/unknown without ever signalling the process."""

    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return "unknown"
    if os.name == "nt":
        return _windows_process_status(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "dead"
    except PermissionError:
        return "unknown"
    except OSError:
        return "unknown"
    return "running"


def _windows_process_status(pid: int) -> str:
    import ctypes  # noqa: PLC0415 - Windows-only helper
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        return "dead" if error == 87 else "unknown"
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return "unknown"
        return "running" if code.value == still_active else "dead"
    finally:
        kernel32.CloseHandle(handle)


# --------------------------------------------------------------------------- #
# persistence
# --------------------------------------------------------------------------- #
def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def acquire_lock(layout: TaskLayout, attempt: int) -> None:
    layout.task_dir.mkdir(parents=True, exist_ok=True)
    payload = {"attempt": attempt, "wrapper_pid": os.getpid(), "started_at": time.time()}
    try:
        with open(layout.lock, "x", encoding="utf-8") as handle:
            json.dump(payload, handle)
    except FileExistsError as exc:
        raise TaskError("task id is already running or paused; refusing to start a second attempt") from exc


def release_lock(layout: TaskLayout) -> None:
    try:
        layout.lock.unlink()
    except OSError:
        pass


def load_state(task_id: str, *, root: Path | None = None) -> dict[str, Any]:
    layout = task_layout(task_id, root=root)
    state = _read_json(layout.state)
    if state is None:
        raise TaskError("task state is missing or unreadable")
    return state


def task_status(task_id: str, *, root: Path | None = None) -> dict[str, Any]:
    layout = task_layout(task_id, root=root)
    state = _read_json(layout.state)
    if state is None:
        raise TaskError("task state is missing or unreadable")
    probe = probe_state(state)
    state["process_probe"] = probe
    return state


def probe_state(state: Mapping[str, Any]) -> dict[str, str]:
    return {
        "wrapper": process_status(state.get("wrapper_pid")),
        "child": process_status(state.get("child_pid")),
    }


# --------------------------------------------------------------------------- #
# run / resume
# --------------------------------------------------------------------------- #
def _attempt_log_path(layout: TaskLayout, attempt: int) -> Path:
    layout.attempt_logs.mkdir(parents=True, exist_ok=True)
    return layout.attempt_logs / f"attempt-{attempt:02d}.log"


def run_task(
    task_id: str,
    command: Sequence[str],
    *,
    root: Path | None = None,
    popen: Callable[..., subprocess.Popen] | None = None,
) -> RunResult:
    """Start one attempt; refuses duplicate ids and records real execution only."""

    layout = task_layout(task_id, root=root)
    if layout.manifest.exists() or layout.state.exists():
        raise TaskError("task id already exists; choose a new id instead of overwriting history")
    execution, displayed = prepare_task_command(command, root=layout.root)

    existing = _read_json(layout.state)
    attempt = int(existing.get("attempt", 0)) + 1 if existing else 1
    acquire_lock(layout, attempt)
    # Check again while holding the lock: a competing attempt may have
    # completed between our first check and acquiring it.
    if layout.manifest.exists() or layout.state.exists():
        release_lock(layout)
        raise TaskError("task id already exists; history was not overwritten")

    started_at = time.time()
    manifest = {
        "task_id": task_id,
        "created_at": started_at,
        "cwd": ".",
        "command": displayed,
        "allowed": list(ALLOWED_SUBCOMMANDS),
    }
    state: dict[str, Any] = {
        "task_id": task_id,
        "status": "running",
        "attempt": attempt,
        "wrapper_pid": os.getpid(),
        "child_pid": None,
        "child_state": "not_started",
        "exit_code": None,
        "started_at": started_at,
        "finished_at": None,
        "command": displayed,
        "cwd": ".",
        "history": list(existing.get("history", [])) if existing else [],
    }
    try:
        _write_json_atomic(layout.manifest, manifest)
        _write_json_atomic(layout.state, state)
        log_path = _attempt_log_path(layout, attempt)
    except OSError as exc:
        release_lock(layout)
        raise TaskError("task storage is unavailable") from exc
    spawn = popen or subprocess.Popen
    exit_code: int | None = None
    status = "failed"
    child: Any = None
    try:
        with open(log_path, "ab") as log:
            log.write(f"$ {' '.join(displayed)}\n".encode("utf-8"))
            log.flush()
            child = spawn(
                execution,
                cwd=str(layout.root),
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            state["child_pid"] = int(getattr(child, "pid", 0)) or None
            state["child_state"] = "running"
            _write_json_atomic(layout.state, state)
            exit_code = int(child.wait())
            state["child_state"] = "exited"
        status = ("unknown" if exit_code < 0 or exit_code in {130, 143}
                  else "completed" if exit_code == 0 else "failed")
    except KeyboardInterrupt:
        status, exit_code = _stop_own_child(child)
        state["child_state"] = "not_started" if child is None else "unknown"
    except OSError as exc:
        status = "failed" if child is None else "unknown"
        exit_code = None
        state["spawn_failed"] = child is None
        with open(log_path, "ab") as log:
            log.write(f"process start failed: {type(exc).__name__}\n".encode("utf-8"))
    finally:
        state["status"] = status
        state["exit_code"] = exit_code
        state["finished_at"] = time.time()
        state["history"] = list(state.get("history", [])) + [
            {"attempt": attempt, "status": status, "exit_code": exit_code, "started_at": started_at}
        ]
        try:
            _write_json_atomic(layout.state, state)
        finally:
            release_lock(layout)

    return RunResult(
        task_id=task_id,
        attempt=attempt,
        status=status,
        exit_code=exit_code,
        wrapper_pid=state.get("wrapper_pid"),
        child_pid=state.get("child_pid"),
        log_path=log_path,
        manifest=manifest,
        state=state,
    )


def _stop_own_child(child: Any) -> tuple[str, int | None]:
    """Stop only the Popen object this attempt created; never a raw pid.

    There is no pid-based kill anywhere: a pid could be recycled between the
    check and the signal, which risks terminating an unrelated process.
    """

    if child is None:
        return "interrupted", None
    try:
        if child.poll() is not None:
            return "unknown", None
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            return "unknown", None
    except (Exception, KeyboardInterrupt):  # a second Ctrl+C cannot prove tree exit
        return "unknown", None
    # Parent exit alone is not proof that grandchildren (e.g. pytest) ended.
    return "unknown", None


def resume_task(
    task_id: str,
    command: Sequence[str] | None = None,
    *,
    root: Path | None = None,
    popen: Callable[..., subprocess.Popen] | None = None,
) -> RunResult:
    """Resume an interrupted/failed task only when nothing else is running."""

    layout = task_layout(task_id, root=root)
    state = _read_json(layout.state)
    if state is None:
        raise TaskError("task state is missing or unreadable; nothing to resume")
    status = str(state.get("status"))
    probe = probe_state(state)
    no_child = state.get("child_pid") is None and (
        state.get("child_state") == "not_started" or bool(state.get("spawn_failed"))
    )
    if status in STATE_TERMINAL_OK:
        raise TaskError("task already completed successfully; refusing to resume")
    if probe["child"] == "running":
        raise TaskError("the child process is still running; refusing to resume")
    if state.get("wrapper_pid") != os.getpid() and probe["wrapper"] != "dead":
        raise TaskError("the task wrapper is still running; refusing to resume")
    if status == "running":
        raise TaskError("task is recorded as running; confirm it is stopped before resuming")
    if status == "unknown" or (probe["child"] == "unknown" and not no_child):
        raise TaskError("task liveness is unknown; refusing to resume without proof the old process ended")
    if status not in STATE_TERMINAL_BAD and status != "failed":
        raise TaskError("task state is not resumable")

    execution = list(command) if command else list(state.get("command", []))
    if not execution:
        raise TaskError("no command recorded for this task")
    execution, _displayed = prepare_task_command(execution, root=layout.root)

    attempt = int(state.get("attempt", 0)) + 1
    acquire_lock(layout, attempt)
    if _read_json(layout.state) != state:
        release_lock(layout)
        raise TaskError("task state changed; refusing to replay a stale attempt")
    state["status"] = "running"
    state["attempt"] = attempt
    state["wrapper_pid"] = os.getpid()
    state["child_pid"] = None
    state["child_state"] = "not_started"
    state["spawn_failed"] = False
    state["command"] = _displayed
    state["exit_code"] = None
    state["started_at"] = time.time()
    state["finished_at"] = None
    try:
        _write_json_atomic(layout.state, state)
        log_path = _attempt_log_path(layout, attempt)
    except OSError as exc:
        release_lock(layout)
        raise TaskError("task storage is unavailable") from exc
    spawn = popen or subprocess.Popen
    exit_code: int | None = None
    new_status = "failed"
    child: Any = None
    try:
        with open(log_path, "ab") as log:
            log.write(f"$ {' '.join(state.get('command', execution))}\n".encode("utf-8"))
            log.flush()
            child = spawn(
                execution,
                cwd=str(layout.root),
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            state["child_pid"] = int(getattr(child, "pid", 0)) or None
            state["child_state"] = "running"
            _write_json_atomic(layout.state, state)
            exit_code = int(child.wait())
            state["child_state"] = "exited"
        new_status = ("unknown" if exit_code < 0 or exit_code in {130, 143}
                      else "completed" if exit_code == 0 else "failed")
    except KeyboardInterrupt:
        new_status, exit_code = _stop_own_child(child)
        state["child_state"] = "not_started" if child is None else "unknown"
    except OSError as exc:
        new_status = "failed" if child is None else "unknown"
        state["spawn_failed"] = child is None
        with open(log_path, "ab") as log:
            log.write(f"process start failed: {type(exc).__name__}\n".encode("utf-8"))
    finally:
        state["status"] = new_status
        state["exit_code"] = exit_code
        state["finished_at"] = time.time()
        state["history"] = list(state.get("history", [])) + [
            {"attempt": attempt, "status": new_status, "exit_code": exit_code}
        ]
        try:
            _write_json_atomic(layout.state, state)
        finally:
            release_lock(layout)

    return RunResult(
        task_id=task_id,
        attempt=attempt,
        status=new_status,
        exit_code=exit_code,
        wrapper_pid=state.get("wrapper_pid"),
        child_pid=state.get("child_pid"),
        log_path=log_path,
        manifest=_read_json(layout.manifest) or {},
        state=state,
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def register(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = subparsers.add_parser("task", help="resumable workbench task runner")
    actions = parser.add_subparsers(dest="task_action", required=True)

    run = actions.add_parser("run", help="start a new task attempt")
    run.add_argument("--id", required=True)
    run.add_argument("command", nargs=argparse.REMAINDER)
    run.set_defaults(handler=_handle_run)

    status = actions.add_parser("status", help="show recorded task state")
    status.add_argument("--id", required=True)
    status.add_argument("--json", action="store_true")
    status.set_defaults(handler=_handle_status)

    resume = actions.add_parser("resume", help="resume a stopped task attempt")
    resume.add_argument("--id", required=True)
    resume.set_defaults(handler=_handle_resume)


def _strip_separator(argv: Sequence[str]) -> list[str]:
    tokens = list(argv)
    if tokens and tokens[0] == "--":
        tokens = tokens[1:]
    return tokens


def _handle_run(args: argparse.Namespace) -> int:
    try:
        result = run_task(args.id, _strip_separator(args.command))
    except TaskError as exc:
        print(f"task error: {exc}")
        return 2
    print(
        json.dumps(
            {
                "task_id": result.task_id,
                "attempt": result.attempt,
                "status": result.status,
                "exit_code": result.exit_code,
                "log": result.log_path.relative_to(PROJECT_ROOT).as_posix(),
            },
            ensure_ascii=False,
        )
    )
    return 0 if result.status == "completed" else 1


def _handle_status(args: argparse.Namespace) -> int:
    try:
        state = task_status(args.id)
    except TaskError as exc:
        print(f"task error: {exc}")
        return 2
    if getattr(args, "json", False):
        print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"task {state.get('task_id')}: status={state.get('status')} attempt={state.get('attempt')}")
        print(f"  command: {' '.join(state.get('command', []))}")
        print(f"  exit_code={state.get('exit_code')} probe={state.get('process_probe')}")
    return 0


def _handle_resume(args: argparse.Namespace) -> int:
    try:
        result = resume_task(args.id)
    except TaskError as exc:
        print(f"task error: {exc}")
        return 2
    print(
        json.dumps(
            {
                "task_id": result.task_id,
                "attempt": result.attempt,
                "status": result.status,
                "exit_code": result.exit_code,
            },
            ensure_ascii=False,
        )
    )
    return 0 if result.status == "completed" else 1
