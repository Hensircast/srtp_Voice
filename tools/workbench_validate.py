"""``workbench validate``: one offline-friendly validation entry point.

Profiles:

* ``offline`` - ``compileall`` over the project sources plus ``pytest -q``;
* ``full`` - ``offline`` plus ``pip check`` (CI uses this profile);
* ``targeted`` - ``pytest -q`` limited to explicitly named files under ``tests/``;
* ``manual`` - prints the real-device test entry point; runs nothing.

Every subprocess uses an argv list, ``sys.executable``, a fixed project working
directory and ``shell=False``. No profile installs packages or touches the
network. Failures propagate as the child exit code, and process-start failures
are reported as a safe category instead of a traceback.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from .workbench import PROJECT_ROOT

MANUAL_ENTRY = "docs/development/V1.8_FIRST_AUDIO_LATENCY.md"

COMPILE_TARGETS: tuple[str, ...] = ("main.py", "srtp_voice", "tools", "tests")
PYTEST_TARGET = "tests"

PROFILES: tuple[str, ...] = ("offline", "full", "targeted", "manual")

PYTHON_ALIAS = "python"


class ValidationError(ValueError):
    """Raised for invalid validation requests (bad profile or unsafe target)."""


@dataclass
class CommandResult:
    argv: list[str]
    returncode: int
    status: str

    def display(self) -> str:
        parts = list(self.argv)
        if parts:
            parts[0] = PYTHON_ALIAS
        return " ".join(parts)

    def to_dict(self) -> dict[str, object]:
        return {
            "argv": list(self.argv),
            "command": self.display(),
            "returncode": self.returncode,
            "status": self.status,
        }


@dataclass
class ValidationOutcome:
    profile: str
    status: str
    commands: list[CommandResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def resolve_test_targets(
    targets: Sequence[str],
    *,
    tests_dir: Path | None = None,
    root: Path | None = None,
) -> list[str]:
    """Validate explicit ``tests/`` targets; reject escapes, flags and non-files."""

    base = (Path(root) if root is not None else PROJECT_ROOT).resolve()
    tests_root = (Path(tests_dir) if tests_dir is not None else base / "tests").resolve()
    if not targets:
        raise ValidationError("targeted profile requires explicit tests/ file paths")
    resolved: list[str] = []
    for raw in targets:
        if raw.startswith("-"):
            raise ValidationError("pytest flags are not allowed in targeted targets")
        candidate = Path(raw)
        if candidate.is_absolute():
            raise ValidationError("absolute test paths are not allowed")
        full = (base / candidate).resolve()
        if full == tests_root or tests_root not in full.parents:
            raise ValidationError("test target must live inside the tests directory")
        if full.suffix != ".py":
            raise ValidationError("test target must be a Python test file")
        if not full.is_file():
            raise ValidationError("test target must be an existing test file")
        resolved.append(full.relative_to(base).as_posix())
    return resolved


def _validated_max_failures(max_failures: int | None) -> int | None:
    """Accept only a real positive int; ``None`` keeps the old behaviour."""

    if max_failures is None:
        return None
    if isinstance(max_failures, bool) or not isinstance(max_failures, int):
        raise ValidationError("max_failures must be a positive integer or None")
    if max_failures <= 0:
        raise ValidationError("max_failures must be a positive integer or None")
    return max_failures


def build_plan(
    profile: str,
    targets: Sequence[str] = (),
    *,
    root: Path | None = None,
    tests_dir: Path | None = None,
    max_failures: int | None = None,
) -> list[list[str]]:
    """Return the exact argv sequences for ``profile`` without running them."""

    if profile not in PROFILES:
        raise ValidationError(f"unknown profile: {profile}")
    python = sys.executable
    limit = _validated_max_failures(max_failures)
    if profile == "manual":
        if targets:
            raise ValidationError("manual profile does not accept test targets")
        if limit is not None:
            raise ValidationError("manual profile does not run pytest")
        return []
    if profile != "targeted" and targets:
        raise ValidationError(f"profile {profile} does not accept test targets")
    pytest_flags = ["-q"]
    if limit is not None:
        # Only the pytest argv grows: compileall and pip check are untouched and
        # no extra process is started.
        pytest_flags.append(f"--maxfail={limit}")
    if profile == "targeted":
        selected = resolve_test_targets(targets, tests_dir=tests_dir, root=root)
        return [[python, "-m", "pytest", *pytest_flags, *selected]]
    plan = [[python, "-m", "compileall", "-q", *COMPILE_TARGETS]]
    plan.append([python, "-m", "pytest", *pytest_flags, PYTEST_TARGET])
    if profile == "full":
        plan.append([python, "-m", "pip", "check"])
    return plan


def run_plan(
    plan: Sequence[Sequence[str]],
    *,
    cwd: Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess] | None = None,
) -> list[CommandResult]:
    """Run ``plan`` with ``shell=False``; stop at the first failing command."""

    workdir = Path(cwd) if cwd is not None else PROJECT_ROOT
    execute = runner if runner is not None else subprocess.run
    results: list[CommandResult] = []
    for argv in plan:
        try:
            completed = execute(list(argv), cwd=str(workdir), shell=False)
        except KeyboardInterrupt:
            results.append(
                CommandResult(argv=list(argv), returncode=130, status="KeyboardInterrupt:interrupted")
            )
            break
        except OSError as exc:
            name = type(exc).__name__
            results.append(
                CommandResult(argv=list(argv), returncode=1, status=f"{name}:process_start")
            )
            break
        code = int(completed.returncode)
        results.append(
            CommandResult(argv=list(argv), returncode=code, status="ok" if code == 0 else "failed")
        )
        if code != 0:
            break
    return results


def run_validation(
    profile: str,
    targets: Sequence[str] = (),
    *,
    root: Path | None = None,
    tests_dir: Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess] | None = None,
    max_failures: int | None = None,
) -> ValidationOutcome:
    plan = build_plan(
        profile,
        targets,
        root=root,
        tests_dir=tests_dir,
        max_failures=max_failures,
    )
    if profile == "manual":
        return ValidationOutcome(
            profile=profile,
            status="manual",
            commands=[],
            notes=[
                "manual profile only prints the real-device entry point; nothing was run",
                f"entry: {MANUAL_ENTRY}",
            ],
        )
    results = run_plan(plan, cwd=root, runner=runner)
    failed = [item for item in results if item.returncode != 0]
    return ValidationOutcome(
        profile=profile,
        status="failed" if failed else "ok",
        commands=results,
        notes=[],
    )


def exit_code_for(outcome: ValidationOutcome) -> int:
    if outcome.status == "failed":
        for item in outcome.commands:
            if item.returncode != 0:
                return item.returncode
        return 1
    return 0


def render_outcome(outcome: ValidationOutcome) -> str:
    lines = [f"profile: {outcome.profile}", f"status: {outcome.status}"]
    for item in outcome.commands:
        lines.append(f"  $ {item.display()} -> {item.returncode} ({item.status})")
    for note in outcome.notes:
        lines.append(f"  note: {note}")
    return "\n".join(lines)


def register(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = subparsers.add_parser("validate", help="run the shared validation profiles")
    parser.add_argument("--profile", required=True, choices=list(PROFILES))
    parser.add_argument("tests", nargs="*", help="explicit tests/ files for --profile targeted")
    parser.add_argument(
        "--max-failures",
        type=int,
        default=None,
        help=(
            "optional positive integer: append --maxfail=N to the pytest command "
            "only (default: unchanged, no limit)"
        ),
    )
    parser.set_defaults(handler=_handle)


def _handle(args: argparse.Namespace) -> int:
    try:
        outcome = run_validation(
            args.profile,
            args.tests,
            max_failures=getattr(args, "max_failures", None),
        )
    except ValidationError as exc:
        print(f"validation error: {exc}")
        return 2
    print(render_outcome(outcome))
    return exit_code_for(outcome)
