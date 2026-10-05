"""Config and snapshot wiring tests for the in-memory ASR switch."""

from __future__ import annotations

import json

from srtp_voice.config import AppConfig
from tools import workbench_latency


def test_stream_asr_in_memory_defaults_on_and_is_strict(monkeypatch) -> None:
    from srtp_voice import config as config_module

    monkeypatch.setattr(config_module, "load_dotenv", None)
    monkeypatch.delenv("STREAM_ASR_IN_MEMORY", raising=False)
    assert AppConfig().stream_asr_in_memory is True

    assert AppConfig.from_env().stream_asr_in_memory is True

    monkeypatch.setenv("STREAM_ASR_IN_MEMORY", "1")
    assert AppConfig.from_env().stream_asr_in_memory is True

    monkeypatch.setenv("STREAM_ASR_IN_MEMORY", "0")
    assert AppConfig.from_env().stream_asr_in_memory is False

    for value in ("no", "off", "false", "on", "yes", "true"):
        monkeypatch.setenv("STREAM_ASR_IN_MEMORY", value)
        expected = value in {"on", "yes", "true"}
        assert AppConfig.from_env().stream_asr_in_memory is expected

    # env_bool only enables explicit truthy values: an unparsable value stays off.
    monkeypatch.setenv("STREAM_ASR_IN_MEMORY", "maybe")
    assert AppConfig.from_env().stream_asr_in_memory is False


def test_snapshot_config_whitelist_records_the_switch() -> None:
    assert "stream_asr_in_memory" in workbench_latency.CONFIG_KEYS

    context = workbench_latency.capture_context(cfg=AppConfig(), git={})
    assert context["config"]["stream_asr_in_memory"] is True

    other = workbench_latency.capture_context(
        cfg=AppConfig(stream_asr_in_memory=False), git={}
    )
    assert other["config"]["stream_asr_in_memory"] is False
    assert other["config_fingerprint"] != context["config_fingerprint"]
    assert "stream_asr_in_memory" in json.dumps(context)
