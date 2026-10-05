"""``workbench baseline``: capture a metrics baseline without touching raw events.

Only timing keys are read from the source metrics file. Raw events, prompts,
audio paths, unknown keys and conversation text are never copied into the
output. The result JSON is written under ``outputs/`` with overwrite
protection.

Measured provenance is kept separate from capture provenance: the current
HEAD/config/Python/OS are recorded as ``capture_context``, while
``recording_context`` stays unknown unless the caller supplies real metadata
from the recording run itself. Comparisons without matching recording metadata
are reported as not comparable.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import inspect
import ipaddress
import json
import math
import os
import platform
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from srtp_voice.config import AppConfig
from srtp_voice.streaming import sanitize_llm_diagnostics

from .workbench import (
    PathEscapeError,
    PROJECT_ROOT,
    is_within_project,
    resolve_project_path,
    resolve_project_path_or_escape,
)

SCHEMA_VERSION = 2
SOURCE_SCHEMA = "srtp.streaming_metrics/v1"
MEASUREMENT_KINDS = ("real", "simulated", "unknown")
SNAPSHOT_KIND = "srtp.workbench.capture_snapshot/v1"

METRICS_KEYS: tuple[str, ...] = (
    "summary",
    "turns",
    "last_turn",
    "late_events",
    "dropped_event_history",
    "tts_backpressure_events",
    "cancelled",
    "failed",
)

SUMMARY_STATS = ("count", "min", "p50", "p95", "max")

# Non-sensitive configuration that is safe to record and compare.
CONFIG_KEYS: tuple[str, ...] = (
    "sample_rate",
    "vad_backend",
    "asr_backend",
    "asr_model",
    "ser_backend",
    "tts_backend",
    "llm_backend",
    "llm_model",
    "llm_ollama_base_url",
    "llm_ollama_chat_url",
    "llm_lmstudio_base_url",
    "llm_lmstudio_chat_url",
    "frame_ms",
    "silence_ms",
    "max_record_seconds",
    "tts_piper_persistent",
    "stream_natural_boundaries",
    "stream_tts_warmup",
    "stream_asr_in_memory",
    "stream_asr_partials_enabled",
    "stream_llm_reuse_connections",
    # Latency-affecting, non-sensitive settings added by the PR24 review fix.
    "asr_cpu_threads",
    "asr_beam_size",
    "asr_device",
    "asr_compute_type",
    "asr_language",
    "asr_vad_filter",
    "asr_min_silence_ms",
    "asr_condition_on_previous_text",
    "tts_voice",
    "tts_piper_model",
    "tts_piper_exe",
    "tts_piper_config",
    "tts_piper_use_json_input",
    "tts_piper_timeout_seconds",
    "tts_piper_extra_args",
    "tts_piper_espeak_data",
    "stream_sentence_max_wait_seconds",
    "stream_audio_queue_size",
    "stream_tts_queue_size",
    "stream_sentence_min_chars",
    "stream_sentence_max_chars",
    "stream_asr_partial_interval_seconds",
    "stream_barge_in_enabled",
    "min_speech_ms",
    "pre_roll_ms",
    "vad_calibration_ms",
    "vad_threshold",
    "vad_noise_multiplier",
    "vad_release_ratio",
    "llm_temperature",
    "llm_context_tokens",
    "llm_timeout_seconds",
    "llm_fallback_to_mock",
    "llm_max_tokens",
    "max_history_turns",
    "ser_model",
    "ser_device",
    "ser_language",
    "ser_fallback_to_heuristic",
    "emotion_smooth_alpha",
    "emotion_decay_half_life_seconds",
    "emotion_max_step",
)

# Export a transport-neutral name without exposing the HTTP endpoint.
CONFIG_ATTRIBUTES = {"stream_llm_reuse_connections": "stream_llm_reuse_http"}

_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SAFE_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,95}(?::[A-Za-z0-9][A-Za-z0-9._-]{0,63})?$")
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_UUID_LIKE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_HEX_ID = re.compile(r"^[0-9a-fA-F]{8,64}$")


class BaselineError(ValueError):
    """Raised for invalid metrics input, unsafe labels or unsafe output paths."""


@dataclass
class BaselineResult:
    output_path: Path
    document: dict[str, Any]
    notes: list[str] = field(default_factory=list)


def _read_json(path: Path) -> Any:
    def reject_constant(value: str) -> None:
        raise BaselineError(f"non-finite number in JSON: {value}")

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BaselineError(f"cannot read JSON: {type(exc).__name__}") from exc
    try:
        return json.loads(text, parse_constant=reject_constant)
    except json.JSONDecodeError as exc:
        raise BaselineError(f"invalid JSON: {exc.msg}") from exc


def _require_finite(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BaselineError(f"{where} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise BaselineError(f"{where} must be finite")
    return number


def _require_non_negative(value: Any, where: str) -> float:
    number = _require_finite(value, where)
    if number < 0:
        raise BaselineError(f"{where} must not be negative")
    return number


def _require_count(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BaselineError(f"{where} must be an integer")
    if value < 0:
        raise BaselineError(f"{where} must not be negative")
    return value


# --------------------------------------------------------------------------- #
# source of truth for metric names: srtp_voice.streaming.TurnTiming.snapshot
# --------------------------------------------------------------------------- #
_STREAMING_MODULE = "srtp_voice.streaming"
_TURN_TIMING_CLASS = "TurnTiming"


def known_metric_names() -> frozenset[str]:
    """Metric names recorded by ``TurnTiming.snapshot``.

    The names are read from the source of the snapshot method (without calling
    any private helper), so the set stays in sync with ``srtp_voice.streaming``
    while never echoing foreign text.
    """

    module = importlib.import_module(_STREAMING_MODULE)
    turn_timing = getattr(module, _TURN_TIMING_CLASS)
    try:
        source = inspect.getsource(turn_timing.snapshot)
    except (OSError, TypeError):
        return frozenset()
    return frozenset(re.findall(r'add\(\s*"([a-z0-9_]+)"', source))


def known_mark_names() -> frozenset[str]:
    try:
        module = importlib.import_module(_STREAMING_MODULE)
    except Exception:  # noqa: BLE001 - unknown marks are simply ignored
        return frozenset()
    mapping = getattr(module, "_TIMING_MARKS", None)
    if not isinstance(mapping, Mapping):
        return frozenset()
    return frozenset(str(value) for value in mapping.values())


def _safe_turn_id(turn_id: Any, index: int) -> tuple[str, bool]:
    if not isinstance(turn_id, str) or not turn_id:
        return f"turn-{index + 1}", True
    if _UUID_LIKE.match(turn_id) or _HEX_ID.match(turn_id):
        return turn_id, False
    if _SAFE_TOKEN.match(turn_id):
        return turn_id, False
    return f"turn-{index + 1}", True


def _sanitize_turn(
    turn: Any,
    index: int,
    metric_names: frozenset[str],
    mark_names: frozenset[str],
) -> tuple[dict[str, Any], int, int]:
    if not isinstance(turn, Mapping):
        raise BaselineError(f"turns[{index}] must be an object")
    turn_id, renamed = _safe_turn_id(turn.get("turn_id"), index)
    ignored_marks = 0
    marks: dict[str, float] = {}
    raw_marks = turn.get("marks")
    if raw_marks is not None:
        if not isinstance(raw_marks, Mapping):
            raise BaselineError(f"turns[{index}].marks must be an object")
        for key, value in raw_marks.items():
            if key not in mark_names:
                ignored_marks += 1
                continue
            marks[key] = _require_non_negative(value, f"turns[{index}].marks.{key}")
    ignored_latencies = 0
    latencies: dict[str, float] = {}
    raw_latencies = turn.get("latencies_ms")
    if raw_latencies is not None:
        if not isinstance(raw_latencies, Mapping):
            raise BaselineError(f"turns[{index}].latencies_ms must be an object")
        for key, value in raw_latencies.items():
            if key not in metric_names:
                ignored_latencies += 1
                continue
            latencies[key] = _require_non_negative(
                value, f"turns[{index}].latencies_ms.{key}"
            )
    cleaned_turn: dict[str, Any] = {
        "turn_id": turn_id,
        "marks": marks,
        "latencies_ms": latencies,
    }
    raw_diagnostics = turn.get("backend_diagnostics")
    if raw_diagnostics is not None:
        # Same whitelist as the runtime: only the seven numeric server fields
        # survive, so no model name, url, prompt or reply text can be exported.
        safe_diagnostics = sanitize_llm_diagnostics(raw_diagnostics)
        if safe_diagnostics:
            cleaned_turn["backend_diagnostics"] = safe_diagnostics
    return (
        cleaned_turn,
        ignored_marks + ignored_latencies,
        int(renamed),
    )


def _sanitize_summary(
    summary: Any,
    metric_names: frozenset[str],
) -> tuple[dict[str, dict[str, float]], int]:
    if not isinstance(summary, Mapping):
        raise BaselineError("summary must be an object")
    cleaned: dict[str, dict[str, float]] = {}
    unknown = 0
    for metric, stats in summary.items():
        if not isinstance(metric, str) or metric not in metric_names:
            unknown += 1
            continue
        if not isinstance(stats, Mapping):
            raise BaselineError("summary entries must map metric names to objects")
        entry: dict[str, float] = {}
        for key in SUMMARY_STATS:
            if key not in stats:
                continue
            if key == "count":
                entry[key] = _require_count(stats[key], f"summary.{metric}.count")
            else:
                entry[key] = _require_non_negative(stats[key], f"summary.{metric}.{key}")
        for lower, upper in (("min", "p50"), ("p50", "p95"), ("p95", "max")):
            if lower in entry and upper in entry and entry[lower] > entry[upper]:
                raise BaselineError(f"summary.{metric} {lower} exceeds {upper}")
        cleaned[metric] = entry
    return cleaned, unknown


_PATH_IDENTITY_PREFIX = "pid1-"
_ENDPOINT_IDENTITY_PREFIX = "ep1-"
_PATH_IDENTITY = re.compile(r"pid1-[0-9a-f]{64}")
_ENDPOINT_IDENTITY = re.compile(r"ep1-[0-9a-f]{64}")
_ENDPOINT_KEYS = frozenset({
    "llm_ollama_base_url", "llm_ollama_chat_url",
    "llm_lmstudio_base_url", "llm_lmstudio_chat_url",
})


def _project_relative_identifier(value: Path) -> str:
    """Deterministic identity for a project-relative model/config path.

    Two files that share a basename in different directories must not collide,
    and no plaintext path (least of all a username or share) may be exported.
    The token is versioned and recognizable, so re-loading an already encoded
    identity never hashes it twice. Project-external or unsafe paths, including
    network shares, are unknown instead of being probed.
    """

    raw = str(value)
    text = raw.replace("\\", "/")
    if not text or text.startswith("//") or (
        os.name != "nt" and ("\\" in raw or PureWindowsPath(raw).drive)
    ):
        return "<redacted>"
    # Pure lexical projection: never resolve/stat a configured path or follow a
    # symlink into a share. This identifies the setting, not the file contents.
    base = Path(PROJECT_ROOT)
    candidate = value if value.is_absolute() else base / value
    normalized = _safe_relative_location(candidate, base)
    if normalized is None:
        return "<redacted>"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"{_PATH_IDENTITY_PREFIX}{digest}"


def _endpoint_identifier(value: str) -> str:
    """Identity for an http(s) endpoint: scheme/host/port/path only.

    Userinfo, query and fragment are dropped before hashing, so credentials or
    tokens can never be exported, and the original URL never appears. Anything
    that is not a usable http(s) endpoint is unknown rather than comparable.
    """

    if _ENDPOINT_IDENTITY.fullmatch(value):
        return value
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return "<redacted>"
        # A query can change routing as well as carry credentials. Do not claim
        # equivalent configurations when its private contents are omitted.
        if parsed.query or parsed.fragment:
            return "<redacted>"
        host = parsed.hostname.lower()
        if ":" in host:
            host = f"[{ipaddress.IPv6Address(host).compressed}]"
        elif not re.fullmatch(r"[a-z0-9.-]{1,253}", host):
            return "<redacted>"
        port = parsed.port
    except ValueError:
        return "<redacted>"
    normalized = f"{parsed.scheme}://{host}"
    if port is not None:
        normalized += f":{port}"
    normalized += parsed.path or ""
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"{_ENDPOINT_IDENTITY_PREFIX}{digest}"


def _safe_config_value(value: Any, *, key: str | None = None) -> Any:
    if key in _ENDPOINT_KEYS:
        return _endpoint_identifier(value) if isinstance(value, str) else "<redacted>"
    if isinstance(value, Path):
        return _project_relative_identifier(value)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        if _PATH_IDENTITY.fullmatch(value) or _ENDPOINT_IDENTITY.fullmatch(value):
            # Already an identity token: never hash it a second time.
            return value
        if "://" in value or value.startswith(("http:", "https:")):
            return "<redacted>"
        if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", value):
            return "<redacted>"
        return value if _SAFE_MODEL.fullmatch(value) else "<redacted>"
    return "<redacted>"


_GIT_HEAD = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")
_CONFIG_FINGERPRINT = re.compile(r"[0-9a-fA-F]{64}")
# ASCII digits only, optional patch, optional standard a/b/rc pre-release:
# 3.11, 3.12.10 and 3.14.0rc1 pass, "3.12<arbitrary text>" does not.
_PYTHON_VERSION = re.compile(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:(?:a|b|rc)[0-9]+)?")
_SAFE_OS_NAMES = {"windows": "Windows", "linux": "Linux", "darwin": "Darwin"}
# Settings that demonstrably change observed latency, kept for documentation and
# the own gate; the authoritative declaration stays CONFIG_KEYS above.
_LATENCY_SETTING_KEYS: tuple[str, ...] = (
    "asr_cpu_threads",
    "asr_beam_size",
    "asr_device",
    "asr_compute_type",
    "tts_voice",
    "stream_sentence_max_wait_seconds",
    "min_speech_ms",
    "vad_threshold",
    "llm_max_tokens",
    "max_history_turns",
)
# Only these keys are legitimately absent in a real recording configuration.
_OPTIONAL_NONE_KEYS = frozenset(
    {"ser_model", "tts_piper_config", "tts_piper_extra_args", "tts_piper_espeak_data"}
)
_RECORDING_CONTEXT_KEYS = frozenset(
    {"git_head", "config_fingerprint", "config", "python", "os", "label"}
)


def _safe_recording_config_value(key: str, value: Any) -> Any:
    """Do not export free text supplied in a numeric or boolean setting."""
    default = getattr(AppConfig(), CONFIG_ATTRIBUTES.get(key, key), None)
    if isinstance(default, bool):
        if type(value) is not bool:
            return None
    elif isinstance(default, int):
        if type(value) is not int:
            return None
    elif isinstance(default, float):
        if type(value) not in (int, float):
            return None
        try:
            if not math.isfinite(value):
                return None
        except OverflowError:
            return None
    elif value is not None and not isinstance(value, (str, Path)):
        return "<redacted>"
    return _safe_config_value(value, key=key)


def _config_is_known(config_view: Mapping[str, Any]) -> bool:
    """Whether every exported value is a real, measurable setting.

    A key that is present but unknown (``None`` outside the genuinely optional
    set, a ``<redacted>`` projection, or a non-finite number) means the
    configuration cannot be treated as recorded evidence.
    """

    for key, value in config_view.items():
        if value is None:
            if key not in _OPTIONAL_NONE_KEYS:
                return False
            continue
        if value == "<redacted>":
            return False
        if isinstance(value, float) and not math.isfinite(value):
            return False
    return True


def _sanitize_recording_context(value: Any) -> dict[str, Any] | None:
    """Export-boundary cleaner for a caller-supplied recording context.

    Unknown keys are dropped instead of copied, every source string goes
    through the same validators as the file loader, and a fingerprint is only
    kept for a complete and known configuration. No path, URL or secret can
    travel through this parameter.
    """

    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise BaselineError("recording_context must be an object")
    config = value.get("config")
    config_view: dict[str, Any] = {}
    if isinstance(config, Mapping):
        config_view = {
            key: _safe_recording_config_value(key, config.get(key))
            for key in CONFIG_KEYS
            if key in config
        }
    complete = all(key in config_view for key in CONFIG_KEYS) and _config_is_known(
        config_view
    )
    provided = value.get("config_fingerprint")
    fingerprint: str | None = None
    if complete:
        expected = _fingerprint(config_view)
        if provided is None or (
            _valid_fingerprint(provided) and provided == expected
        ):
            fingerprint = expected
    cleaned: dict[str, Any] = {
        "git_head": _valid_git_head(value.get("git_head")),
        "config_fingerprint": fingerprint,
        "config": config_view,
        "python": _valid_python_version(value.get("python")),
        "os": _valid_os_name(value.get("os")),
        "label": (
            _safe_config_value(value.get("label"))
            if isinstance(value.get("label"), str)
            else None
        ),
    }
    return {key: cleaned[key] for key in cleaned if key in _RECORDING_CONTEXT_KEYS}


def _valid_git_head(value: Any) -> str | None:
    """40/64 hex only; anything else becomes unknown and is never echoed."""

    return value if isinstance(value, str) and _GIT_HEAD.fullmatch(value) else None


def _valid_fingerprint(value: Any) -> bool:
    return isinstance(value, str) and bool(_CONFIG_FINGERPRINT.fullmatch(value))


def _valid_python_version(value: Any) -> str | None:
    return value if isinstance(value, str) and _PYTHON_VERSION.fullmatch(value) else None


def _valid_os_name(value: Any) -> str | None:
    """Only the identifiers this project supports; paths and URLs are dropped."""

    if not isinstance(value, str):
        return None
    return _SAFE_OS_NAMES.get(value.strip().lower())


def _config_view(cfg: AppConfig) -> dict[str, Any]:
    return {
        key: _safe_recording_config_value(key, getattr(cfg, CONFIG_ATTRIBUTES.get(key, key), None))
        for key in CONFIG_KEYS
    }


def _fingerprint(config_view: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(dict(config_view), sort_keys=True, ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _safe_relative_location(resolved: Path, base: Path) -> str | None:
    """Project-relative location, or None when any component is unsafe.

    Pure syntax only: a POSIX-legal filename may literally contain Windows
    separators, a drive letter or a UNC prefix, and each component is checked
    so no username or share name can be exported from either platform.
    """

    try:
        relative = resolved.relative_to(base)
    except (ValueError, OSError):
        return None
    parts = relative.parts
    if not parts:
        return None
    for part in parts:
        text = str(part)
        if not text:
            return None
        if text in {".", ".."}:
            return None
        if "\\" in text or ":" in text:
            return None
        if PureWindowsPath(text).drive or PureWindowsPath(text).root:
            return None
        if PurePosixPath(text).is_absolute():
            return None
    return PurePosixPath(*parts).as_posix()


def _sha256_of_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def read_git_head(root: Path | None = None) -> dict[str, Any]:
    """Read-only ``git rev-parse HEAD``; never mutates repository state."""

    base = (Path(root) if root is not None else PROJECT_ROOT).resolve()
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(base),
            shell=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return {"head": None, "available": False}
    if completed.returncode != 0:
        return {"head": None, "available": False}
    head = completed.stdout.strip()
    return {"head": head or None, "available": bool(head)}


def read_project_version(root: Path | None = None) -> str | None:
    base = (Path(root) if root is not None else PROJECT_ROOT).resolve()
    marker = base / "srtp_voice" / "__init__.py"
    try:
        text = marker.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if "__version__" in line and "=" in line:
            return line.split("=", 1)[1].strip().strip("'\"")
    return None


def capture_context(
    *,
    root: Path | None = None,
    cfg: AppConfig | None = None,
    git: Mapping[str, Any] | None = None,
    python_version: str | None = None,
    os_name: str | None = None,
) -> dict[str, Any]:
    """Current HEAD/config/Python/OS; about the capture, not the measurement."""

    base = (Path(root) if root is not None else PROJECT_ROOT).resolve()
    if cfg is not None:
        config = cfg
    else:
        try:
            config = AppConfig.from_env()
        except Exception as exc:
            raise BaselineError("project configuration could not be loaded") from exc
    config_view = _config_view(config)
    raw_git = read_git_head(base) if git is None else git
    if not isinstance(raw_git, Mapping):
        raise BaselineError("capture git context must be an object")
    head = _valid_git_head(raw_git.get("head"))
    git_view = {"head": head, "available": raw_git.get("available") is True and head is not None}
    return {
        "git": git_view,
        "project_version": read_project_version(base),
        "python": _valid_python_version(platform.python_version() if python_version is None else python_version),
        "os": _valid_os_name(platform.system() if os_name is None else os_name),
        "config": config_view,
        "config_fingerprint": (
            _fingerprint(config_view) if _config_is_known(config_view) else None
        ),
    }


def _validate_label(label: Any) -> str:
    if not isinstance(label, str) or not _SAFE_LABEL.match(label):
        raise BaselineError(
            "label must be 1-64 characters of letters, digits, dot, dash or underscore"
        )
    return label


def capture_baseline(
    metrics_path: Path,
    *,
    measurement: str,
    label: str,
    cfg: AppConfig | None = None,
    root: Path | None = None,
    recording_context: Mapping[str, Any] | None = None,
    git: Mapping[str, Any] | None = None,
    python_version: str | None = None,
    os_name: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Build the sanitized baseline document for ``metrics_path``."""

    if measurement not in MEASUREMENT_KINDS:
        raise BaselineError(f"measurement must be one of {MEASUREMENT_KINDS}")
    safe_label = _validate_label(label)
    notes: list[str] = []
    base = (Path(root) if root is not None else PROJECT_ROOT).resolve()
    source = Path(metrics_path)
    payload = _read_json(source)
    if not isinstance(payload, Mapping):
        raise BaselineError("source metrics must be a JSON object")

    metric_names = known_metric_names()
    mark_names = known_mark_names()

    unknown_keys = [key for key in payload if key not in METRICS_KEYS]
    if unknown_keys:
        notes.append(f"ignored {len(unknown_keys)} non-whitelisted top-level keys")

    source_info: dict[str, Any] = {"schema": SOURCE_SCHEMA, "key_count": len(payload)}
    relative, escaped = None, True
    try:
        resolved = source.resolve()
        if is_within_project(resolved, root=base):
            relative = _safe_relative_location(resolved, base)
            escaped = relative is None
    except OSError:
        escaped = True
    source_info["location"] = relative if relative is not None else "<outside-project>"
    source_info["outside_project"] = escaped
    source_info["sha256"] = _sha256_of_file(source)
    if source_info["sha256"] is None:
        notes.append("source hash unavailable")

    summary, unknown_metrics = _sanitize_summary(payload.get("summary", {}), metric_names)
    if unknown_metrics:
        notes.append(f"ignored {unknown_metrics} unknown metric names")

    turns_out: list[dict[str, Any]] = []
    raw_turns = payload.get("turns")
    per_turn = True
    ignored_turn_keys = 0
    renamed_ids = 0
    if raw_turns is None:
        per_turn = False
        notes.append("per_turn_unavailable: source metrics has no 'turns' array")
    elif not isinstance(raw_turns, list):
        raise BaselineError("turns must be an array")
    elif not raw_turns:
        per_turn = False
        notes.append("per_turn_unavailable: source 'turns' array is empty")
    else:
        for index, turn in enumerate(raw_turns):
            cleaned, ignored_count, renamed = _sanitize_turn(
                turn, index, metric_names, mark_names
            )
            renamed_ids += renamed
            ignored_turn_keys += ignored_count
            cleaned["group"] = "first_observed" if index == 0 else "subsequent"
            turns_out.append(cleaned)
        if not any(turn.get("latencies_ms") for turn in turns_out):
            # Timing evidence needs at least one retained latencies_ms entry. A
            # cancelled or partial record must never be exported as a timing
            # group of zero milliseconds, and the declared group counts have to
            # stay consistent with the records, so they are not exported here.
            dropped = len(turns_out)
            turns_out = []
            per_turn = False
            notes.append(
                "per_turn_unavailable: "
                f"{dropped} retained turn record(s) carried no usable "
                "latencies_ms entry and were not exported as timing evidence"
            )

    if payload.get("last_turn") is not None:
        notes.append("last_turn present in source but not used as per-turn evidence")

    capture = capture_context(
        root=base, cfg=cfg, git=git, python_version=python_version, os_name=os_name
    )

    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "measurement": measurement,
        "label": safe_label,
        "source": source_info,
        "capture_context": capture,
        "recording_context": _sanitize_recording_context(recording_context),
        "config": capture["config"],
        "config_fingerprint": capture["config_fingerprint"],
        "summary": summary,
        "counters": {
            "late_events": _require_count(payload.get("late_events", 0), "late_events"),
            "tts_backpressure_events": (
                _require_count(payload["tts_backpressure_events"], "tts_backpressure_events")
                if "tts_backpressure_events" in payload else None
            ),
            "dropped_event_history": _require_count(
                payload.get("dropped_event_history", 0), "dropped_event_history"
            ),
            "cancelled": (
                _require_json_bool(payload["cancelled"], "cancelled")
                if "cancelled" in payload else False
            ),
            "failed": (
                _require_json_bool(payload["failed"], "failed")
                if "failed" in payload else False
            ),
        },
        "per_turn": turns_out,
        "per_turn_available": per_turn,
        "per_turn_groups": {
            "first_observed": 1 if per_turn else 0,
            "subsequent": max(0, len(turns_out) - 1) if per_turn else 0,
        },
        "per_turn_note": (
            "grouping follows recorded order only; it does not prove cold/warm start"
        ),
        "ignored_key_count": len(unknown_keys) + unknown_metrics + ignored_turn_keys,
        "renamed_turn_ids": renamed_ids,
        "notes": notes,
    }
    return document, notes


