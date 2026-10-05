"""Own gate for the ASR model path privacy review item (workbench tools only).

Synthetic values only: the sentinels below are obvious fakes, never real user
paths, credentials or model files. The configured model is never probed; the
synthetic JSON written here lives only in the test's own temporary directory.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from srtp_voice.config import AppConfig
from tools import workbench_latency as latency

FAKE_USER = "fake-private-person"
PLAIN_NAMES = ("small", "base", "large-v3", "tiny.en", "distil-large-v3")


@pytest.mark.parametrize("name", PLAIN_NAMES)
def test_plain_catalogue_names_stay_readable(name):
    assert latency._safe_config_value(name, key="asr_model") == name
    view = latency._config_view(AppConfig(asr_model=name))
    assert view["asr_model"] == name
    assert latency._config_is_known(view) is True
    assert latency._fingerprint(view)


def test_actual_runtime_value_is_never_rewritten():
    hub_id = "Systran/faster-whisper-small"
    cfg = AppConfig(asr_model=hub_id)
    projected = latency._config_view(cfg)

    assert cfg.asr_model == hub_id  # the projection does not mutate the config
    assert projected["asr_model"] != hub_id
    assert projected["asr_model"].startswith("pid1-")


@pytest.mark.parametrize(
    "value",
    [
        f"models/{FAKE_USER}/whisper-small",
        f"./models/{FAKE_USER}/whisper-small",
        "Systran/faster-whisper-small",
    ],
)
def test_private_or_hub_paths_never_export_the_plaintext(value):
    projected = latency._safe_config_value(value, key="asr_model")
    dumped = json.dumps(latency._config_view(AppConfig(asr_model=value)))

    assert projected.startswith("pid1-")
    assert FAKE_USER not in dumped
    assert "models/" not in dumped and "Systran" not in dumped


def test_backslash_relative_follows_the_host_contract():
    """POSIX treats backslash syntax as unknown; Windows projects it in-project."""

    value = "models\\" + FAKE_USER + "\\small"
    projected = latency._safe_config_value(value, key="asr_model")
    dumped = json.dumps(latency._config_view(AppConfig(asr_model=value)))

    assert FAKE_USER not in dumped
    if os.name == "nt":
        assert projected.startswith("pid1-")
    else:
        assert projected == "<redacted>"


@pytest.mark.parametrize(
    "value",
    ["C:private/model", "C:private-model", "C:", f"D:{FAKE_USER}/model", "z:model"],
)
def test_drive_relative_syntax_is_unknown_everywhere(value):
    """``C:private/model`` depends on a per-drive working directory: not a source."""

    assert latency._safe_config_value(value, key="asr_model") == "<redacted>"

    view = latency._config_view(AppConfig(asr_model=value))
    assert view["asr_model"] == "<redacted>"
    assert latency._config_is_known(view) is False


def test_relative_and_project_absolute_paths_share_one_identity():
    relative = "models/asr/small"
    absolute = str(Path(latency.PROJECT_ROOT) / "models" / "asr" / "small")

    left = latency._safe_config_value(relative, key="asr_model")
    right = latency._safe_config_value(absolute, key="asr_model")

    assert left == right
    assert left.startswith("pid1-")


def test_different_directories_never_collide_on_basename():
    left = latency._config_view(AppConfig(asr_model="models/one/small"))
    right = latency._config_view(AppConfig(asr_model="models/two/small"))

    assert left["asr_model"] != right["asr_model"]
    assert latency._fingerprint(left) != latency._fingerprint(right)


def test_encoded_identity_round_trips_without_rehashing():
    first = latency._safe_config_value("models/asr/small", key="asr_model")
    assert latency._safe_config_value(first, key="asr_model") == first
    assert latency._safe_config_value(Path(first), key="asr_model") == first


@pytest.mark.parametrize(
    "value",
    [
        f"/home/{FAKE_USER}/models/small",
        f"C:\\Users\\{FAKE_USER}\\models\\small",
        f"//{FAKE_USER}-server/share/small",
        "../outside/small",
        f"/mnt/{FAKE_USER}/small",
    ],
)
def test_external_unc_cross_system_and_traversal_are_unknown(value):
    assert latency._safe_config_value(value, key="asr_model") == "<redacted>"

    view = latency._config_view(AppConfig(asr_model=value))
    assert latency._config_is_known(view) is False


def test_projection_never_probes_the_filesystem(monkeypatch):
    def forbidden(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("the projection must not touch the filesystem")

    monkeypatch.setattr(Path, "resolve", forbidden)
    monkeypatch.setattr(Path, "exists", forbidden)
    monkeypatch.setattr(Path, "stat", forbidden)

    assert latency._safe_config_value("models/asr/small", key="asr_model").startswith("pid1-")
    assert latency._safe_config_value(f"/home/{FAKE_USER}/small", key="asr_model") == "<redacted>"
    assert latency._safe_config_value("C:private/model", key="asr_model") == "<redacted>"


def test_all_export_boundaries_agree(tmp_path):
    value = "models/asr/small"
    expected = latency._safe_config_value(value, key="asr_model")

    live = latency._config_view(AppConfig(asr_model=value))
    recording = latency._safe_recording_config_value("asr_model", value)
    metadata = tmp_path / "metadata.json"
    config = {**latency._config_view(AppConfig()), "asr_model": value}
    metadata.write_text(
        json.dumps(
            {
                "git_head": "a" * 40,
                "config": config,
                "config_fingerprint": latency._fingerprint(config),
                "python": "3.12.10",
                "os": "Windows",
            }
        ),
        encoding="utf-8",
    )
    loaded = latency.load_recording_metadata(metadata)
    revalidated = latency._revalidate_file_recording_context(
        {"recording_context": dict(loaded)}
    )

    assert live["asr_model"] == recording == expected
    assert loaded["config"]["asr_model"] == expected
    assert revalidated["recording_context"]["config"]["asr_model"] == expected
    assert FAKE_USER not in json.dumps(loaded)


def test_unknown_asr_model_blocks_the_fingerprint(tmp_path):
    config = {**latency._config_view(AppConfig()), "asr_model": "<redacted>"}
    metadata = tmp_path / "metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "git_head": "a" * 40,
                "config": config,
                "config_fingerprint": latency._fingerprint(config),
                "python": "3.12.10",
                "os": "Windows",
            }
        ),
        encoding="utf-8",
    )
    loaded = latency.load_recording_metadata(metadata)

    assert loaded["config"]["asr_model"] == "<redacted>"
    assert loaded["config_fingerprint"] is None
