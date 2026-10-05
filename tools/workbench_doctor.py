"""``workbench doctor``: read-only environment self-check.

Design rules:

* never writes files and never opens the network unless ``--online`` is given;
* dependencies are probed with ``importlib.util.find_spec`` plus distribution
  metadata, so optional heavy runtimes (torch, funasr, faster_whisper, piper)
  are never imported or initialized;
* output is sanitized: no user absolute paths, no audio device names, no
  configuration URLs, and never the raw text of an exception (only its type
  and a coarse safety category);
* a missing optional dependency, model or device is a warning; a missing base
  dependency or an unsupported Python version is an error;
* the exit code follows the worst severity reported.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Iterable, Mapping, Sequence

try:  # packaging ships with pytest; a missing parser must not break import
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    from packaging.version import Version
except Exception:  # noqa: BLE001 - reported as a safe category later
    Requirement = None  # type: ignore[assignment]
    canonicalize_name = None  # type: ignore[assignment]
    Version = None  # type: ignore[assignment]

_PARSER_MISSING = Requirement is None or canonicalize_name is None or Version is None

from srtp_voice.config import AppConfig
from srtp_voice.diagnostics import collect_diagnostics

from .workbench import PROJECT_ROOT, resolve_project_path, sanitize_text

SEVERITY_ORDER = {"ok": 0, "warning": 1, "error": 2}

MIN_PYTHON = (3, 11)
TESTED_MAX_PYTHON = (3, 13)

# ``name`` is the distribution named in requirements.txt; ``module`` is what we
# probe with find_spec (None when the distribution has no importable module,
# for example the ``piper`` CLI wheel).
BASE_DEPENDENCIES: tuple[tuple[str, str | None], ...] = (
    ("pytest", "pytest"),
    ("soundfile", "soundfile"),
    ("sounddevice", "sounddevice"),
    ("python-dotenv", "dotenv"),
    ("requests", "requests"),
    ("pyserial", "serial"),
    ("edge-tts", "edge_tts"),
)
OPTIONAL_DEPENDENCIES: tuple[tuple[str, str | None], ...] = (
    ("faster-whisper", "faster_whisper"),
    ("funasr", "funasr"),
    ("torch", "torch"),
)

_EXCEPTION_CATEGORIES = {
    "ModuleNotFoundError": "dependency_missing",
    "ImportError": "dependency_missing",
    "PackageNotFoundError": "distribution_missing",
    "PermissionError": "permission_denied",
    "OSError": "io_error",
    "ValueError": "invalid_value",
    "TypeError": "invalid_type",
    "json.JSONDecodeError": "invalid_payload",
}

ERROR = "error"
WARNING = "warning"
OK = "ok"

_MAX_METADATA_BYTES = 1_000_000_000


class DoctorError(ValueError):
    """Raised for invalid doctor invocations."""


def worse(left: str, right: str) -> str:
    return left if SEVERITY_ORDER[left] >= SEVERITY_ORDER[right] else right


def safe_exception(exc: BaseException) -> str:
    """Return ``ExceptionType:category`` without echoing the raw message."""

    name = type(exc).__name__
    category = _EXCEPTION_CATEGORIES.get(name, "unexpected")
    return f"{name}:{category}"


def check_python(version_info: Sequence[int]) -> dict[str, Any]:
    major, minor = int(version_info[0]), int(version_info[1])
    version = f"{major}.{minor}"
    if (major, minor) < MIN_PYTHON:
        return {
            "status": ERROR,
            "version": version,
            "supported": False,
            "reason": "below_minimum",
            "minimum": "3.11",
        }
    if (major, minor) > TESTED_MAX_PYTHON:
        return {
            "status": WARNING,
            "version": version,
            "supported": True,
            "reason": "newer_than_tested",
            "tested_max": "3.13",
        }
    return {
        "status": OK,
        "version": version,
        "supported": True,
        "reason": None,
        "minimum": "3.11",
    }


def _distribution_version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None
    except Exception:  # noqa: BLE001 - metadata problems must not crash doctor
        return None


def _declared_constraints(root: Path | None = None) -> dict[str, Any] | None:
    """Read constraints once; None means declarations cannot be verified."""

    if _PARSER_MISSING:
        return None
    base = (Path(root) if root is not None else PROJECT_ROOT).resolve()
    constraints: dict[str, Any] = {}
    try:
        lines = (base / "requirements.txt").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        # Unreadable or undecodable declarations stay unverifiable; the raw
        # error text is never surfaced.
        return None
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        try:
            requirement = Requirement(text)
        except Exception:  # noqa: BLE001 - never silently weaken constraints
            return None
        key = canonicalize_name(requirement.name)
        existing = constraints.get(key)
        # Duplicate declaration lines must intersect, never overwrite.
        constraints[key] = (
            existing & requirement.specifier if existing is not None else requirement.specifier
        )
    return constraints


def _declared_constraint(distribution: str, declared: Mapping[str, Any]) -> Any:
    try:
        key = canonicalize_name(distribution)
    except Exception:  # noqa: BLE001
        return None
    return declared.get(key)


def _check_declared_version(
    distribution: str,
    version: Any,
    declared: Mapping[str, Any] | None,
    *,
    base: bool,
) -> dict[str, Any]:
    """PEP 440 check of one installed version against the declared constraint."""

    if _PARSER_MISSING:
        # No declared-version verification is possible without a parser.
        return {
            "status": ERROR if base else WARNING,
            "detail": "declared_version_parser_unavailable",
        }
    if declared is None or not isinstance(version, str):
        return {"status": ERROR if base else WARNING, "detail": "declared_version_unverifiable"}
    try:
        installed = Version(version)
    except Exception:  # noqa: BLE001 - invalid metadata must not echo the value
        return {"status": ERROR if base else WARNING, "detail": "version_metadata_invalid"}
    specifier = _declared_constraint(distribution, declared)
    if specifier is None:
        if base:
            return {"status": ERROR, "detail": "declared_version_unverifiable"}
        # Known declarations without this optional runtime impose no minimum.
        # Its module and safe metadata are presence evidence, not a model test.
        return {"status": OK, "detail": None}
    if not specifier.contains(installed, prereleases=True):
        return {"status": ERROR if base else WARNING, "detail": "declared_version_unsatisfied"}
    return {"status": OK, "detail": None}


def dependency_report(
    entries: Iterable[tuple[str, str | None]],
    *,
    base: bool,
    spec_finder: Callable[[str], object | None] = importlib.util.find_spec,
) -> list[dict[str, Any]]:
    """Probe dependencies without importing them and enforce requirements.txt."""

    declared = _declared_constraints()
    report: list[dict[str, Any]] = []
    for distribution, module in entries:
        present = True
        detail: str | None = None
        if module is not None:
            try:
                present = spec_finder(module) is not None
            except Exception as exc:  # noqa: BLE001 - never echo the message
                present = False
                detail = safe_exception(exc)
        version: Any = None
        if present:
            raw_version = _distribution_version(distribution)
            if isinstance(raw_version, str) and Version is not None:
                try:
                    # Only a successfully parsed version is safe diagnostic
                    # evidence; an old-but-valid version is kept as-is.
                    version = str(Version(raw_version))
                except Exception:  # noqa: BLE001 - invalid metadata is dropped
                    version = None
        status = OK if present else (ERROR if base else WARNING)
        if present:
            check = _check_declared_version(
                distribution, raw_version, declared, base=base
            )
            if check["status"] != OK:
                status = check["status"]
                detail = check["detail"]
        elif detail is None:
            detail = "not_installed"
        entry = {
            "name": distribution,
            "module": module,
            "present": bool(present),
            "version": version,
            "status": status,
            "detail": detail,
        }
        report.append(entry)
    return report


def is_remote_namespace(path: Any) -> bool:
    """True for UNC and network-namespace paths; never touches the filesystem."""

    if path is None:
        return False
    try:
        raw = os.fspath(path)
    except TypeError:
        return False
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str):
        return False
    text = raw.strip()
    if not text:
        return False
    # Normalize both separators in memory first: Windows accepts "\/server" and
    # "/\server" as UNC, and POSIX Path("//server/share") silently drops one
    # leading slash. Block any multi-prefix form conservatively; nothing here
    # resolves or touches the filesystem.
    normalized = text.replace("\\", "/")
    return normalized.startswith("//")


def is_foreign_absolute_path(path: Path | str, *, native_path_type=Path) -> bool:
    """Classify path syntax in memory, without rejecting native absolute paths."""
    raw = str(path)
    return not native_path_type(raw).is_absolute() and (
        bool(PureWindowsPath(raw).drive) or PurePosixPath(raw).is_absolute()
    )


def describe_path(
    path: Path | str,
    project_root: Path,
    *,
    exists: bool | None = None,
    location: str | None = None,
) -> dict[str, Any]:
    """Report existence/size for a path without leaking absolute locations.

    UNC/network paths are classified and returned untouched: no resolve(),
    exists() or stat() is issued, so an offline or ``--online`` run never
    reaches an SMB share. Their state is reported as unknown, not missing.
    """

    if is_remote_namespace(path):
        return {"location": "<network-path>", "exists": None, "bytes": None, "probed": False}
    root = Path(project_root).resolve()
    candidate = Path(path)
    raw = str(path)
    # A Windows path is a relative filename on Linux (and vice versa); a POSIX
    # absolute path is drive-relative on Windows. Detect both before native
    # resolution so nothing foreign is probed and no raw path is echoed.
    foreign_absolute = is_foreign_absolute_path(raw)
    if foreign_absolute:
        return {"location": "<outside-project>", "exists": exists, "bytes": None, "probed": False}
    candidate = candidate if candidate.is_absolute() else root / candidate
    if location is None:
        root = Path(project_root).resolve()
        try:
            resolved = candidate.resolve()
        except OSError:
            return {"location": "unresolved", "exists": None, "bytes": None, "probed": False}
        if resolved == root or root in resolved.parents:
            # Reuse the component sanitizer: a POSIX-legal filename may contain
            # Windows separators, a drive letter or a UNC prefix, and none of
            # those may leak a username or a share name into the report.
            from .workbench_latency import _safe_relative_location

            location = _safe_relative_location(resolved, root) or "<outside-project>"
        else:
            location = "<outside-project>"
    path = candidate
    probed = exists is not None
    if exists is None:
        try:
            exists = Path(path).exists()
            probed = True
        except OSError:
            exists = None
    size: int | None = None
    if exists:
        try:
            candidate = Path(path)
            if candidate.is_file():
                size = candidate.stat().st_size
        except OSError:
            size = None
    return {"location": location, "exists": None if exists is None else bool(exists), "bytes": size, "probed": probed}


def _check_url(url: str) -> tuple[bool, str | None]:
    """Validate a URL without echoing any part of it back to the caller."""

    try:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").strip().lower()
    except ValueError:
        return False, "unparsable"
    try:
        port = parts.port
    except ValueError:
        return False, "invalid_port"
    if parts.scheme not in {"http", "https"}:
        return False, "scheme_not_http"
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return False, "not_loopback"
    if parts.username is not None or parts.password is not None:
        return False, "credentials_present"
    if parts.query or parts.fragment:
        return False, "query_or_fragment_present"
    if port is not None and not (1 <= port <= 65535):
        return False, "invalid_port"
    return True, None


def tags_endpoint(chat_or_base_url: str) -> str | None:
    """Derive ``<origin>/api/tags`` from a chat endpoint or base URL."""

    try:
        parts = urllib.parse.urlsplit(chat_or_base_url)
    except ValueError:
        return None
    if not parts.scheme or not parts.netloc:
        return None
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, "/api/tags", "", ""))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def _default_fetch(url: str, timeout: float) -> Mapping[str, Any]:
    request = urllib.request.Request(url=url, method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with opener.open(request, timeout=timeout) as response:  # noqa: S310 - loopback only
        raw = response.read(65536)
    payload = json.loads(raw.decode("utf-8", errors="replace"))
    if not isinstance(payload, Mapping):
        raise ValueError("unexpected payload shape")
    return payload


def check_online(
    endpoint: str,
    model: str,
    *,
    fetch: Callable[[str, float], Mapping[str, Any]] | None = None,
    timeout: float = 3.0,
) -> dict[str, Any]:
    """GET ``/api/tags`` on the configured loopback origin and compare a model."""

    tags_url = tags_endpoint(endpoint)
    if tags_url is None:
        return {
            "status": WARNING,
            "checked": False,
            "reason": "unparsable_endpoint",
            "model_present": None,
        }
    # Validate the configured endpoint itself first, so credentials, query
    # strings or an out-of-range port are refused without dropping the reason.
    raw_ok, raw_reason = _check_url(endpoint)
    if not raw_ok:
        return {
            "status": WARNING,
            "checked": False,
            "reason": raw_reason,
            "model_present": None,
        }
    ok, reason = _check_url(tags_url)
    if not ok:
        return {
            "status": WARNING,
            "checked": False,
            "reason": reason,
            "model_present": None,
        }
    effective_timeout = max(0.1, min(float(timeout), 3.0))
    fetcher = fetch or _default_fetch
    try:
        payload = fetcher(tags_url, effective_timeout)
    except Exception as exc:  # noqa: BLE001 - category only, never the message
        return {
            "status": WARNING,
            "checked": True,
            "reason": safe_exception(exc),
            "model_present": None,
        }
    names: list[str] = []
    models = payload.get("models") if isinstance(payload, Mapping) else None
    if isinstance(models, list):
        for item in models:
            if isinstance(item, Mapping):
                for key in ("name", "model"):
                    value = item.get(key)
                    if isinstance(value, str) and value:
                        names.append(value)
    present = model in names or any(name.split(":")[0] == model for name in names)
    return {
        "status": OK if present else WARNING,
        "checked": True,
        "reason": None if present else "model_not_listed",
        "model_present": bool(present),
    }


def summarize_audio(diagnostics: Mapping[str, Any]) -> dict[str, Any]:
    """Reduce audio diagnostics to sanitized booleans/versions."""

    audio = diagnostics.get("audio")
    if not isinstance(audio, Mapping):
        return {"status": WARNING, "available": False}
    summary: dict[str, Any] = {}
    for name in ("soundfile", "sounddevice"):
        entry = audio.get(name)
        if isinstance(entry, Mapping):
            summary[name] = {
                "status": entry.get("status"),
                "present": bool(entry.get("available")),
                "version": entry.get("version"),
            }
        else:
            summary[name] = {"status": WARNING, "present": False, "version": None}
    portaudio = audio.get("portaudio")
    summary["portaudio"] = {
        "status": portaudio.get("status") if isinstance(portaudio, Mapping) else WARNING,
        "version": portaudio.get("version") if isinstance(portaudio, Mapping) else None,
    }
    # Device names/indices are intentionally dropped; only presence is reported.
    for key in ("default_input", "default_output"):
        entry = audio.get(key)
        summary[key] = {
            "status": entry.get("status") if isinstance(entry, Mapping) else WARNING,
            "present": bool(isinstance(entry, Mapping) and entry.get("status") == "ok"),
        }
    summary["status"] = OK
    for key in ("soundfile", "sounddevice", "portaudio", "default_input", "default_output"):
        value = summary.get(key)
        status = value.get("status") if isinstance(value, Mapping) else WARNING
        if status == "warning":
            summary["status"] = WARNING
    return summary


def _local_path_report(cfg: AppConfig, root: Path, entry: Mapping[str, Any]) -> dict[str, Any]:
    """Safely inspect one local model path; network paths stay unprobed."""

    raw = entry.get("path")
    if not raw:
        return {"location": "<not-configured>", "exists": None, "bytes": None, "probed": False}
    if is_remote_namespace(raw):
        return {"location": "<network-path>", "exists": None, "bytes": None, "probed": False}
    if entry.get("probed") is False:
        # The diagnostics collector did not probe; do a safe local check here.
        return describe_path(raw, root)
    exists = entry.get("exists")
    return describe_path(raw, root, exists=None if exists is None else bool(exists))


def _model_reports(
    cfg: AppConfig,
    root: Path,
    diagnostics: Mapping[str, Any] | None,
) -> dict[str, Any]:
    models = {
        "piper_model": describe_path(cfg.tts_piper_model, root),
        "piper_executable": describe_path(cfg.tts_piper_exe, root),
    }
    ser_entry: Mapping[str, Any] | None = None
    if isinstance(diagnostics, Mapping):
        paths = diagnostics.get("paths")
        if isinstance(paths, Mapping) and isinstance(paths.get("ser_model"), Mapping):
            ser_entry = paths["ser_model"]
    if ser_entry is not None:
        models["ser_model"] = _local_path_report(cfg, root, ser_entry)
    elif cfg.ser_model is not None:
        models["ser_model"] = describe_path(cfg.ser_model, root)
    else:
        models["ser_model"] = {
            "location": "<resolved-by-diagnostics>",
            "exists": None,
            "bytes": None,
            "probed": False,
        }
    return models


def collect_doctor_report(
    *,
    cfg: AppConfig | None = None,
    cfg_loaded: bool | None = None,
    project_root: Path | None = None,
    python_version: Sequence[int] | None = None,
    collect: Callable[[AppConfig], Mapping[str, Any]] | None = None,
    fetch: Callable[[str, float], Mapping[str, Any]] | None = None,
    online: bool = False,
) -> dict[str, Any]:
    root = Path(project_root).resolve() if project_root is not None else resolve_project_path(".")
    config = cfg if cfg is not None else AppConfig()
    loaded = cfg is not None if cfg_loaded is None else bool(cfg_loaded)
    version_info = tuple(python_version if python_version is not None else sys.version_info[:3])

    python = check_python(version_info)
    base = dependency_report(BASE_DEPENDENCIES, base=True)
    optional = dependency_report(OPTIONAL_DEPENDENCIES, base=False)

    diagnostics: Mapping[str, Any] | None = None
    audio: dict[str, Any]
    try:
        if collect is not None:
            diagnostics = collect(config)
        else:
            # Probe only audio and metadata inside diagnostics; model paths are
            # classified here first so UNC/network namespaces are never touched.
            diagnostics = collect_diagnostics(config, probe_paths=False)
        audio = summarize_audio(diagnostics)
    except Exception as exc:  # noqa: BLE001 - read-only probe must not crash doctor
        audio = {"status": WARNING, "available": False, "detail": safe_exception(exc)}

    models = _model_reports(config, root, diagnostics)

    disk: dict[str, Any]
    try:
        usage = shutil.disk_usage(root)
        disk = {"status": OK, "free_bytes": int(usage.free), "total_bytes": int(usage.total)}
    except Exception as exc:  # noqa: BLE001
        disk = {
            "status": WARNING,
            "free_bytes": None,
            "total_bytes": None,
            "detail": safe_exception(exc),
        }

    severity = worse(python["status"], worse(audio.get("status", WARNING), disk["status"]))
    for entry in base:
        severity = worse(severity, entry["status"])
    for entry in optional:
        severity = worse(severity, entry["status"])
    for name, entry in models.items():
        if entry.get("exists") is None:
            if entry.get("location") == "<network-path>":
                severity = worse(severity, WARNING)
            continue
        if not entry["exists"]:
            severity = worse(severity, WARNING)

    online_report: dict[str, Any] | None = None
    if online:
        try:
            online_report = check_online(
                config.llm_ollama_chat_url or config.llm_ollama_base_url,
                config.llm_model,
                fetch=fetch,
            )
        except Exception as exc:  # noqa: BLE001
            online_report = {
                "status": WARNING,
                "checked": False,
                "reason": safe_exception(exc),
                "model_present": None,
            }
        severity = worse(severity, str(online_report.get("status", WARNING)))

    return {
        "severity": severity,
        "config_source": "from_env" if loaded else "defaults",
        "python": python,
        "dependencies": {"base": base, "optional": optional},
        "audio": audio,
        "models": models,
        "disk": disk,
        "online": online_report,
    }


def _format_dependency(entry: Mapping[str, Any]) -> str:
    # The rendered label follows entry.status, never presence alone: a present
    # module with an unverifiable or unsatisfied declared version is not OK.
    status = str(entry.get("status", WARNING))
    if entry.get("present") and status == OK:
        return f"[OK] {entry['name']}: version={entry.get('version') or 'unknown'}"
    return f"[{status.upper()}] {entry['name']}: {entry.get('detail')}"


def render_report(report: Mapping[str, Any]) -> str:
    lines: list[str] = ["=== SRTP workbench doctor ==="]
    python = report["python"]
    lines.append(
        f"[{str(python['status']).upper()}] Python {python['version']} "
        f"(supported={python['supported']}, reason={python.get('reason') or 'none'})"
    )
    lines.append(f"config source: {report.get('config_source')}")
    lines.append("-- base dependencies --")
    for entry in report["dependencies"]["base"]:
        lines.append("  " + _format_dependency(entry))
    lines.append("-- optional dependencies --")
    for entry in report["dependencies"]["optional"]:
        lines.append("  " + _format_dependency(entry))
    audio = report["audio"]
    lines.append(f"[{str(audio.get('status', WARNING)).upper()}] audio environment")
    for key in ("soundfile", "sounddevice", "portaudio", "default_input", "default_output"):
        entry = audio.get(key)
        if isinstance(entry, Mapping):
            lines.append(
                f"  {key}: status={entry.get('status')} "
                f"version={entry.get('version')} present={entry.get('present')}"
            )
    lines.append("-- model files --")
    for name, entry in report["models"].items():
        lines.append(
            f"  {name}: location={entry.get('location')} "
            f"exists={entry.get('exists')} bytes={entry.get('bytes')}"
        )
    disk = report["disk"]
    lines.append(f"[{str(disk['status']).upper()}] disk free_bytes={disk.get('free_bytes')}")
    if report.get("online") is not None:
        online = report["online"]
        lines.append(
            f"[{str(online.get('status', WARNING)).upper()}] online check "
            f"checked={online.get('checked')} "
            f"model_present={online.get('model_present')} reason={online.get('reason')}"
        )
    lines.append(f"SEVERITY={report['severity']}")
    return "\n".join(lines)


def register(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = subparsers.add_parser(
        "doctor",
        help="read-only environment self-check (no writes, no network by default)",
    )
    parser.add_argument("--online", action="store_true", help="probe a loopback Ollama endpoint")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.set_defaults(handler=_handle)


def _handle(args: argparse.Namespace) -> int:
    cfg: AppConfig | None = None
    cfg_loaded = False
    load_error: str | None = None
    try:
        cfg = AppConfig.from_env()
        cfg_loaded = True
    except Exception as exc:  # noqa: BLE001 - configuration must not crash doctor
        load_error = safe_exception(exc)

    report = collect_doctor_report(
        cfg=cfg,
        cfg_loaded=cfg_loaded,
        online=bool(getattr(args, "online", False)),
    )
    if load_error is not None:
        report["config_load_error"] = load_error
        report["severity"] = worse(str(report["severity"]), WARNING)
    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(sanitize_text(render_report(report)))
    return SEVERITY_ORDER[str(report["severity"])]