# --------------------------------------------------------------------------- #
# comparison
# --------------------------------------------------------------------------- #
def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * fraction
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def _recording_context_of(document: Mapping[str, Any]) -> Mapping[str, Any] | None:
    context = document.get("recording_context")
    if not isinstance(context, Mapping):
        return None
    required = ("git_head", "config_fingerprint", "python", "os")
    if not all(context.get(key) for key in required):
        return None
    return context


def _require_mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BaselineError(f"{where} must be a JSON object")
    return value


def _require_bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise BaselineError(f"{where} must be a boolean")
    return value


def _require_json_bool(value: Any, where: str) -> bool:
    """Strict JSON boolean: no truthiness coercion, no strings or numbers."""

    if not isinstance(value, bool):
        raise BaselineError(f"{where} must be a JSON boolean")
    return value


def _validate_comparison_document(document: Any, *, label: str) -> Mapping[str, Any]:
    """Strictly validate an untrusted baseline JSON before any computation."""

    if not isinstance(document, Mapping):
        raise BaselineError(f"{label} must be a JSON object")
    version = document.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise BaselineError(f"{label} schema_version must be an integer")
    if version != SCHEMA_VERSION:
        raise BaselineError(f"{label} schema_version is not supported")
    # Apply the same numeric/type/order rules as raw metric ingestion before
    # any float conversion can fabricate plausible deltas.
    _, unknown_metrics = _sanitize_summary(document.get("summary"), known_metric_names())
    if unknown_metrics:
        raise BaselineError(f"{label} summary contains unknown timing fields")
    _require_bool(document.get("per_turn_available"), f"{label} per_turn_available")
    turns = document.get("per_turn")
    if not isinstance(turns, list):
        raise BaselineError(f"{label} per_turn must be an array")
    groups: list[str] = []
    for index, turn in enumerate(turns):
        _require_mapping(turn, f"{label} per_turn[{index}]")
        if not isinstance(turn.get("turn_id"), str) or not turn["turn_id"]:
            raise BaselineError(f"{label} per_turn[{index}].turn_id must be a string")
        group = turn.get("group")
        if group not in {"first_observed", "subsequent"}:
            raise BaselineError(f"{label} per_turn[{index}].group is missing or unknown")
        expected = "first_observed" if index == 0 else "subsequent"
        if group != expected:
            raise BaselineError(
                f"{label} per_turn[{index}].group contradicts the recorded order"
            )
        groups.append(group)
        latency_map = _require_mapping(turn.get("latencies_ms"), f"{label} per_turn[{index}].latencies_ms")
        allowed_metrics = known_metric_names()
        for key, value in latency_map.items():
            if not isinstance(key, str):
                raise BaselineError(f"{label} per_turn[{index}] latency keys must be strings")
            if key not in allowed_metrics:
                raise BaselineError(
                    f"{label} per_turn[{index}] contains an unknown timing field"
                )
            _require_non_negative(value, f"{label} per_turn[{index}] latency value")

    available = bool(document.get("per_turn_available"))
    if available and not groups:
        raise BaselineError(f"{label} claims per-turn evidence but carries no valid record")
    if available and not any(
        isinstance(turn.get("latencies_ms"), Mapping) and turn["latencies_ms"] for turn in turns
    ):
        raise BaselineError(f"{label} claims per-turn evidence with empty latency records")
    declared_groups = document.get("per_turn_groups")
    if declared_groups is not None and groups:
        declared = _require_mapping(declared_groups, f"{label} per_turn_groups")
        for name in ("first_observed", "subsequent"):
            if name in declared:
                expected_count = groups.count(name)
                _require_count(declared[name], f"{label} per_turn_groups.{name}")
                if int(declared[name]) != expected_count:
                    raise BaselineError(
                        f"{label} per_turn_groups.{name} contradicts the per-turn records"
                    )
    elif declared_groups is not None and available:
        raise BaselineError(f"{label} per_turn_groups is present without per-turn records")

    counters = document.get("counters")
    if counters is not None:
        counters_map = _require_mapping(counters, f"{label} counters")
        for name in ("cancelled", "failed"):
            if name in counters_map:
                _require_json_bool(counters_map[name], f"{label} counters.{name}")

    measurement = document.get("measurement")
    if measurement is None:
        measurement = "unknown"
    if not isinstance(measurement, str) or measurement not in MEASUREMENT_KINDS:
        raise BaselineError(f"{label} measurement is invalid")
    context = document.get("recording_context")
    if context is not None:
        context = _require_mapping(context, f"{label} recording_context")
        for key in ("git_head", "config_fingerprint", "python", "os"):
            if context.get(key) is not None and not isinstance(context.get(key), str):
                raise BaselineError(f"{label} recording_context.{key} must be a string")
    return document


