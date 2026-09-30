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

from srtp_voice.config import AppConfig

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
    "frame_ms",
    "silence_ms",
    "max_record_seconds",
    "tts_piper_persistent",
    "stream_natural_boundaries",
    "stream_tts_warmup",
    "stream_asr_in_memory",
    "stream_tts_queue_size",
    "stream_sentence_min_chars",
    "stream_sentence_max_chars",
    "stream_sentence_max_wait_seconds",
)

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
    return (
        {"turn_id": turn_id, "marks": marks, "latencies_ms": latencies},
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


def _safe_config_value(value: Any) -> Any:
    if isinstance(value, Path):
        name = value.name
        return name if _SAFE_TOKEN.match(name) else "<redacted>"
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", value):
            return "<redacted>"
        return value if _SAFE_MODEL.fullmatch(value) else "<redacted>"
    return "<redacted>"


def _config_view(cfg: AppConfig) -> dict[str, Any]:
    return {key: _safe_config_value(getattr(cfg, key, None)) for key in CONFIG_KEYS}


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
    return {
        "git": dict(git) if git is not None else read_git_head(base),
        "project_version": read_project_version(base),
        "python": python_version or platform.python_version(),
        "os": os_name or platform.system(),
        "config": config_view,
        "config_fingerprint": _fingerprint(config_view),
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
        "recording_context": dict(recording_context) if recording_context else None,
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

    payload = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
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
    config_view = {key: _safe_config_value(config.get(key)) for key in CONFIG_KEYS if key in config}
    fingerprint = payload.get("config_fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint:
        fingerprint = _fingerprint(config_view) if config_view else None
    candidate = payload.get("capture_context")
    if isinstance(candidate, Mapping):
        git = candidate.get("git")
        if git_head is None and isinstance(git, Mapping):
            git_head = git.get("head")
        if not config_view and isinstance(candidate.get("config"), Mapping):
            config_view = {
                key: _safe_config_value(candidate["config"].get(key))
                for key in CONFIG_KEYS
                if key in candidate["config"]
            }
        if not fingerprint:
            value = candidate.get("config_fingerprint")
            fingerprint = value if isinstance(value, str) and value else None
    python = payload.get("python")
    os_name = payload.get("os")
    if isinstance(candidate, Mapping):
        python = python or candidate.get("python")
        os_name = os_name or candidate.get("os")
    context: dict[str, Any] = {
        "git_head": git_head if isinstance(git_head, str) and git_head else None,
        "config_fingerprint": fingerprint,
        "config": config_view,
        "python": python if isinstance(python, str) and python else None,
        "os": os_name if isinstance(os_name, str) and os_name else None,
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
