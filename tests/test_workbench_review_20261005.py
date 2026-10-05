"""Independent synthetic gates for the five current PR24 review findings."""
from __future__ import annotations

import json
import os
from pathlib import PurePosixPath

import pytest

from srtp_voice.config import AppConfig
from tools import workbench_doctor as doctor
from tools import workbench_latency as latency


def _metadata(tmp_path, **changes):
    config = latency._config_view(AppConfig())
    payload = {"git_head": "a" * 40, "config": config,
               "config_fingerprint": latency._fingerprint(config),
               "python": "3.12.10", "os": "Windows"}
    payload.update(changes)
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.parametrize("name", [r"models/C:\Users\fake-private-person\secret.bin",
                                  r"models/\\fake-server\fake-private-person\secret.bin"])
def test_doctor_redacts_embedded_foreign_path_components(tmp_path, monkeypatch, name):
    if os.name == "nt":
        raw = "models/placeholder.bin"
        native = type(tmp_path)
        original = native.relative_to
        candidate = (tmp_path / raw).resolve()

        def relative(path, *args, **kwargs):
            return PurePosixPath(name) if path == candidate else original(path, *args, **kwargs)

        monkeypatch.setattr(native, "relative_to", relative)
    else:
        raw = name
    report = doctor.describe_path(raw, tmp_path, exists=False)
    assert "fake-private-person" not in json.dumps(report)
    assert "fake-server" not in json.dumps(report)
    assert report["location"] == "<outside-project>"


@pytest.mark.parametrize("latencies", [{}, {"unknown_metric": 1}])
def test_empty_sanitized_turns_are_not_claimed_as_timing_evidence(tmp_path, latencies):
    source = tmp_path / "metrics.json"
    source.write_text(json.dumps({"summary": {}, "turns": [{
        "turn_id": "a" * 32, "marks": {}, "latencies_ms": latencies,
    }]}), encoding="utf-8")
    document, notes = latency.capture_baseline(source, measurement="simulated",
                                             label="empty-probe", root=tmp_path)
    assert document["per_turn_available"] is False
    assert document["per_turn_groups"] == {"first_observed": 0, "subsequent": 0}
    assert any("per_turn_unavailable" in note for note in notes)
    latency._validate_comparison_document(document, label="current")


@pytest.mark.parametrize("field", ["git_head", "config_fingerprint", "python", "os"])
@pytest.mark.parametrize("value", ["https://example.invalid/?token=fake-private-secret",
                                  "/home/fake-private-person/venv/python"])
def test_invalid_provenance_is_rejected_or_redacted_without_echo(tmp_path, field, value):
    path = _metadata(tmp_path, **{field: value})
    try:
        context = latency.load_recording_metadata(path)
    except latency.BaselineError as error:
        assert value not in str(error)
    else:
        assert value not in json.dumps(context)
        assert context.get(field) is None


@pytest.mark.parametrize("changes", [{"config": {"sample_rate": 16000}},
                                    {"config": {}}, {"config": None}])
def test_incomplete_configuration_cannot_receive_usable_fingerprint(tmp_path, changes):
    path = _metadata(tmp_path, **changes)
    try:
        context = latency.load_recording_metadata(path)
    except latency.BaselineError:
        pass
    else:
        assert context.get("config_fingerprint") is None


@pytest.mark.parametrize("field,value", [
    ("asr_cpu_threads", 64), ("asr_beam_size", 5), ("asr_device", "cuda"),
    ("asr_compute_type", "float16"), ("asr_vad_filter", False),
    ("asr_min_silence_ms", 1000), ("tts_voice", "zh-CN-YunxiNeural"),
    ("stream_sentence_max_wait_seconds", 5.0), ("min_speech_ms", 500),
    ("vad_threshold", 0.02), ("llm_max_tokens", 128), ("max_history_turns", 10),
])
def test_latency_affecting_settings_change_capture_fingerprint(field, value):
    original = AppConfig()
    changed = AppConfig()
    setattr(changed, field, value)
    assert latency._fingerprint(latency._config_view(original)) != latency._fingerprint(latency._config_view(changed))


def test_valid_full_snapshot_keeps_canonical_provenance(tmp_path):
    context = latency.load_recording_metadata(_metadata(tmp_path))
    assert context["git_head"] == "a" * 40
    assert context["python"] == "3.12.10"
    assert context["os"] == "Windows"
    assert context["config_fingerprint"] == latency._fingerprint(latency._config_view(AppConfig()))