def compare_baselines(
    current: Mapping[str, Any],
    previous: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare two baselines only when measurement and recording context match.

    Both inputs are validated first; malformed values raise a safe
    ``BaselineError`` that never echoes the offending value.
    """

    try:
        _validate_comparison_document(previous, label="comparison baseline")
        _validate_comparison_document(current, label="current baseline")
    except BaselineError:
        raise
    except (TypeError, ValueError) as exc:
        raise BaselineError("comparison input shape is unsupported") from exc

    reasons: list[str] = []
    if int(current.get("schema_version", 0)) != int(previous.get("schema_version", 0)):
        reasons.append("schema version differs")
    if current.get("measurement") != previous.get("measurement"):
        reasons.append("measurement differs")
    if current.get("measurement") == "unknown":
        reasons.append("measurement is unknown")
    # Provenance comes from the real recording context only: an imported or
    # capture-time config fingerprint must never veto a comparable recording.
    # The recording config_fingerprint check below covers that case.
    if not current.get("per_turn_available") or not previous.get("per_turn_available"):
        reasons.append("per-turn evidence unavailable")

    current_context = _recording_context_of(current)
    previous_context = _recording_context_of(previous)
    if current_context is None or previous_context is None:
        reasons.append("recording metadata unavailable")
    else:
        for key in ("os", "python", "config_fingerprint"):
            if current_context.get(key) != previous_context.get(key):
                reasons.append(f"recording {key} differs")

    if reasons:
        return {
            "comparable": False,
            "reasons": reasons,
            "metrics": {},
            "per_turn": {"comparable": False, "reasons": reasons},
        }

    current_summary = current.get("summary", {})
    previous_summary = previous.get("summary", {})
    metrics: dict[str, dict[str, float]] = {}
    for name in sorted(set(current_summary) & set(previous_summary)):
        now = current_summary[name]
        before = previous_summary[name]
        entry: dict[str, float] = {}
        for stat in ("p50", "p95"):
            if stat in now and stat in before:
                entry[stat] = round(float(now[stat]) - float(before[stat]), 3)
        if entry:
            metrics[name] = entry
    # Comparability must rest on real shared deltas, not on a non-empty key
    # intersection: a shared metric with no common p50/p95 is no evidence.
    summary_comparable = bool(metrics)

    per_turn: dict[str, Any] = {"comparable": True, "groups": {}}
    group_overlap = False
    group_reasons: list[str] = []
    for group in ("first_observed", "subsequent"):
        current_group = [t for t in current.get("per_turn", []) if t.get("group") == group]
        previous_group = [t for t in previous.get("per_turn", []) if t.get("group") == group]
        if not current_group or not previous_group:
            group_reasons.append("no shared per-turn records in group " + group)
            continue
        now_values: dict[str, list[float]] = {}
        before_values: dict[str, list[float]] = {}
        for turn in current_group:
            for key, value in (turn.get("latencies_ms") or {}).items():
                now_values.setdefault(key, []).append(float(value))
        for turn in previous_group:
            for key, value in (turn.get("latencies_ms") or {}).items():
                before_values.setdefault(key, []).append(float(value))
        shared_metrics = sorted(set(now_values) & set(before_values))
        if not shared_metrics:
            group_reasons.append("no shared timing metric in group " + group)
            continue
        group_overlap = True
        latencies: dict[str, dict[str, float]] = {}
        for key in shared_metrics:
            now_sample = now_values[key]
            before_sample = before_values[key]
            latencies[key] = {
                "count": len(now_sample),
                "p50": round(_percentile(now_sample, 0.5) - _percentile(before_sample, 0.5), 3),
                "p95": round(_percentile(now_sample, 0.95) - _percentile(before_sample, 0.95), 3),
            }
        per_turn["groups"][group] = {
            "turns_current": len(current_group),
            "turns_previous": len(previous_group),
            "latency_ms": latencies,
        }

    if not group_overlap:
        per_turn["comparable"] = False
        per_turn["reasons"] = group_reasons or ["no shared per-turn evidence"]

    comparable = summary_comparable or group_overlap
    incomparable_reasons: list[str] = []
    if not summary_comparable:
        incomparable_reasons.append("no shared timing metric in summary")
    if not group_overlap:
        incomparable_reasons.append("no shared timing metric in per-turn groups")
    return {
        "comparable": comparable,
        "reasons": [] if comparable else incomparable_reasons,
        "metrics": metrics,
        "per_turn": per_turn,
    }


# --------------------------------------------------------------------------- #
# output paths and writing
# --------------------------------------------------------------------------- #
def _resolve_under(
    base: Path,
    value: str | os.PathLike[str],
    *,
    allow_absolute: bool = False,
) -> Path:
    """Resolve ``value`` inside ``base``; reject absolute inputs and escapes."""

    candidate = Path(value)
    if candidate.is_absolute() and not allow_absolute:
        raise BaselineError("absolute paths are not allowed for this argument")
    resolved = candidate.resolve() if candidate.is_absolute() else (base / candidate).resolve()
    if resolved != base and base not in resolved.parents:
        raise BaselineError("path escapes the project root")
    return resolved


def resolve_output_path(
    requested: str,
    *,
    root: Path | None = None,
    outputs_dir: Path | None = None,
) -> Path:
    """Resolve the output path, forcing it under ``outputs/`` and never overwriting."""

    base = (Path(root) if root is not None else PROJECT_ROOT).resolve()
    outputs = (Path(outputs_dir) if outputs_dir is not None else base / "outputs").resolve()
    candidate = _resolve_under(base, requested, allow_absolute=True)
    if candidate != outputs and outputs not in candidate.parents:
        raise BaselineError("baseline output must be written under outputs/")
    if candidate.exists():
        stem, suffix = candidate.stem, candidate.suffix or ".json"
        for index in range(1, 1000):
            alternative = candidate.with_name(f"{stem}-{index}{suffix}")
            if not alternative.exists():
                return alternative
        raise BaselineError("no free baseline filename available")
    return candidate


def write_baseline(document: Mapping[str, Any], path: Path) -> None:
    """Write atomically with exclusive creation to avoid overwrite races."""

    try:
        payload = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    except (ValueError, TypeError, OverflowError) as exc:
        raise BaselineError("baseline cannot be serialized as finite JSON") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "x", encoding="utf-8") as handle:
            handle.write(payload)
    except FileExistsError as exc:
        raise BaselineError("refusing to overwrite an existing baseline") from exc


# --------------------------------------------------------------------------- #
# recording metadata (real run provenance)
# --------------------------------------------------------------------------- #
def load_recording_metadata(path: Path) -> dict[str, Any]:
    """Load run provenance from a snapshot or a small metadata JSON."""

    payload = _read_json(path)
    if not isinstance(payload, Mapping):
        raise BaselineError("recording metadata must be a JSON object")
    git = payload.get("git")
    git_head = payload.get("git_head")
    if git_head is None and isinstance(git, Mapping):
        git_head = git.get("head")
    config = payload.get("config")
    if not isinstance(config, Mapping):
        config = {}
    config_view = {key: _safe_recording_config_value(key, config.get(key)) for key in CONFIG_KEYS if key in config}
    provided_fingerprint = payload.get("config_fingerprint")
    candidate = payload.get("capture_context")
    if isinstance(candidate, Mapping):
        git = candidate.get("git")
        if git_head is None and isinstance(git, Mapping):
            git_head = git.get("head")
        if not config_view and isinstance(candidate.get("config"), Mapping):
            config_view = {
                key: _safe_recording_config_value(key, candidate["config"].get(key))
                for key in CONFIG_KEYS
                if key in candidate["config"]
            }
        if not isinstance(provided_fingerprint, str) or not provided_fingerprint:
            value = candidate.get("config_fingerprint")
            if isinstance(value, str) and value:
                provided_fingerprint = value
    python = payload.get("python")
    os_name = payload.get("os")
    if isinstance(candidate, Mapping):
        python = python or candidate.get("python")
        os_name = os_name or candidate.get("os")

    # A fingerprint is only meaningful for a complete configuration whose values
    # are actually known, and it is recomputed locally: an opaque string cannot
    # bypass the completeness check, a key that is present but unknown blocks
    # the fingerprint, and a mismatching value is reported as unknown.
    fingerprint: str | None = None
    if all(key in config_view for key in CONFIG_KEYS) and _config_is_known(config_view):
        expected = _fingerprint(config_view)
        if provided_fingerprint is None:
            fingerprint = expected
        elif _valid_fingerprint(provided_fingerprint) and provided_fingerprint == expected:
            fingerprint = expected

    context: dict[str, Any] = {
        "git_head": _valid_git_head(git_head),
        "config_fingerprint": fingerprint,
        "config": config_view,
        "python": _valid_python_version(python),
        "os": _valid_os_name(os_name),
        "label": _safe_config_value(payload.get("label")) if isinstance(payload.get("label"), str) else None,
    }
    if not context["config"] and not context["config_fingerprint"]:
        raise BaselineError("recording metadata is missing config information")
    return context


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def register(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    baseline = subparsers.add_parser("baseline", help="capture a sanitized metrics baseline")
    baseline.add_argument("--metrics", required=True, help="source streaming metrics JSON")
    baseline.add_argument("--output", required=True, help="destination JSON under outputs/")
    baseline.add_argument("--compare", help="previous baseline JSON to compare against")
    baseline.add_argument(
        "--recording-metadata",
        help="project-relative JSON with real run provenance (HEAD/config/environment)",
    )
    baseline.add_argument(
        "--measurement",
        choices=list(MEASUREMENT_KINDS),
        default="unknown",
        help="how the source metrics were produced",
    )
    baseline.add_argument("--label", default="", help="short safe label for this baseline")
    baseline.set_defaults(handler=_handle_baseline)

    snapshot = subparsers.add_parser(
        "snapshot",
        help="record current HEAD/config/Python/OS before a run (capture only, no measurements)",
    )
    snapshot.add_argument("--output", required=True, help="destination JSON under outputs/")
    snapshot.add_argument("--label", default=None, help="optional safe label")
    snapshot.set_defaults(handler=_handle_snapshot)


def _revalidate_file_recording_context(document: Mapping[str, Any]) -> Mapping[str, Any]:
    """Strictly revalidate an untrusted comparison FILE's recording context.

    A copied fingerprint proves nothing on its own: the configuration must be
    complete, typed and known, and the fingerprint has to match a local
    recomputation. Any defect becomes unknown here, so a hand-written JSON file
    cannot bypass the checks the CLI applies to live captures. Trusted
    in-memory callers of :func:`compare_baselines` are unaffected because this
    runs only at the file entry point.
    """

    context = document.get("recording_context")
    if context is None:
        return document
    if not isinstance(context, Mapping):
        raise BaselineError("comparison baseline recording_context must be an object")
    patched = _sanitize_recording_context(context)
    if not _valid_fingerprint(context.get("config_fingerprint")):
        patched["config_fingerprint"] = None
    result = dict(document)
    result["recording_context"] = patched
    return result


def _handle_baseline(args: argparse.Namespace) -> int:
    try:
        metrics_path, _escaped = resolve_project_path_or_escape(args.metrics)
        recording_context = None
        if args.recording_metadata:
            metadata_path = resolve_project_path(args.recording_metadata)
            recording_context = load_recording_metadata(metadata_path)
        document, _ = capture_baseline(
            metrics_path,
            measurement=args.measurement,
            label=args.label or "baseline",
            recording_context=recording_context,
        )
        output_path = resolve_output_path(args.output)
        if args.compare:
            previous_path = resolve_project_path(args.compare)
            previous_raw = _read_json(previous_path)
            previous = _validate_comparison_document(previous_raw, label="comparison baseline")
            # Untrusted file boundary: revalidate the recorded context before
            # any delta is computed.
            previous = _revalidate_file_recording_context(previous)
            document["comparison"] = compare_baselines(document, previous)
        write_baseline(document, output_path)
    except BaselineError as exc:
        print(f"baseline error: {exc}")
        return 2
    except (ValueError, TypeError) as exc:
        print(f"baseline error: unsupported input shape ({type(exc).__name__})")
        return 2
    except PathEscapeError:
        print("baseline error: an argument path escapes the project root")
        return 2
    except OSError as exc:
        print(f"baseline error: {type(exc).__name__}")
        return 2
    print(f"baseline written: {output_path.relative_to(PROJECT_ROOT).as_posix()}")
    if not document.get("per_turn_available", False):
        print("note: per_turn_unavailable (source metrics has no usable 'turns' array)")
    if document.get("recording_context") is None:
        print("note: recording metadata unknown; comparison will be reported incomparable")
    comparison = document.get("comparison")
    if isinstance(comparison, Mapping):
        if comparison.get("comparable"):
            print("note: comparison reported as comparable")
        else:
            print("note: comparison marked incomparable")
    return 0


def _handle_snapshot(args: argparse.Namespace) -> int:
    try:
        output_path = resolve_output_path(args.output)
        label = args.label or None
        if label is not None:
            label = _validate_label(label)
        context = capture_context()
        document = {
            "schema_version": SCHEMA_VERSION,
            "kind": SNAPSHOT_KIND,
            "measurement": None,
            "status": "not_measured",
            "label": label,
            "capture_context": context,
            "config": context["config"],
            "config_fingerprint": context["config_fingerprint"],
            "note": (
                "capture-time context only: no measurements were taken; import it with "
                "baseline --recording-metadata only if it is paired with that run's results"
            ),
            "pairing_note": "must be paired with the results of the same run; never back-filled",
        }
        write_baseline(document, output_path)
    except BaselineError as exc:
        print(f"snapshot error: {exc}")
        return 2
    except OSError as exc:
        print(f"snapshot error: {type(exc).__name__}")
        return 2
    print(f"snapshot written: {output_path.relative_to(PROJECT_ROOT).as_posix()}")
    print("note: not_measured - pair this file with the SAME run's metrics later")
    return 0
