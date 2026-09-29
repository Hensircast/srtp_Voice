"""Independent review regressions for untrusted baseline input."""
from __future__ import annotations

import json

import pytest

from tools import workbench, workbench_latency as latency


@pytest.mark.parametrize("schema", ["private-schema-value", {}, None, True])
def test_compare_malformed_schema_has_safe_cli_error(tmp_path, monkeypatch, capsys, schema):
    monkeypatch.setattr(workbench, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(latency, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(latency, "read_git_head", lambda *args: {"head": None, "available": False})
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "metrics.json").write_text(json.dumps({"summary": {}}), encoding="utf-8")
    (outputs / "old.json").write_text(json.dumps({"schema_version": schema, "summary": {}}), encoding="utf-8")
    code = workbench.main([
        "baseline", "--metrics", "outputs/metrics.json", "--output", "outputs/new.json",
        "--compare", "outputs/old.json",
    ])
    assert code == 2
    printed = capsys.readouterr().out
    assert "private-schema-value" not in printed
    assert not (outputs / "new.json").exists()


def test_timing_metric_allowlist_keeps_all_real_metrics():
    from srtp_voice.streaming import StreamEvent, StreamEventType, TurnTiming
    from srtp_voice.streaming import _TIMING_MARKS

    timing = TurnTiming("review-turn")
    for index, event_type in enumerate(_TIMING_MARKS):
        timing.observe(StreamEvent(event_type, "review-turn", index, float(index), {}))
    generated = set(timing.snapshot().latencies_ms)
    assert "endpoint_to_playback_ms" in generated
    assert "llm_first_token_ms" in generated
    assert generated <= latency.known_metric_names()


def test_recording_snapshot_uses_loaded_configuration(monkeypatch):
    from srtp_voice.config import AppConfig
    cfg = AppConfig(llm_model="review-model", asr_backend="faster_whisper")
    monkeypatch.setattr(AppConfig, "from_env", classmethod(lambda cls: cfg))
    context = latency.capture_context(git={"head": "a" * 40, "available": True})
    assert context["config"]["llm_model"] == "review-model"
    assert context["config"]["asr_backend"] == "faster_whisper"


def test_baseline_import_context_uses_loaded_config_not_constructor_defaults(tmp_path, monkeypatch):
    from srtp_voice.config import AppConfig
    cfg = AppConfig(asr_backend="faster_whisper", llm_model="review:actual")
    monkeypatch.setattr(AppConfig, "from_env", classmethod(lambda cls: cfg))
    source = tmp_path / "metrics.json"
    source.write_text('{"summary": {}}', encoding="utf-8")
    document, _ = latency.capture_baseline(source, root=tmp_path, measurement="unknown", label="import")
    assert document["capture_context"]["config"]["llm_model"] == "review:actual"
    assert document["recording_context"] is None


def test_model_tags_remain_distinct_in_snapshot_fingerprints():
    from srtp_voice.config import AppConfig
    contexts = [latency.capture_context(cfg=AppConfig(llm_model=name), git={})
                for name in ("qwen3:4b-instruct", "qwen3:8b-instruct")]
    assert contexts[0]["config"]["llm_model"] == "qwen3:4b-instruct"
    assert contexts[0]["config_fingerprint"] != contexts[1]["config_fingerprint"]
    for value in ("https://user:password@private.example/model", "C:/Users/private/model", "/home/private/model"):
        assert latency._safe_config_value(value) == "<redacted>"


def test_baseline_round_trip_remains_valid(tmp_path):
    from srtp_voice.config import AppConfig
    source = tmp_path / "metrics.json"
    source.write_text(json.dumps({"summary": {"endpoint_to_playback_ms": {
        "count": 1, "min": 100, "p50": 100, "p95": 100, "max": 100,
    }}}), encoding="utf-8")
    document, _ = latency.capture_baseline(source, measurement="real", label="roundtrip", cfg=AppConfig(), root=tmp_path)
    assert isinstance(document["summary"]["endpoint_to_playback_ms"]["count"], int)
    loaded = json.loads(json.dumps(document))
    assert latency.compare_baselines(loaded, loaded)["comparable"] is False


def test_dsh_actual_inbox_projection_names_are_counted_not_copied():
    from tools.dsh_client import summarize_projections
    summary = summarize_projections({"values": {
        "inbox": {"next-turn": [{"text": "private-placeholder"}], "next-step": []},
        "permissions": {"currentValue": "danger-full-access"},
        "tokenUsage": {"uncachedInputTokens": 12, "cacheReadTokens": 30, "outputTokens": 5},
    }})
    assert summary["queue_known"] is True
    assert summary["queue_count"] == 1
    assert summary["token_usage"]["uncachedInputTokens"] == 12
    assert "private-placeholder" not in json.dumps(summary)


@pytest.mark.parametrize("prefix", [["-cprint(1)"], ["-X", "dev"], ["-W", "ignore"], ["--"]])
def test_task_rejects_interpreter_options_without_writing(tmp_path, prefix):
    from tools import workbench_tasks as tasks
    with pytest.raises(tasks.TaskError):
        tasks.run_task("unsafe", ["python", *prefix, "-m", "tools.workbench", "doctor"], root=tmp_path)
    assert not (tmp_path / "outputs").exists()


@pytest.mark.parametrize("arguments", [
    ["doctor", "--token", "private-placeholder"],
    ["doctor", "private-placeholder"],
    ["snapshot", "--label", "http://private.invalid/?token=placeholder"],
    ["validate", "--profile", "manual", "tests/missing.py"],
    ["baseline", "--metrics", "../private.json", "--output", "outputs/result.json"],
])
def test_task_rejects_bad_arguments_before_persistence(tmp_path, arguments):
    from tools import workbench_tasks as tasks
    with pytest.raises(tasks.TaskError) as error:
        tasks.run_task("unsafe", ["python", "-m", "tools.workbench", *arguments], root=tmp_path)
    assert "private-placeholder" not in str(error.value)
    assert not (tmp_path / "outputs").exists()


def test_task_duplicate_check_is_inside_lock(tmp_path, monkeypatch):
    from tools import workbench_tasks as tasks
    layout = tasks.task_layout("race", root=tmp_path)
    original = tasks.acquire_lock

    def competing_writer(target, attempt):
        original(target, attempt)
        target.manifest.write_text('{"history":"preserve"}', encoding="utf-8")

    monkeypatch.setattr(tasks, "acquire_lock", competing_writer)
    with pytest.raises(tasks.TaskError):
        tasks.run_task("race", ["python", "-m", "tools.workbench", "doctor"], root=tmp_path)
    assert json.loads(layout.manifest.read_text(encoding="utf-8"))["history"] == "preserve"
    assert not layout.lock.exists()


def test_dsh_retry_lock_is_exclusive_and_released(tmp_path):
    from tools import dsh_client as client
    ledger = client.ledger_path("race", root=tmp_path)
    with client._dispatch_lock(ledger):
        with pytest.raises(client.DshClientError):
            with client._dispatch_lock(ledger):
                pytest.fail("another writer entered the same dispatch")
    with client._dispatch_lock(ledger):
        pass


def test_abnormal_child_exit_cannot_be_resumed(tmp_path, monkeypatch):
    from tools import workbench_tasks as tasks

    class Child:
        pid = 12345
        def wait(self):
            return 130

    result = tasks.run_task("cancelled", ["python", "-m", "tools.workbench", "doctor"],
                            root=tmp_path, popen=lambda *args, **kwargs: Child())
    assert result.status == "unknown"
    monkeypatch.setattr(tasks, "process_status", lambda pid: "dead")
    with pytest.raises(tasks.TaskError):
        tasks.resume_task("cancelled", root=tmp_path)
