"""Codex acceptance probes; no real dsh RPC, model loading or network share."""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from srtp_voice.config import AppConfig
from tools import dsh_client as dsh, workbench_doctor as doctor, workbench_latency as latency


class IdleSession:
    def __init__(self, root, session_id="target", on_list=None):
        self.root = root
        self.session_id = session_id
        self.on_list = on_list
        self.calls = []

    def rpc(self, method, parameters=None, *, request_id=None):
        self.calls.append(method)
        if method == "session/list":
            if self.on_list is not None:
                self.on_list()
            return {"ok": True, "value": {"items": [{
                "sessionId": self.session_id, "cwd": str(self.root), "running": False,
            }]}}
        if method == "session/projections":
            return {"ok": True, "value": {"values": {
                "inbox": {"next-turn": [], "next-step": []},
            }}}
        assert method == "session/prompt"
        return {"ok": True, "value": {"accepted": True}}


def task(root):
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "task.md").write_text("bounded test task", encoding="utf-8")
    return "docs/task.md"


def test_session_lock_cannot_collide_with_any_valid_dispatch_id(tmp_path):
    name = dsh.session_lock_name("target")
    assert not dsh.safe_id(Path(name).stem)
    recorder = IdleSession(tmp_path)
    result = dsh.dispatch_task(recorder, "session-valid-id", task(tmp_path),
                               session_id="target", project_root=tmp_path)
    assert result["state"] == "accepted"


def test_other_dispatch_id_cannot_even_read_queue_while_session_is_locked(tmp_path):
    filename = task(tmp_path)
    competitor = IdleSession(tmp_path)

    def contend():
        with pytest.raises(dsh.DshClientError):
            dsh.dispatch_task(competitor, "competing", filename,
                              session_id="target", project_root=tmp_path)

    owner = IdleSession(tmp_path, on_list=contend)
    result = dsh.dispatch_task(owner, "owner", filename, session_id="target", project_root=tmp_path)
    assert result["state"] == "accepted"
    assert competitor.calls == []
    assert owner.calls.count("session/prompt") == 1


def test_different_sessions_in_same_project_do_not_share_lock(tmp_path):
    recorder = IdleSession(tmp_path, session_id="second")
    filename = task(tmp_path)
    with dsh.session_dispatch_lock("first", root=tmp_path):
        result = dsh.dispatch_task(recorder, "parallel-other-session", filename,
                                   session_id="second", project_root=tmp_path)
    assert result["state"] == "accepted"


@pytest.mark.parametrize("field", ["cancelled", "failed"])
@pytest.mark.parametrize("bad", ["false", 0, 1, None, [], {}])
def test_outcome_types_are_rejected_without_echoing_values(tmp_path, field, bad):
    source = tmp_path / "metrics.json"
    source.write_text(json.dumps({"summary": {}, field: bad}), encoding="utf-8")
    with pytest.raises(latency.BaselineError):
        latency.capture_baseline(source, measurement="unknown", label="probe",
                                 cfg=AppConfig(), root=tmp_path)


def recorded_baseline(tmp_path):
    source = tmp_path / "metrics.json"
    source.write_text(json.dumps({"summary": {}, "turns": [{
        "turn_id": "turn1", "marks": {}, "latencies_ms": {"asr_final_ms": 20},
    }]}), encoding="utf-8")
    result, _ = latency.capture_baseline(source, measurement="real", label="probe",
                                         cfg=AppConfig(), root=tmp_path)
    result["recording_context"] = {
        "git_head": "a" * 40, "config_fingerprint": result["config_fingerprint"],
        "python": "3.11", "os": "test-platform",
    }
    return result


def test_empty_per_turn_evidence_never_produces_comparison(tmp_path):
    good = recorded_baseline(tmp_path)
    bad = json.loads(json.dumps(good))
    bad["per_turn"] = []
    with pytest.raises(latency.BaselineError):
        latency.compare_baselines(good, bad)


@pytest.mark.parametrize("bad_group", ["unknown-private-field", None, [], {}])
def test_invalid_groups_have_safe_public_error(tmp_path, bad_group):
    good = recorded_baseline(tmp_path)
    bad = json.loads(json.dumps(good))
    bad["per_turn"][0]["group"] = bad_group
    with pytest.raises(latency.BaselineError) as error:
        latency.compare_baselines(good, bad)
    assert "unknown-private-field" not in str(error.value)


