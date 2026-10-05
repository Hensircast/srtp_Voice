"""JSON strings in path settings share the live Path privacy boundary."""
import json

import pytest

from srtp_voice.config import AppConfig
from tools import workbench_latency as latency


@pytest.mark.parametrize("key", ["tts_piper_model", "tts_piper_exe", "tts_piper_config", "tts_piper_espeak_data", "ser_model"])
def test_json_path_strings_are_projected_and_round_trip_without_private_text(tmp_path, key):
    config = latency._config_view(AppConfig())
    config[key] = "models/fake-private-person/model.onnx"
    metadata = {"git_head": "a" * 40, "python": "3.12.10", "os": "Windows", "config": config}
    source = tmp_path / "metadata.json"
    source.write_text(json.dumps(metadata), encoding="utf-8")
    context = latency.load_recording_metadata(source)
    assert "fake-private-person" not in json.dumps(context)
    identity = context["config"][key]
    assert identity.startswith("pid1-")
    assert context["config_fingerprint"]
    source.write_text(json.dumps(context), encoding="utf-8")
    reloaded = latency.load_recording_metadata(source)
    assert reloaded["config"][key] == identity
    assert reloaded["config_fingerprint"] == context["config_fingerprint"]

    exported = latency._sanitize_recording_context(metadata)
    assert exported["config"][key] == identity
    assert "fake-private-person" not in json.dumps(exported)
    forged = dict(metadata, config_fingerprint=latency._fingerprint(config))
    previous = latency._revalidate_file_recording_context({"recording_context": forged})
    assert previous["recording_context"]["config_fingerprint"] is None
    assert "fake-private-person" not in json.dumps(previous)


@pytest.mark.parametrize("value", ["/home/fake-private-person/model.onnx", "C:/Users/fake-private-person/model.onnx", "models/../outside/model.onnx"])
def test_external_or_unsafe_string_model_paths_are_unknown(value):
    assert latency._safe_recording_config_value("ser_model", value) == "<redacted>"
