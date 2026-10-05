"""Own gate for the PR24 review follow-up items (workbench tools only).

Synthetic sentinels only, never credentials or real user paths.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from srtp_voice.config import AppConfig
from tools import workbench_latency as latency

FAKE_SUFFIX = "fake-private-secret"


def _full_config(**changes) -> dict:
    config = latency._config_view(AppConfig())
    config.update(changes)
    return config


def _metadata(tmp_path: Path, **changes) -> Path:
    config = _full_config()
    payload = {
        "git_head": "b" * 40,
        "config": config,
        "config_fingerprint": latency._fingerprint(config),
        "python": "3.12.10",
        "os": "Windows",
    }
    payload.update(changes)
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_config_keys_are_declared_once_and_cover_pipeline_settings():
    for key in (
        "pre_roll_ms",
        "vad_calibration_ms",
        "vad_noise_multiplier",
        "vad_release_ratio",
        "asr_language",
        "asr_condition_on_previous_text",
        "llm_temperature",
        "llm_context_tokens",
        "llm_timeout_seconds",
        "llm_fallback_to_mock",
        "ser_model",
        "ser_device",
        "ser_language",
        "ser_fallback_to_heuristic",
        "stream_audio_queue_size",
        "stream_asr_partial_interval_seconds",
        "stream_barge_in_enabled",
        "tts_piper_model",
        "tts_piper_config",
        "tts_piper_use_json_input",
        "tts_piper_timeout_seconds",
        "tts_piper_extra_args",
        "tts_piper_espeak_data",
        "emotion_smooth_alpha",
        "emotion_decay_half_life_seconds",
        "emotion_max_step",
    ):
        assert key in latency.CONFIG_KEYS
    # No duplicate declaration and no key invented for a field that this tree
    # does not have.
    assert len(latency.CONFIG_KEYS) == len(set(latency.CONFIG_KEYS))
    fields = set(AppConfig.__dataclass_fields__)
    for key in latency.CONFIG_KEYS:
        assert latency.CONFIG_ATTRIBUTES.get(key, key) in fields, key
    if "stream_natural_boundaries" in fields:
        assert "stream_natural_boundaries" in latency.CONFIG_KEYS
    else:
        assert "stream_natural_boundaries" not in latency.CONFIG_KEYS


@pytest.mark.parametrize("value", ["3.11", "3.12.10", "3.14.0rc1"])
def test_python_version_accepts_standard_versions(value):
    assert latency._valid_python_version(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "3.12" + FAKE_SUFFIX,
        "3.12.10." + FAKE_SUFFIX,
        "3.12.10\n",
        "https://example.invalid/3.12",
        "/home/" + FAKE_SUFFIX + "/python",
        "v3.12",
        "3",
        "",
    ],
)
def test_python_version_rejects_anything_else(value):
    assert latency._valid_python_version(value) is None
    assert FAKE_SUFFIX not in json.dumps({"python": latency._valid_python_version(value)})


def test_source_patterns_use_fullmatch():
    assert latency._valid_git_head("c" * 40) == "c" * 40
    assert latency._valid_git_head("c" * 64) == "c" * 64
    assert latency._valid_git_head("c" * 40 + "\n") is None
    assert latency._valid_git_head("c" * 41) is None
    assert latency._valid_fingerprint("d" * 64) is True
    assert latency._valid_fingerprint("d" * 64 + "\n") is False
    assert latency._valid_fingerprint("d" * 63) is False


def test_complete_but_unknown_configuration_cannot_be_measured(tmp_path):
    # A present key with an unknown value blocks the fingerprint.
    path = _metadata(tmp_path, config=_full_config(asr_cpu_threads=None))
    context = latency.load_recording_metadata(path)
    assert context["config_fingerprint"] is None

    # A projected URL/secret becomes <redacted>, which is equally unusable.
    secret_path = r"C:\Users\\" + FAKE_SUFFIX + r"\model.bin"
    path = _metadata(tmp_path, config=_full_config(asr_model=secret_path))
    context = latency.load_recording_metadata(path)
    assert FAKE_SUFFIX not in json.dumps(context)
    assert context["config"]["asr_model"] == "<redacted>"
    assert context["config_fingerprint"] is None

    # Non-finite numbers cannot be exported as a measured configuration.
    assert latency._config_is_known({"llm_temperature": float("inf")}) is False
    assert latency._config_is_known({"llm_temperature": float("nan")}) is False
    assert latency._config_is_known({"llm_temperature": 0.0}) is True
    assert latency._config_is_known({"ser_model": None}) is True
    assert latency._config_is_known({"asr_cpu_threads": None}) is False


def test_valid_complete_snapshot_still_round_trips(tmp_path):
    context = latency.load_recording_metadata(_metadata(tmp_path))
    assert context["config_fingerprint"] == latency._fingerprint(
        latency._config_view(AppConfig())
    )
    assert context["os"] == "Windows"
    assert context["git_head"] == "b" * 40


def test_direct_recording_context_is_cleaned_at_the_export_boundary(tmp_path):
    source = tmp_path / "metrics.json"
    source.write_text(json.dumps({"summary": {}, "turns": []}), encoding="utf-8")
    context = latency.load_recording_metadata(_metadata(tmp_path))
    polluted = dict(context)
    polluted.update(
        {
            "secret_path": r"C:\Users\\" + FAKE_SUFFIX + r"\token.txt",
            "url": "https://example.invalid/?token=" + FAKE_SUFFIX,
            "python": "3.12" + FAKE_SUFFIX,
            "os": FAKE_SUFFIX,
            "git_head": FAKE_SUFFIX,
        }
    )
    document, _notes = latency.capture_baseline(
        source,
        measurement="simulated",
        label="context-probe",
        root=tmp_path,
        recording_context=polluted,
    )
    exported = document["recording_context"]
    dumped = json.dumps(document)
    assert FAKE_SUFFIX not in dumped
    assert "secret_path" not in exported and "url" not in exported
    assert exported["git_head"] is None
    assert exported["python"] is None
    assert exported["os"] is None
    assert exported["config_fingerprint"] == context["config_fingerprint"]


def test_recording_context_rejects_non_mapping(tmp_path):
    source = tmp_path / "metrics.json"
    source.write_text(json.dumps({"summary": {}, "turns": []}), encoding="utf-8")
    with pytest.raises(latency.BaselineError):
        latency.capture_baseline(
            source,
            measurement="simulated",
            label="context-probe",
            root=tmp_path,
            recording_context=["not", "a", "mapping"],
        )
