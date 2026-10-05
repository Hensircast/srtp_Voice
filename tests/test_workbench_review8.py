"""Own gate for the PR24 round-8 review items (workbench tools only).

Synthetic sentinels only; no credentials, real paths or network access.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import pytest

from srtp_voice.config import AppConfig
from tools import workbench_latency as latency

FAKE = "fake-private-secret"


def _document() -> dict:
    config = latency._config_view(AppConfig())
    return {
        "schema_version": latency.SCHEMA_VERSION,
        "measurement": "real",
        "summary": {"turn_total_ms": {"count": 1, "min": 10, "p50": 10, "p95": 10, "max": 10}},
        "per_turn_available": True,
        "per_turn": [
            {"turn_id": "turn-1", "group": "first_observed", "latencies_ms": {"turn_total_ms": 10}}
        ],
        "recording_context": {
            "git_head": "a" * 40,
            "python": "3.12.10",
            "os": "Windows",
            "config": config,
            "config_fingerprint": latency._fingerprint(config),
        },
    }


@pytest.mark.parametrize("mutation", ["empty", "missing", "different"])
def test_cli_file_context_is_revalidated_before_any_delta(tmp_path, monkeypatch, mutation):
    current = _document()
    previous = copy.deepcopy(current)
    context = previous["recording_context"]
    if mutation == "empty":
        context["config"] = {}
    elif mutation == "missing":
        context.pop("config")
    else:
        context["config"]["asr_cpu_threads"] = 64
    written: list[dict] = []
    monkeypatch.setattr(
        latency, "resolve_project_path_or_escape", lambda value: (tmp_path / value, False)
    )
    monkeypatch.setattr(latency, "resolve_project_path", lambda value: tmp_path / value)
    monkeypatch.setattr(latency, "capture_baseline", lambda *a, **kw: (copy.deepcopy(current), []))
    monkeypatch.setattr(latency, "_read_json", lambda *a: previous)
    monkeypatch.setattr(latency, "resolve_output_path", lambda value: tmp_path / "result.json")
    monkeypatch.setattr(latency, "write_baseline", lambda doc, path: written.append(doc))

    result = latency._handle_baseline(
        argparse.Namespace(
            metrics="metrics.json",
            output="result.json",
            compare="previous.json",
            recording_metadata=None,
            measurement="real",
            label="probe",
        )
    )

    assert result in (0, 2)
    if result == 0:
        assert written and written[0]["comparison"]["comparable"] is False
        assert written[0]["comparison"]["metrics"] == {}
    else:
        assert not written


def test_revalidation_helper_marks_defects_unknown():
    changed = _document()
    changed["recording_context"]["config"]["asr_cpu_threads"] = 64
    patched = latency._revalidate_file_recording_context(changed)
    assert patched["recording_context"]["config_fingerprint"] is None

    valid = latency._revalidate_file_recording_context(_document())
    assert valid["recording_context"]["config_fingerprint"] == latency._fingerprint(
        latency._config_view(AppConfig())
    )

    with pytest.raises(latency.BaselineError):
        latency._revalidate_file_recording_context({"recording_context": ["nope"]})


def test_capture_overrides_are_whitelisted_and_never_echoed():
    context = latency.capture_context(
        cfg=AppConfig(),
        git={"head": "a" * 40, "available": True, "token": FAKE},
        python_version=f"/home/{FAKE}/venv/python",
        os_name=FAKE,
    )
    encoded = json.dumps(context, allow_nan=False)
    assert FAKE not in encoded
    assert set(context["git"]) == {"head", "available"}
    assert context["git"]["head"] == "a" * 40
    assert context["git"]["available"] is True
    assert context["python"] is None and context["os"] is None


def test_project_path_identity_is_stable_and_distinct():
    left = latency._config_view(AppConfig(tts_piper_model=Path("models/one/model.onnx")))
    right = latency._config_view(AppConfig(tts_piper_model=Path("models/two/model.onnx")))
    assert left["tts_piper_model"] != right["tts_piper_model"]
    assert left["tts_piper_model"].startswith("pid1-")
    assert "models/one" not in json.dumps(left)
    # Re-loading an encoded identity must not hash it a second time.
    assert latency._safe_config_value(left["tts_piper_model"]) == left["tts_piper_model"]
    assert latency._config_view(AppConfig(tts_piper_model=Path("models/one/model.onnx")))[
        "tts_piper_model"
    ] == left["tts_piper_model"]


def test_foreign_or_unsafe_paths_are_unknown():
    assert latency._safe_config_value(Path("/etc/passwd")) == "<redacted>"
    assert latency._safe_config_value(Path("..") / "outside.bin") == "<redacted>"


@pytest.mark.parametrize(
    "field",
    ["llm_ollama_base_url", "llm_ollama_chat_url", "llm_lmstudio_base_url", "llm_lmstudio_chat_url"],
)
def test_endpoint_identity_ignores_credentials_and_query(field):
    with_secret = latency._config_view(
        AppConfig(**{field: f"http://user:{FAKE}@127.0.0.1:11434/api/chat"})
    )
    without_secret = latency._config_view(
        AppConfig(**{field: "http://127.0.0.1:11434/api/chat"})
    )
    dumped = json.dumps(with_secret)
    assert FAKE not in dumped and "http://" not in dumped
    assert with_secret[field].startswith("ep1-")
    # Same endpoint identity despite the credentials, and different ports differ.
    assert with_secret[field] == without_secret[field]
    other_port = latency._config_view(AppConfig(**{field: "http://127.0.0.1:11435/api/chat"}))
    assert other_port[field] != with_secret[field]
    with_query = latency._config_view(AppConfig(**{field: f"http://127.0.0.1:11434/api/chat?token={FAKE}"}))
    assert with_query[field] == "<redacted>"


@pytest.mark.parametrize("value", ["ftp://example.invalid/x", "http://", "not a url://x"])
def test_invalid_endpoints_are_unknown(value):
    assert latency._safe_config_value(value) == "<redacted>"


def test_non_url_scalars_are_not_treated_as_endpoints():
    # A plain finite scalar is a legitimate setting, not a rejected endpoint.
    assert latency._safe_config_value(42) == 42
    assert latency._safe_config_value("small") == "small"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_settings_are_unknown_and_json_safe(value):
    context = latency.capture_context(
        cfg=AppConfig(llm_temperature=value), git={"head": "a" * 40, "available": True}
    )
    json.dumps(context, allow_nan=False)
    assert context["config"]["llm_temperature"] is None
    assert context["config_fingerprint"] is None
    assert latency._config_is_known({"llm_temperature": None}) is False
