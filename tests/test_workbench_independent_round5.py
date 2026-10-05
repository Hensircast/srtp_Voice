"""Independent acceptance for shared timing evidence and required versions.

These are synthetic inputs, not voice or model-performance measurements.
"""
from __future__ import annotations

import json
import builtins
import importlib.util

import pytest

from srtp_voice.config import AppConfig
from tools import workbench_doctor as doctor, workbench_latency as latency


def baseline(tmp_path, turns, summary=None):
    source = tmp_path / "synthetic-metrics.json"
    source.write_text(json.dumps({
        "summary": summary or {},
        "turns": [{"turn_id": f"turn-{index}", "marks": {}, "latencies_ms": values}
                  for index, values in enumerate(turns, 1)],
    }), encoding="utf-8")
    document, _ = latency.capture_baseline(
        source, measurement="real", label="synthetic-probe", cfg=AppConfig(), root=tmp_path,
    )
    document["recording_context"] = {
        "git_head": "a" * 40, "config_fingerprint": document["config_fingerprint"],
        "python": "3.11", "os": "synthetic-test-platform",
    }
    return document


def summary(value):
    return {"count": 1, "min": value, "p50": value, "p95": value, "max": value}


@pytest.mark.parametrize("with_summary", [False, True])
def test_disjoint_metric_sets_are_not_comparable(tmp_path, with_summary):
    now = baseline(tmp_path, [{"asr_final_ms": 20}],
                   {"asr_final_ms": summary(20)} if with_summary else None)
    before = baseline(tmp_path, [{"tts_first_chunk_ms": 10}],
                      {"tts_first_chunk_ms": summary(10)} if with_summary else None)
    result = latency.compare_baselines(now, before)
    assert result["comparable"] is False
    assert result["per_turn"]["comparable"] is False
    assert result["metrics"] == {}
    assert result["reasons"]


def test_shared_per_turn_evidence_without_summary_remains_comparable(tmp_path):
    now = baseline(tmp_path, [{"asr_final_ms": 20}])
    before = baseline(tmp_path, [{"asr_final_ms": 10}])
    result = latency.compare_baselines(now, before)
    assert result["comparable"] is True
    assert result["per_turn"]["comparable"] is True
    assert result["metrics"] == {}
    assert result["per_turn"]["groups"]["first_observed"]["latency_ms"]["asr_final_ms"]["p50"] == 10


def test_partial_metric_overlap_emits_only_shared_deltas(tmp_path):
    now = baseline(tmp_path, [{"asr_final_ms": 20, "tts_first_chunk_ms": 5}])
    before = baseline(tmp_path, [{"asr_final_ms": 10, "llm_first_token_ms": 3}])
    result = latency.compare_baselines(now, before)
    assert result["comparable"] is True
    delta = result["per_turn"]["groups"]["first_observed"]["latency_ms"]
    assert set(delta) == {"asr_final_ms"}
    assert delta["asr_final_ms"]["p50"] == 10


def test_summary_overlap_does_not_fabricate_group_comparison(tmp_path):
    common = {"asr_final_ms": summary(20), "tts_first_chunk_ms": summary(20)}
    now = baseline(tmp_path, [{"asr_final_ms": 20}, {"tts_first_chunk_ms": 20}], common)
    before = baseline(tmp_path, [{"tts_first_chunk_ms": 20}, {"asr_final_ms": 20}], common)
    result = latency.compare_baselines(now, before)
    assert result["comparable"] is True
    assert set(result["metrics"]) == set(common)
    assert result["per_turn"]["comparable"] is False


def test_shared_summary_names_without_shared_timing_stats_are_incomparable(tmp_path):
    count_only = {"asr_final_ms": {"count": 1}}
    now = baseline(tmp_path, [{"asr_final_ms": 20}], count_only)
    before = baseline(tmp_path, [{"tts_first_chunk_ms": 10}], count_only)
    result = latency.compare_baselines(now, before)
    assert result["metrics"] == {}
    assert result["per_turn"]["comparable"] is False
    assert result["comparable"] is False


@pytest.mark.parametrize(("installed", "expected"), [
    ("2.30.0", doctor.ERROR),
    ("2.31.0rc1", doctor.ERROR),
    ("2.31.0", doctor.OK),
    ("2.31", doctor.OK),
    ("2.31.0.post1", doctor.OK),
    ("2.31.0+local", doctor.OK),
    ("2.32.0", doctor.OK),
    (None, doctor.ERROR),
])
def test_declared_requests_constraint_is_enforced(monkeypatch, installed, expected):
    monkeypatch.setattr(doctor, "_distribution_version", lambda _: installed)
    result = doctor.dependency_report((("requests", "requests"),), base=True,
                                      spec_finder=lambda _: object())[0]
    assert result["present"] is True
    assert result["status"] == expected
    rendered = doctor._format_dependency(result)
    if expected == doctor.ERROR:
        assert "[OK]" not in rendered
        assert result["detail"]
    else:
        assert "[OK]" in rendered


def test_unknown_required_version_cannot_be_reported_healthy(monkeypatch):
    private_value = "invalid-version-CONTAINS-PRIVATE-PATH"
    monkeypatch.setattr(doctor, "_distribution_version", lambda _: private_value)
    result = doctor.dependency_report((("requests", "requests"),), base=True,
                                      spec_finder=lambda _: object())[0]
    assert result["status"] == doctor.ERROR
    assert private_value not in json.dumps(result)
    assert "[OK]" not in doctor._format_dependency(result)


