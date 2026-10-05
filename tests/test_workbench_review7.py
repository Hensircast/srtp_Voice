"""Own gate for the five current PR24 review findings (workbench tools only).

Synthetic values only: the sentinels below are obvious fakes, never credentials
or real user paths. These tests assert the export boundary, not throughput.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

import pytest

from srtp_voice.config import AppConfig
from tools import workbench_doctor as doctor
from tools import workbench_latency as latency

FAKE_USER = "fake-private-person"
FAKE_SERVER = "fake-server"


def _metadata(tmp_path: Path, **changes) -> Path:
    config = latency._config_view(AppConfig())
    payload = {
        "git_head": "b" * 40,
        "config": config,
        "config_fingerprint": latency._fingerprint(config),
        "python": "3.12.10",
        "os": "Linux",
    }
    payload.update(changes)
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "name",
    [
        rf"models/C:\Users\{FAKE_USER}\secret.bin",
        rf"models/\\{FAKE_SERVER}\{FAKE_USER}\secret.bin",
        "models/plain.bin",
    ],
)
def test_doctor_never_echoes_embedded_foreign_components(tmp_path, monkeypatch, name):
    if name.endswith("plain.bin"):
        report = doctor.describe_path(name, tmp_path, exists=False)
        assert report["location"] == "models/plain.bin"
        return

    raw = "models/placeholder.bin"
    native = type(tmp_path)
    original = native.relative_to
    candidate = (tmp_path / raw).resolve()

    def relative(path, *args, **kwargs):
        return PurePosixPath(name) if path == candidate else original(path, *args, **kwargs)

    monkeypatch.setattr(native, "relative_to", relative)
    report = doctor.describe_path(raw, tmp_path, exists=False)

    assert report["location"] == "<outside-project>"
    dumped = json.dumps(report)
    assert FAKE_USER not in dumped and FAKE_SERVER not in dumped


@pytest.mark.parametrize("latencies", [{}, {"unknown_metric": 1}])
def test_turns_without_timings_are_not_timing_evidence(tmp_path, latencies):
    source = tmp_path / "metrics.json"
    source.write_text(
        json.dumps(
            {
                "summary": {},
                "turns": [{"turn_id": "c" * 32, "marks": {}, "latencies_ms": latencies}],
            }
        ),
        encoding="utf-8",
    )
    document, notes = latency.capture_baseline(
        source, measurement="simulated", label="empty-probe", root=tmp_path
    )

    assert document["per_turn_available"] is False
    assert document["per_turn_groups"] == {"first_observed": 0, "subsequent": 0}
    assert any("per_turn_unavailable" in note for note in notes)
    latency._validate_comparison_document(document, label="current")


def test_mixed_empty_and_valid_turns_stay_available(tmp_path):
    source = tmp_path / "metrics.json"
    source.write_text(
        json.dumps(
            {
                "summary": {},
                "turns": [
                    {"turn_id": "d" * 32, "marks": {}, "latencies_ms": {}},
                    {"turn_id": "e" * 32, "marks": {}, "latencies_ms": {"asr_final_ms": 120.0}},
                ],
            }
        ),
        encoding="utf-8",
    )
    document, _notes = latency.capture_baseline(
        source, measurement="simulated", label="mixed-probe", root=tmp_path
    )

    assert document["per_turn_available"] is True
    assert document["per_turn_groups"] == {"first_observed": 1, "subsequent": 1}
    latency._validate_comparison_document(document, label="current")


@pytest.mark.parametrize("field", ["git_head", "config_fingerprint", "python", "os"])
@pytest.mark.parametrize(
    "value",
    [
        "https://example.invalid/?token=fake-private-secret",
        f"/home/{FAKE_USER}/venv/python",
        r"C:\Users\\" + FAKE_USER + r"\python.exe",
    ],
)
def test_invalid_provenance_is_dropped_without_echo(tmp_path, field, value):
    path = _metadata(tmp_path, **{field: value})
    try:
        context = latency.load_recording_metadata(path)
    except latency.BaselineError as error:
        assert value not in str(error)
    else:
        assert value not in json.dumps(context)
        assert context.get(field) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"config": {"sample_rate": 16000}},
        {"config": {}},
        {"config": None},
        {"config": {"sample_rate": 16000}, "config_fingerprint": "f" * 64},
    ],
)
def test_incomplete_configuration_never_gets_a_usable_fingerprint(tmp_path, changes):
    path = _metadata(tmp_path, **changes)
    try:
        context = latency.load_recording_metadata(path)
    except latency.BaselineError:
        pass
    else:
        assert context.get("config_fingerprint") is None


def test_opaque_fingerprint_cannot_bypass_missing_config(tmp_path):
    path = _metadata(tmp_path, config={}, config_fingerprint="a" * 64)
    try:
        context = latency.load_recording_metadata(path)
    except latency.BaselineError:
        return
    assert context["config_fingerprint"] is None


def test_mismatching_fingerprint_for_complete_config_is_unknown(tmp_path):
    path = _metadata(tmp_path, config_fingerprint="a" * 64)
    context = latency.load_recording_metadata(path)
    assert context["config_fingerprint"] is None


def test_valid_provenance_round_trips(tmp_path):
    path = _metadata(tmp_path)
    context = latency.load_recording_metadata(path)

    assert context["git_head"] == "b" * 40
    assert context["python"] == "3.12.10"
    assert context["os"] == "Linux"
    assert context["config_fingerprint"] == latency._fingerprint(
        latency._config_view(AppConfig())
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("asr_cpu_threads", 64),
        ("asr_beam_size", 5),
        ("asr_device", "cuda"),
        ("asr_compute_type", "float16"),
        ("asr_vad_filter", False),
        ("asr_min_silence_ms", 1000),
        ("tts_voice", "zh-CN-YunxiNeural"),
        ("stream_sentence_max_wait_seconds", 5.0),
        ("min_speech_ms", 500),
        ("vad_threshold", 0.02),
        ("llm_max_tokens", 128),
        ("max_history_turns", 10),
    ],
)
def test_each_latency_setting_changes_the_fingerprint(field, value):
    original = AppConfig()
    changed = AppConfig()
    setattr(changed, field, value)
    assert latency._fingerprint(latency._config_view(original)) != latency._fingerprint(
        latency._config_view(changed)
    )


def test_config_keys_cover_the_documented_settings_without_paths():
    for key in (
        "asr_cpu_threads",
        "asr_beam_size",
        "asr_device",
        "asr_compute_type",
        "asr_vad_filter",
        "asr_min_silence_ms",
        "tts_voice",
        "stream_sentence_max_wait_seconds",
        "min_speech_ms",
        "vad_threshold",
        "llm_max_tokens",
        "max_history_turns",
    ):
        assert key in latency.CONFIG_KEYS

    view = latency._config_view(AppConfig())
    dumped = json.dumps(view)
    # No absolute path, URL or .env-like key may appear in the shared view.
    assert "C:\\" not in dumped and "/home/" not in dumped and "://" not in dumped
    for key in view:
        assert not key.endswith("_file") and not key.endswith("_key")
