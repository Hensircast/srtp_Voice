"""Final strict projection checks without private inputs, devices or network."""
import json
from pathlib import Path

import pytest

from srtp_voice.config import AppConfig
from tools import workbench_latency as latency


def test_file_revalidation_rejects_wrong_typed_config_and_discards_unknown_keys():
    config = latency._config_view(AppConfig())
    config["asr_cpu_threads"] = "fake-private-secret"
    result = latency._revalidate_file_recording_context({"recording_context": {
        "git_head": "a" * 40, "python": "3.12.10", "os": "Windows",
        "token": "fake-private-secret", "config": config,
        "config_fingerprint": latency._fingerprint(config),
    }})
    assert result["recording_context"]["config_fingerprint"] is None
    assert "fake-private-secret" not in json.dumps(result)


def test_capture_invalid_overrides_stay_unknown_not_host_fallback():
    context = latency.capture_context(cfg=AppConfig(), git={"head": "unsafe", "available": True},
        python_version="3.12-private", os_name="unknown-os")
    assert context["python"] is None
    assert context["os"] is None
    assert context["git"] == {"head": None, "available": False}


def test_path_projection_never_calls_resolve_even_for_unc(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("configuration identity must not probe paths")
    monkeypatch.setattr(Path, "resolve", forbidden)
    assert latency._safe_config_value(Path("//fake-host/share/model.onnx")) == "<redacted>"
    assert latency._safe_config_value(Path("models/a/model.onnx")).startswith("pid1-")


def test_relative_and_absolute_project_paths_have_same_identity():
    relative = Path("models/a/model.onnx")
    absolute = Path(latency.PROJECT_ROOT) / relative
    assert latency._safe_config_value(relative) == latency._safe_config_value(absolute)


def test_ipv6_endpoint_is_recognized_without_exporting_address():
    value = latency._safe_config_value("http://[::1]:11434/api/chat", key="llm_ollama_chat_url")
    assert value.startswith("ep1-")
    assert "::1" not in value


@pytest.mark.parametrize("value", [None, 42, True, "bad endpoint", "http://host.invalid/x?q=route", "http://host.invalid/x#frag"])
def test_invalid_endpoint_configuration_is_unknown(value):
    context = latency.capture_context(cfg=AppConfig(llm_ollama_chat_url=value), git={})
    assert context["config_fingerprint"] is None
    assert context["config"]["llm_ollama_chat_url"] in (None, "<redacted>")


def test_huge_integer_does_not_overflow_float_projection():
    config = latency._config_view(AppConfig(llm_temperature=10 ** 400))
    assert config["llm_temperature"] is None
    json.dumps(config, allow_nan=False)
