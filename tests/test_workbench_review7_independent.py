"""Final export-shape probes; synthetic values, no model or network."""
import json

import pytest

from srtp_voice.config import AppConfig
from tools import workbench_latency as latency


def test_python_version_is_ascii_not_unicode_digit_text():
    assert latency._valid_python_version("٣.١٢.١٠") is None
    assert latency._valid_python_version("3.12.10") == "3.12.10"


@pytest.mark.parametrize("field,value", [
    ("stream_tts_queue_size", 16), ("stream_sentence_min_chars", 30),
    ("stream_sentence_max_chars", 120),
])
def test_existing_stream_controls_have_distinct_fingerprints(field, value):
    before = AppConfig()
    after = AppConfig()
    setattr(after, field, value)
    assert latency._fingerprint(latency._config_view(before)) != latency._fingerprint(latency._config_view(after))


@pytest.mark.parametrize("field", ["asr_cpu_threads", "asr_vad_filter", "llm_temperature"])
def test_wrong_typed_numeric_or_bool_provenance_cannot_export_text(tmp_path, field):
    config = latency._config_view(AppConfig())
    config[field] = "fake-private-secret"
    source = tmp_path / "metadata.json"
    source.write_text(json.dumps({"git_head": "a" * 40, "config": config,
        "config_fingerprint": latency._fingerprint(config),
        "python": "3.12.10", "os": "Windows"}), encoding="utf-8")
    context = latency.load_recording_metadata(source)
    assert "fake-private-secret" not in json.dumps(context)
    assert context["config_fingerprint"] is None
