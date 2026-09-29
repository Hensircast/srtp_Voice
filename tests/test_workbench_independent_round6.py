"""Synthetic acceptance: use recorded provenance and redact foreign path syntax."""
from __future__ import annotations

import copy
import json
import os
from pathlib import PurePosixPath

import pytest

from srtp_voice.config import AppConfig
from tools import workbench_latency as latency


def baseline(tmp_path, *, model="import:one", value=10):
    source = tmp_path / "metrics.json"
    source.write_text(json.dumps({"summary": {}, "turns": [{
        "turn_id": "synthetic-turn", "marks": {}, "latencies_ms": {"asr_final_ms": value},
    }]}), encoding="utf-8")
    document, _ = latency.capture_baseline(
        source, measurement="real", label="synthetic-probe", cfg=AppConfig(llm_model=model), root=tmp_path,
    )
    document["recording_context"] = {
        "git_head": "a" * 40, "config_fingerprint": "actual-recording-config",
        "python": "3.11", "os": "synthetic-platform",
    }
    return document


def test_recording_context_wins_over_different_import_config(tmp_path):
    now = baseline(tmp_path, model="import:changed", value=20)
    before = baseline(tmp_path, model="import:original", value=10)
    assert now["config_fingerprint"] != before["config_fingerprint"]
    assert now["recording_context"] == before["recording_context"]
    result = latency.compare_baselines(now, before)
    assert result["comparable"] is True
    assert result["per_turn"]["groups"]["first_observed"]["latency_ms"]["asr_final_ms"]["p50"] == 10


def test_equal_import_config_never_overrides_changed_recording_config(tmp_path):
    now = baseline(tmp_path)
    before = copy.deepcopy(now)
    now["recording_context"]["config_fingerprint"] = "different-recording-config"
    result = latency.compare_baselines(now, before)
    assert result["comparable"] is False
    assert result["metrics"] == {}


def test_missing_recording_config_cannot_be_replaced_by_import_config(tmp_path):
    now = baseline(tmp_path)
    before = copy.deepcopy(now)
    now["recording_context"]["config_fingerprint"] = None
    assert latency.compare_baselines(now, before)["comparable"] is False


@pytest.mark.parametrize("relative_name", [
    r"C:\Users\private-person\metrics.json",
    r"\\private-server\private-person\metrics.json",
    r"outputs/C:\Users\private-person\metrics.json",
])
def test_literal_foreign_path_names_are_never_exported(tmp_path, monkeypatch, relative_name):
    if os.name == "nt":
        # Windows cannot create a literal colon/backslash filename. Exercise
        # the export boundary with its POSIX-relative representation instead;
        # Ubuntu CI creates the actual literal file below (no test is skipped).
        source = tmp_path / "safe-metrics.json"
        original = type(source).relative_to
        resolved_source = source.resolve()

        def posix_relative(path, *args, **kwargs):
            if path == resolved_source:
                return PurePosixPath(relative_name)
            return original(path, *args, **kwargs)

        monkeypatch.setattr(type(source), "relative_to", posix_relative)
    else:
        source = tmp_path / relative_name
        source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('{"summary": {}}', encoding="utf-8")
    document, _ = latency.capture_baseline(source, measurement="simulated", label="path-probe", root=tmp_path)
    assert document["source"]["sha256"]
    assert "private-person" not in json.dumps(document)
    assert "private-server" not in json.dumps(document)
    assert document["source"]["location"] == "<outside-project>"
    assert document["source"]["outside_project"] is True


def test_normal_project_relative_source_remains_visible(tmp_path):
    document = baseline(tmp_path)
    assert document["source"]["location"] == "metrics.json"
    assert document["source"]["outside_project"] is False