def test_untrusted_per_turn_metric_names_are_not_echoed(tmp_path):
    good = recorded_baseline(tmp_path)
    bad = json.loads(json.dumps(good))
    bad["per_turn"][0]["latencies_ms"] = {"unknown-private-field": 20}
    with pytest.raises(latency.BaselineError) as error:
        latency.compare_baselines(good, bad)
    assert "unknown-private-field" not in str(error.value)


@pytest.mark.parametrize("raw", [r"\/server/share/model", r"/\server\share\model"])
def test_mixed_separator_unc_syntax_is_rejected_without_io(raw):
    assert PureWindowsPath(raw).drive.startswith("\\\\")
    assert doctor.is_remote_namespace(raw)


@pytest.mark.parametrize("online", [False, True])
def test_default_doctor_never_probes_configured_unc_paths(tmp_path, monkeypatch, online):
    from srtp_voice import diagnostics

    monkeypatch.setattr(diagnostics, "_collect_audio_environment", lambda: {})
    monkeypatch.setattr(doctor, "dependency_report", lambda *args, **kwargs: [])
    probes = []
    for method in ("resolve", "exists", "stat", "is_file"):
        original = getattr(Path, method)

        def guarded(path, *args, _original=original, **kwargs):
            if PureWindowsPath(str(path)).drive.startswith("\\\\"):
                probes.append(str(path))
                raise AssertionError("unexpected network path probe")
            return _original(path, *args, **kwargs)

        monkeypatch.setattr(Path, method, guarded)

    cfg = AppConfig(ser_model=Path(r"\\test-only\share\ser"),
                    tts_piper_model=r"\\test-only\share\piper",
                    tts_piper_exe=r"\\test-only\share\exe")
    fetched = []

    def fetch(url, timeout):
        fetched.append(url)
        return {"models": [{"name": cfg.llm_model}]}

    report = doctor.collect_doctor_report(cfg=cfg, project_root=tmp_path,
                                          fetch=fetch, online=online)
    assert probes == []
    for model in report["models"].values():
        assert model["exists"] is None
        assert model["probed"] is False
    assert "test-only" not in json.dumps(report)
    assert len(fetched) == int(online)


def test_diagnostics_no_path_probes_mode_is_real_not_a_flag(monkeypatch):
    from srtp_voice import diagnostics

    monkeypatch.setattr(diagnostics, "_collect_audio_environment", lambda: {})

    def denied(path):
        pytest.fail("metadata-only diagnostics called Path.exists")

    monkeypatch.setattr(Path, "exists", denied)
    report = diagnostics.collect_diagnostics(AppConfig(), probe_paths=False)
    assert all(item["probed"] is False for item in report["paths"].values())


def test_remote_path_returns_before_any_resolution(tmp_path, monkeypatch):
    def denied(path, *args, **kwargs):
        pytest.fail("a network path should return before any resolution")

    monkeypatch.setattr(Path, "resolve", denied)
    report = doctor.describe_path(r"\\test-only\share\model", tmp_path)
    assert report["exists"] is None
    assert report["probed"] is False


def test_unknown_network_model_is_warning_not_healthy(tmp_path, monkeypatch):
    from srtp_voice import diagnostics

    audio = {
        key: {"status": "ok", "available": True, "version": "synthetic"}
        for key in ("soundfile", "sounddevice", "portaudio", "default_input", "default_output")
    }
    monkeypatch.setattr(diagnostics, "_collect_audio_environment", lambda: audio)
    monkeypatch.setattr(doctor, "dependency_report", lambda *args, **kwargs: [])
    cfg = AppConfig(ser_backend="none", tts_piper_model=r"\\test-only\share\model",
                    tts_piper_exe=r"\\test-only\share\exe")
    report = doctor.collect_doctor_report(cfg=cfg, project_root=tmp_path, python_version=(3, 11, 0))
    assert report["audio"]["status"] == "ok"
    assert report["models"]["piper_model"]["exists"] is None
    assert report["severity"] == "warning"


@pytest.mark.parametrize("native_type,raw,foreign", [
    (PurePosixPath, "/tmp/project/model", False),
    (PurePosixPath, r"C:\project\model", True),
    (PureWindowsPath, r"C:\project\model", False),
    (PureWindowsPath, "/tmp/project/model", True),
    (PurePosixPath, "models/model", False),
    (PureWindowsPath, "models/model", False),
])
def test_native_and_foreign_path_syntax_on_both_platforms(native_type, raw, foreign):
    assert doctor.is_foreign_absolute_path(raw, native_path_type=native_type) is foreign