def test_optional_severity_does_not_turn_version_failure_into_ok(monkeypatch):
    monkeypatch.setattr(doctor, "_distribution_version", lambda _: "2.30.0")
    result = doctor.dependency_report((("requests", "requests"),), base=False,
                                      spec_finder=lambda _: object())[0]
    assert result["status"] == doctor.WARNING
    assert "[OK]" not in doctor._format_dependency(result)


@pytest.mark.parametrize(("distribution", "older"), [
    ("requests", "2.30.0"), ("soundfile", "0.12.0"), ("sounddevice", "0.4.5"),
    ("python-dotenv", "0.9.9"), ("pyserial", "3.4"), ("edge-tts", "6.1.9"),
    ("pytest", "8.4.2"),
])
def test_every_current_base_minimum_is_checked(monkeypatch, distribution, older):
    monkeypatch.setattr(doctor, "_distribution_version", lambda _: older)
    result = doctor.dependency_report(((distribution, "synthetic_module"),), base=True,
                                      spec_finder=lambda _: object())[0]
    assert result["status"] == doctor.ERROR


def test_valid_but_old_version_is_retained_as_safe_diagnostic_evidence(monkeypatch):
    monkeypatch.setattr(doctor, "_distribution_version", lambda _: "2.30.0")
    result = doctor.dependency_report((("requests", "requests"),), base=True,
                                      spec_finder=lambda _: object())[0]
    assert result["status"] == doctor.ERROR
    assert result["version"] == "2.30.0"


def test_undeclared_optional_runtime_can_still_be_reported_present(monkeypatch):
    monkeypatch.setattr(doctor, "_distribution_version", lambda _: "2.8.0")
    result = doctor.dependency_report((("torch", "torch"),), base=False,
                                      spec_finder=lambda _: object())[0]
    assert result["present"] is True
    assert result["version"] == "2.8.0"
    assert result["status"] == doctor.OK


def test_duplicate_requirement_lines_are_intersected(tmp_path, monkeypatch):
    (tmp_path / "requirements.txt").write_text("requests>=2.31.0\nrequests<3.0\n", encoding="utf-8")
    monkeypatch.setattr(doctor, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(doctor, "_distribution_version", lambda _: "2.30.0")
    result = doctor.dependency_report((("requests", "requests"),), base=True,
                                      spec_finder=lambda _: object())[0]
    assert result["status"] == doctor.ERROR


def test_unreadable_requirement_encoding_is_safe_not_a_crash(tmp_path, monkeypatch):
    (tmp_path / "requirements.txt").write_bytes(b"requests>=2.31.0\n\xff")
    monkeypatch.setattr(doctor, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(doctor, "_distribution_version", lambda _: "2.32.0")
    result = doctor.dependency_report((("requests", "requests"),), base=True,
                                      spec_finder=lambda _: object())[0]
    assert result["status"] == doctor.ERROR
    assert "[OK]" not in doctor._format_dependency(result)


@pytest.mark.parametrize("installed", [None, "PRIVATE-INVALID-VERSION"])
def test_unknown_optional_version_is_warning_and_never_echoed(monkeypatch, installed):
    monkeypatch.setattr(doctor, "_distribution_version", lambda _: installed)
    result = doctor.dependency_report((("torch", "torch"),), base=False,
                                      spec_finder=lambda _: object())[0]
    assert result["status"] == doctor.WARNING
    assert result["version"] is None
    assert "PRIVATE-INVALID-VERSION" not in json.dumps(result)


def test_invalid_requirement_cannot_silently_weaken_a_valid_line(tmp_path, monkeypatch):
    (tmp_path / "requirements.txt").write_text("requests>=2.31.0\nrequests<INVALID\n", encoding="utf-8")
    monkeypatch.setattr(doctor, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(doctor, "_distribution_version", lambda _: "2.32.0")
    result = doctor.dependency_report((("requests", "requests"),), base=True,
                                      spec_finder=lambda _: object())[0]
    assert result["status"] == doctor.ERROR


@pytest.mark.parametrize("unreadable", [False, True])
def test_unknown_declarations_are_not_confused_with_no_optional_constraint(tmp_path, monkeypatch, unreadable):
    if unreadable:
        (tmp_path / "requirements.txt").write_bytes(b"\xff")
    monkeypatch.setattr(doctor, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(doctor, "_distribution_version", lambda _: "2.8.0")
    result = doctor.dependency_report((("torch", "torch"),), base=False,
                                      spec_finder=lambda _: object())[0]
    assert result["status"] == doctor.WARNING


def test_missing_version_parser_is_reported_without_crashing_import(monkeypatch):
    original_import = builtins.__import__

    def no_packaging(name, *args, **kwargs):
        if name == "packaging" or name.startswith("packaging."):
            raise ModuleNotFoundError("PRIVATE-PARSER-ERROR")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_packaging)
    spec = importlib.util.spec_from_file_location("tools._doctor_without_packaging_probe", doctor.__file__)
    assert spec is not None and spec.loader is not None
    isolated = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolated)
    monkeypatch.setattr(isolated, "_distribution_version", lambda _: "2.32.0")
    result = isolated.dependency_report((("requests", "requests"),), base=True,
                                        spec_finder=lambda _: object())[0]
    assert result["status"] == doctor.ERROR
    assert result["detail"]
    assert "PRIVATE-PARSER-ERROR" not in json.dumps(result)
