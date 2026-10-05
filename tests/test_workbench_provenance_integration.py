"""Contract checks for the combined workbench and streaming options."""

import json

from srtp_voice.config import AppConfig
from tools import workbench_latency as latency


def test_transport_alias_is_a_real_bool_setting_with_distinct_fingerprints():
    key = "stream_llm_reuse_connections"
    assert key in latency.CONFIG_KEYS
    assert latency.CONFIG_ATTRIBUTES[key] == "stream_llm_reuse_http"
    enabled = AppConfig(stream_llm_reuse_http=True)
    disabled = AppConfig(stream_llm_reuse_http=False)
    on_view = latency._config_view(enabled)
    off_view = latency._config_view(disabled)
    assert on_view[key] is True
    assert off_view[key] is False
    assert latency._fingerprint(on_view) != latency._fingerprint(off_view)


def test_transport_alias_has_typed_and_complete_recording_provenance(tmp_path):
    config = latency._config_view(AppConfig())
    assert latency._config_is_known(config)
    metadata = {
        "config": config, "git_head": "a" * 40,
        "config_fingerprint": latency._fingerprint(config),
        "python": "3.12.10", "os": "Windows",
    }
    source = tmp_path / "metadata.json"
    source.write_text(json.dumps(metadata), encoding="utf-8")
    context = latency.load_recording_metadata(source)
    assert context["config_fingerprint"] == metadata["config_fingerprint"]
    assert context["config"]["stream_llm_reuse_connections"] is True

    config["stream_llm_reuse_connections"] = "fake-private-secret"
    metadata["config_fingerprint"] = latency._fingerprint(config)
    source.write_text(json.dumps(metadata), encoding="utf-8")
    rejected = latency.load_recording_metadata(source)
    assert rejected["config_fingerprint"] is None
    assert rejected["config"]["stream_llm_reuse_connections"] is None
    assert "fake-private-secret" not in json.dumps(rejected)
