"""Lightweight project workbench: environment self-check, validation, latency baseline.

Entry point: ``python -m tools.workbench <doctor|validate|baseline>``.

The module also hosts the small shared helpers (project-root resolution and text
sanitizing) so that every subcommand resolves relative paths against the project
root and never leaks absolute user paths into its output.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Sequence

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent

_WINDOWS_ABSOLUTE = re.compile(r"[A-Za-z]:[\\/][^\s\"']*")
_POSIX_ABSOLUTE = re.compile(
    r"(?<![\w.])/(?:home|Users|root|tmp|var|opt|mnt|private|etc)/[^\s\"']*"
)


class PathEscapeError(ValueError):
    """Raised when a path would leave the project root."""


def resolve_project_path(value: str | os.PathLike[str], *, root: Path | None = None) -> Path:
    """Resolve ``value`` against the project root and refuse to escape it."""

    base = (Path(root) if root is not None else PROJECT_ROOT).resolve()
    candidate = Path(value)
    resolved = candidate.resolve() if candidate.is_absolute() else (base / candidate).resolve()
    if resolved != base and base not in resolved.parents:
        raise PathEscapeError("path escapes the project root")
    return resolved


def resolve_project_path_or_escape(
    value: str | os.PathLike[str],
    *,
    root: Path | None = None,
) -> tuple[Path, bool]:
    """Resolve a possibly-external path, reporting whether it escaped the root.

    Relative inputs are always interpreted against the project root (they must
    stay inside it); absolute inputs are allowed to reference local evidence
    outside the project, and the caller is told so it can sanitize its output.
    """

    base = (Path(root) if root is not None else PROJECT_ROOT).resolve()
    candidate = Path(value)
    resolved = candidate.resolve() if candidate.is_absolute() else (base / candidate).resolve()
    escaped = resolved != base and base not in resolved.parents
    if escaped and not candidate.is_absolute():
        raise PathEscapeError("relative path escapes the project root")
    return resolved, escaped


def is_within_project(path: Path, *, root: Path | None = None) -> bool:
    base = (Path(root) if root is not None else PROJECT_ROOT).resolve()
    resolved = Path(path).resolve()
    return resolved == base or base in resolved.parents


def sanitize_text(text: str, *, root: Path | None = None) -> str:
    """Strip absolute paths from free text before it is printed or stored."""

    base = (Path(root) if root is not None else PROJECT_ROOT).resolve()
    cleaned = str(text)
    for prefix in {str(base), base.as_posix()}:
        cleaned = cleaned.replace(prefix, "<project>")
    cleaned = _WINDOWS_ABSOLUTE.sub("<path>", cleaned)
    cleaned = _POSIX_ABSOLUTE.sub("<path>", cleaned)
    return cleaned


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.workbench",
        description="SRTP lightweight workbench (offline-friendly, read-only by default)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    from . import workbench_doctor, workbench_latency, workbench_tasks, workbench_validate

    workbench_doctor.register(subparsers)
    workbench_validate.register(subparsers)
    workbench_latency.register(subparsers)
    workbench_tasks.register(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    handler = getattr(args, "handler", None)
    if handler is None:  # pragma: no cover - argparse enforces subcommands
        parser.print_help()
        return 2
    return int(handler(args))


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess-free main()
    sys.exit(main())
