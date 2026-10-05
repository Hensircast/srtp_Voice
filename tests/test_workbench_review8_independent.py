"""Independent untrusted-file, capture and identity probes for the new review."""
import argparse
import copy
import json
from pathlib import Path

import pytest

from srtp_voice.config import AppConfig
from tools import workbench_latency as latency


def _document():
    config = latency._config_view(AppConfig())
    return {
        "schema_version": latency.SCHEMA_VERSION, "measurement": "real",
        "summary": {"turn_total_ms": {"count": 1, "min": 10, "p50": 10, "p95": 10, "max": 10}},
        "per_turn_available": True,
        "per_turn": [{"turn_id": "turn-1", "group": "first_observed", "latencies_ms": {"turn_total_ms": 10}}],
        "recording_context": {"git_head": "a" * 40, "python": "3.12.10", "os": "Windows",
                              "config": config, "config_fingerprint": latency._fingerprint(config)},
    }


@pytest.mark.parametrize("mutation", ["empty", "different", "missing"])
def test_cli_copied_fingerprint_does_not_prove_recording_config(tmp_path, monkeypatch, mutation):
    current = _document()
    previous = copy.deepcopy(current)
    monkeypatch.setattr(latency, "PROJECT_ROOT", tmp_path)
    context = previous["recording_context"]
    if mutation == "empty":
        context["config"] = {}
    elif mutation == "missing":
        context.pop("config")
    else:
        context["config"]["asr_cpu_threads"] = 64
    written = []
    monkeypatch.setattr(latency, "resolve_project_path_or_escape", lambda value: (tmp_path / value, False))
    monkeypatch.setattr(latency, "resolve_project_path", lambda value: tmp_path / value)
    monkeypatch.setattr(latency, "capture_baseline", lambda *a, **kw: (copy.deepcopy(current), []))
    monkeypatch.setattr(latency, "_read_json", lambda *a: previous)
    monkeypatch.setattr(latency, "resolve_output_path", lambda value: tmp_path / "result.json")
    monkeypatch.setattr(latency, "write_baseline", lambda doc, path: written.append(doc))
    args = argparse.Namespace(metrics="metrics.json", output="result.json", compare="previous.json",
                              recording_metadata=None, measurement="real", label="probe")
    result = latency._handle_baseline(args)
    assert result in (0, 2)
    if result == 0:
        assert written and written[0]["comparison"]["comparable"] is False
        assert written[0]["comparison"]["metrics"] == {}
    else:
        assert not written


def test_capture_overrides_cannot_export_paths_or_extra_git_secrets():
    context = latency.capture_context(cfg=AppConfig(), git={"head": "a" * 40, "available": True,
        "token": "fake-private-secret"}, python_version="/home/fake-private-person/venv/python",
        os_name="fake-private-secret")
    encoded = json.dumps(context, allow_nan=False)
    assert "fake-private" not in encoded
    assert set(context["git"]) <= {"head", "available"}
    assert context["git"]["head"] == "a" * 40


@pytest.mark.parametrize("field", ["tts_piper_model", "tts_piper_exe"])
def test_distinct_project_paths_with_same_basename_have_distinct_identity(field):
    before = AppConfig()
    after = AppConfig()
    setattr(before, field, Path("models/one/model.onnx"))
    setattr(after, field, Path("models/two/model.onnx"))
    left, right = latency._config_view(before), latency._config_view(after)
    assert latency._fingerprint(left) != latency._fingerprint(right)
    assert "models/one" not in json.dumps(left)


@pytest.mark.parametrize("field", ["llm_ollama_base_url", "llm_ollama_chat_url", "llm_lmstudio_base_url", "llm_lmstudio_chat_url"])
def test_endpoints_have_distinct_safe_identities(field):
    before = AppConfig()
    after = AppConfig()
    setattr(before, field, "http://fake-user:fake-private-secret@127.0.0.1:11434/api/chat")
    setattr(after, field, "http://fake-user:fake-private-secret@127.0.0.1:11435/api/chat")
    left, right = latency._config_view(before), latency._config_view(after)
    assert latency._fingerprint(left) != latency._fingerprint(right)
    assert "fake-private" not in json.dumps(left)
    assert "http://" not in json.dumps(left)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_live_capture_never_serializes_nonfinite_config(value):
    context = latency.capture_context(cfg=AppConfig(llm_temperature=value), git={"head": "a" * 40, "available": True})
    json.dumps(context, allow_nan=False)
    assert context["config_fingerprint"] is None
