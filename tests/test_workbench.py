"""Offline tests for the project workbench (doctor / validate / baseline)."""

from __future__ import annotations

import copy
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from srtp_voice.config import AppConfig
from tools import workbench, workbench_doctor, workbench_latency, workbench_validate

PROJECT_ROOT = workbench.PROJECT_ROOT


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _fake_diagnostics(*, sounddevice_ok: bool = True, default_ok: bool = True):
    def collect(_cfg):
        return {
            "system": {"os": "Fake", "platform": "Fake-1", "python": "3.11.9"},
            "config": {"sample_rate": 16000},
            "paths": {
                "ser_model": {"path": "C:\\Users\\somebody\\secret\\model", "exists": True},
                "piper_executable": {"path": "C:\\Users\\somebody\\secret\\piper.exe", "exists": False},
                "piper_model": {"path": "C:\\Users\\somebody\\secret\\voice.onnx", "exists": True},
            },
            "audio": {
                "soundfile": {"status": "ok", "available": True, "version": "0.test"},
                "sounddevice": {
                    "status": "ok" if sounddevice_ok else "warning",
                    "available": sounddevice_ok,
                    "version": "0.5.test" if sounddevice_ok else None,
                },
                "portaudio": {"status": "ok", "version": 1246720, "text": "PortAudio"},
                "default_input": {
                    "status": "ok" if default_ok else "warning",
                    "index": 1,
                    "name": "Private Microphone Name",
                    "channels": 2,
                    "sample_rate": 48000.0,
                    "error": None,
                },
                "default_output": {
                    "status": "warning",
                    "index": None,
                    "name": None,
                    "channels": None,
                    "sample_rate": None,
                    "error": "No default output audio device",
                },
            },
        }

    return collect


def _snapshot(directory: Path) -> dict[str, float]:
    if not directory.exists():
        return {}
    return {
        entry.relative_to(directory).as_posix(): entry.stat().st_mtime
        for entry in sorted(directory.rglob("*"))
        if entry.is_file()
    }


def _fake_metrics(*, turns=True) -> dict:
    payload = {
        "summary": {
            "endpoint_to_playback_ms": {
                "count": 2,
                "min": 2703.0,
                "p50": 3398.0,
                "p95": 7906.0,
                "max": 8906.0,
            },
            "asr_final_ms": {"count": 2, "min": 1312.0, "p50": 1437.0, "p95": 2859.25, "max": 3234.0},
        },
        "late_events": 0,
        "dropped_event_history": 738,
        "cancelled": True,
        "tts_backpressure_events": 0,
        "execution": {"mode": "streaming"},
    }
    if turns:
        payload["turns"] = [
            {
                "turn_id": "turn-a",
                "marks": {"turn_started": 0.0, "vad_stopped": 2.0},
                "latencies_ms": {"endpoint_to_playback_ms": 2703.0},
            },
            {
                "turn_id": "turn-b",
                "marks": {"turn_started": 30.0, "vad_stopped": 32.5},
                "latencies_ms": {"endpoint_to_playback_ms": 8906.0},
            },
        ]
    return payload


def _write_json(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class _RecordingRunner:
    def __init__(self, codes: list[int]) -> None:
        self.codes = list(codes)
        self.calls: list[dict] = []

    def __call__(self, argv, cwd=None, shell=None, **kwargs):
        code = self.codes[len(self.calls)] if len(self.calls) < len(self.codes) else 0
        self.calls.append({"argv": list(argv), "cwd": cwd, "shell": shell})
        return SimpleNamespace(returncode=code, stdout="", stderr="")


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #
def test_doctor_report_is_read_only_and_masks_paths_devices_and_secrets(tmp_path) -> None:
    outputs = PROJECT_ROOT / "outputs"
    before = _snapshot(outputs)

    report = workbench_doctor.collect_doctor_report(
        collect=_fake_diagnostics(),
        cfg=AppConfig(tts_piper_model=PROJECT_ROOT / "models" / "private-model.onnx"),
        project_root=tmp_path,
        python_version=(3, 11, 9),
    )
    rendered = workbench_doctor.render_report(report)
    serialized = json.dumps(report, ensure_ascii=False)

    assert report["python"]["status"] == "ok"
    for leaked in (
        "C:\\Users",
        "Private Microphone Name",
        "127.0.0.1",
        "11434",
        str(PROJECT_ROOT),
    ):
        assert leaked not in rendered
        assert leaked not in serialized
    assert report["models"]["piper_model"]["location"] == "<outside-project>"
    assert _snapshot(outputs) == before


def test_doctor_missing_base_dependency_is_error_and_missing_optional_is_warning() -> None:
    def fake_finder(name: str):
        if name in {"numpy", "torch"}:
            raise ModuleNotFoundError(f"{name} is not installed")
        return SimpleNamespace(origin="fake")

    base = workbench_doctor.dependency_report(
        (("numpy", "numpy"),), base=True, spec_finder=fake_finder
    )
    optional = workbench_doctor.dependency_report(
        (("torch", "torch"),), base=False, spec_finder=fake_finder
    )

    assert base[0]["name"] == "numpy"
    assert base[0]["status"] == "error"
    assert base[0]["present"] is False
    assert optional[0]["status"] == "warning"
    assert optional[0]["present"] is False
    assert base[0]["detail"] == "ModuleNotFoundError:dependency_missing"
    assert "is not installed" not in json.dumps(base + optional)


def test_doctor_severity_tracks_missing_base_dependency(monkeypatch) -> None:
    failing = [
        {
            "name": "numpy",
            "present": False,
            "version": None,
            "status": "error",
            "detail": "ModuleNotFoundError:dependency_missing",
        }
    ]

    def fake_dependency_report(entries, *, base, spec_finder=None):
        return list(failing) if base else []

    monkeypatch.setattr(workbench_doctor, "dependency_report", fake_dependency_report)
    report = workbench_doctor.collect_doctor_report(
        collect=_fake_diagnostics(),
        project_root=PROJECT_ROOT,
        python_version=(3, 11, 9),
    )

    assert report["dependencies"]["base"] == failing
    assert report["severity"] == "error"


def test_doctor_missing_audio_devices_and_optional_models_are_warnings_only(tmp_path) -> None:
    isolated_root = tmp_path / "isolated-project"
    isolated_root.mkdir()
    cfg = AppConfig(
        tts_piper_exe=isolated_root / "tools" / "piper" / "piper.exe",
        tts_piper_model=isolated_root / "models" / "piper" / "model.onnx",
        ser_model=isolated_root / "models" / "ser" / "model",
    )
    report = workbench_doctor.collect_doctor_report(
        cfg=cfg,
        collect=_fake_diagnostics(default_ok=False),
        project_root=isolated_root,
        python_version=(3, 11, 9),
    )

    assert report["audio"]["default_input"]["present"] is False
    assert report["audio"]["default_output"]["present"] is False
    assert report["audio"]["status"] == "warning"
    assert report["models"]["piper_executable"] == {
        "location": "tools/piper/piper.exe",
        "exists": False,
        "bytes": None,
        "probed": True,
    }
    assert report["models"]["ser_model"]["location"] == "<outside-project>"
    assert report["models"]["ser_model"]["exists"] is True
    assert "warning" in {report["severity"]} or report["severity"] == "ok"
    assert report["severity"] != "error"


def test_doctor_python_outside_supported_range_is_error() -> None:
    too_old = workbench_doctor.check_python((3, 10, 0))
    supported = workbench_doctor.check_python((3, 11, 9))

    assert too_old["status"] == "error" and too_old["reason"] == "below_minimum"
    assert supported["status"] == "ok"


def test_doctor_online_rejects_remote_and_credentialed_endpoints() -> None:
    calls: list[str] = []

    def fetch(url: str, timeout: float):
        calls.append(url)
        return {"models": []}

    remote = workbench_doctor.check_online("http://192.168.10.7:11434", "qwen3:4b", fetch=fetch)
    credentialed = workbench_doctor.check_online(
        "http://user:token@127.0.0.1:11434", "qwen3:4b", fetch=fetch
    )
    query = workbench_doctor.check_online(
        "http://127.0.0.1:11434?api_key=abcd", "qwen3:4b", fetch=fetch
    )
    https_remote = workbench_doctor.check_online(
        "https://ollama.example.com", "qwen3:4b", fetch=fetch
    )

    assert calls == []
    assert remote["checked"] is False and remote["reason"] == "not_loopback"
    assert credentialed["status"] == "warning" and credentialed["checked"] is False
    assert credentialed["reason"] in {"credentials_present", "unparsable_endpoint"}
    assert query["status"] == "warning" and query["checked"] is False
    assert query["reason"] in {"query_or_fragment_present", "unparsable_endpoint"}
    assert https_remote["reason"] == "not_loopback"
    assert "token" not in json.dumps(credentialed)


def test_doctor_online_loopback_compares_only_needed_model() -> None:
    seen: list[tuple[str, float]] = []

    def fetch(url: str, timeout: float):
        seen.append((url, timeout))
        return {"models": [{"name": "qwen3:4b-instruct"}, {"name": "llama3:8b"}]}

    hit = workbench_doctor.check_online(
        "http://127.0.0.1:11434", "qwen3:4b-instruct", fetch=fetch, timeout=10.0
    )
    miss = workbench_doctor.check_online("http://127.0.0.1:11434", "missing:1b", fetch=fetch)

    assert seen == [
        ("http://127.0.0.1:11434/api/tags", 3.0),
        ("http://127.0.0.1:11434/api/tags", 3.0),
    ]
    assert hit["status"] == "ok" and hit["model_present"] is True
    assert miss["status"] == "warning" and miss["reason"] == "model_not_listed"
    assert all("/api/generate" not in url and "/api/pull" not in url for url, _ in seen)


def test_doctor_online_failure_reports_category_not_message() -> None:
    def fetch(url: str, timeout: float):
        raise RuntimeError("connection refused for token=super-secret-value")

    report = workbench_doctor.check_online("http://127.0.0.1:11434", "qwen3:4b", fetch=fetch)

    assert report["status"] == "warning"
    assert report["reason"] == "RuntimeError:unexpected"
    assert "super-secret-value" not in json.dumps(report)


def test_doctor_cli_json_exit_code_matches_severity(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        workbench_doctor,
        "collect_doctor_report",
        lambda **kwargs: {
            "severity": "warning",
            "python": {"status": "ok", "version": "3.11", "supported": True, "reason": None},
            "dependencies": {"base": [], "optional": []},
            "audio": {"status": "warning"},
            "models": {},
            "disk": {"status": "ok", "free_bytes": 1, "total_bytes": 2},
            "online": None,
        },
    )

    code = workbench.main(["doctor", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["severity"] == "warning"


# --------------------------------------------------------------------------- #
# validate
# --------------------------------------------------------------------------- #
def test_validate_offline_and_full_plans_use_shared_entry_point() -> None:
    offline = workbench_validate.build_plan("offline")
    full = workbench_validate.build_plan("full")

    python = sys.executable
    assert offline == [
        [python, "-m", "compileall", "-q", "main.py", "srtp_voice", "tools", "tests"],
        [python, "-m", "pytest", "-q", "tests"],
    ]
    assert full == offline + [[python, "-m", "pip", "check"]]
    flattened = " ".join(" ".join(argv) for argv in full)
    for forbidden in ("install", "--cov", "http://", "https://"):
        assert forbidden not in flattened


def test_validate_targeted_accepts_tests_files_and_rejects_escapes() -> None:
    plan = workbench_validate.build_plan("targeted", ["tests/test_workbench.py"])
    assert plan == [[sys.executable, "-m", "pytest", "-q", "tests/test_workbench.py"]]

    for bad in ("tests", "tests/../main.py", "srtp_voice/config.py", "../tests/test_x.py"):
        with pytest.raises(workbench_validate.ValidationError):
            workbench_validate.resolve_test_targets([bad])
    for bad in ("--cov", "-x"):
        with pytest.raises(workbench_validate.ValidationError):
            workbench_validate.resolve_test_targets([bad])
    with pytest.raises(workbench_validate.ValidationError):
        workbench_validate.resolve_test_targets(["tests/notes.txt"])
    with pytest.raises(workbench_validate.ValidationError):
        workbench_validate.resolve_test_targets(["--cov", "tests/test_workbench.py"])
    with pytest.raises(workbench_validate.ValidationError):
        workbench_validate.resolve_test_targets([])
    with pytest.raises(workbench_validate.ValidationError):
        workbench_validate.build_plan("nonsense")


def test_validate_runs_commands_with_fixed_cwd_and_propagates_failure() -> None:
    runner = _RecordingRunner([0, 3, 0])

    outcome = workbench_validate.run_validation("full", runner=runner)

    assert [call["argv"][1:3] for call in runner.calls] == [
        ["-m", "compileall"],
        ["-m", "pytest"],
    ]
    assert {call["shell"] for call in runner.calls} == {False}
    assert {Path(call["cwd"]) for call in runner.calls} == {PROJECT_ROOT}
    assert outcome.status == "failed"
    assert workbench_validate.exit_code_for(outcome) == 3
    assert len(outcome.commands) == 2  # stopped after the failing command


def test_validate_offline_success_exit_code() -> None:
    ok_runner = _RecordingRunner([0, 0])
    outcome = workbench_validate.run_validation("offline", runner=ok_runner)

    assert outcome.status == "ok"
    assert workbench_validate.exit_code_for(outcome) == 0
    assert len(outcome.commands) == 2


def test_validate_manual_never_runs_anything(capsys) -> None:
    runner = _RecordingRunner([])
    outcome = workbench_validate.run_validation("manual", runner=runner)

    assert runner.calls == []
    assert outcome.status == "manual"
    assert outcome.commands == []
    assert workbench_validate.MANUAL_ENTRY in " ".join(outcome.notes)
    assert workbench_validate.exit_code_for(outcome) == 0


def test_validate_cli_rejects_bad_targets_without_running(capsys) -> None:
    code = workbench.main(["validate", "--profile", "targeted", "../outside/test_x.py"])

    assert code == 2
    assert "validation error" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# baseline
# --------------------------------------------------------------------------- #
def test_baseline_without_turns_marks_per_turn_unavailable(tmp_path) -> None:
    metrics = _write_json(tmp_path / "streaming_metrics.json", _fake_metrics(turns=False))

    document, notes = workbench_latency.capture_baseline(
        metrics, measurement="real", label="first", root=PROJECT_ROOT
    )

    assert document["per_turn_available"] is False
    assert document["per_turn"] == []
    assert document["per_turn_groups"] == {"first_observed": 0, "subsequent": 0}
    assert any("per_turn_unavailable" in note for note in notes)
    assert document["counters"]["dropped_event_history"] == 738
    assert document["summary"]["endpoint_to_playback_ms"]["p50"] == 3398.0
    assert any("non-whitelisted" in note for note in notes)


def test_baseline_with_turns_groups_in_recorded_order_only(tmp_path) -> None:
    metrics = _write_json(tmp_path / "streaming_metrics.json", _fake_metrics())

    document, _ = workbench_latency.capture_baseline(
        metrics, measurement="real", label="second", root=PROJECT_ROOT
    )

    assert document["per_turn_available"] is True
    assert [turn["turn_id"] for turn in document["per_turn"]] == ["turn-a", "turn-b"]
    assert [turn["group"] for turn in document["per_turn"]] == ["first_observed", "subsequent"]
    assert document["per_turn_groups"] == {"first_observed": 1, "subsequent": 1}
    assert "does not prove cold/warm start" in document["per_turn_note"]
    assert "confirmed" not in document["per_turn_note"].lower()
    assert json.dumps(document)
    for turn in document["per_turn"]:
        assert set(turn) <= {"turn_id", "marks", "latencies_ms", "group"}
        assert set(turn["marks"]) <= {"turn_started", "vad_stopped"}
        assert all(isinstance(value, (int, float)) for value in turn["latencies_ms"].values())


def test_baseline_rejects_invalid_numbers_and_schema(tmp_path) -> None:
    bad_number = _write_json(
        tmp_path / "nan.json",
        {"summary": {"asr_final_ms": {"p50": float("nan"), "count": 1}}},
    )
    negative = _write_json(
        tmp_path / "negative.json",
        {
            "turns": [
                {"turn_id": "t", "latencies_ms": {"endpoint_to_playback_ms": -5.0}}
            ]
        },
    )
    literal_nan = tmp_path / "literal-nan.json"
    literal_nan.write_text('{"summary": {"p50": NaN}}', encoding="utf-8")
    not_object = _write_json(tmp_path / "list.json", [1, 2, 3])
    bad_marks = _write_json(
        tmp_path / "marks.json",
        {"turns": [{"turn_id": "t", "marks": {"turn_started": "soon"}}]},
    )
    bad_turns = _write_json(tmp_path / "turns.json", {"turns": {"turn_id": "t"}})

    for path in (bad_number, negative, literal_nan, not_object, bad_marks, bad_turns):
        with pytest.raises(workbench_latency.BaselineError):
            workbench_latency.capture_baseline(path, measurement="real", label="x", root=PROJECT_ROOT)


def test_baseline_output_refuses_overwrite_and_stays_under_outputs(tmp_path) -> None:
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    existing = outputs / "baseline.json"
    existing.write_text("{}", encoding="utf-8")

    second = workbench_latency.resolve_output_path("outputs/baseline.json", root=tmp_path)
    assert second == outputs / "baseline-1.json"
    assert not second.exists()

    outside = workbench_latency.resolve_output_path("outputs/baseline-two.json", root=tmp_path)
    assert outside.parent == outputs

    for escaping in ("outputs/../../notes.json", "../notes.json"):
        with pytest.raises(workbench_latency.BaselineError):
            workbench_latency.resolve_output_path(escaping, root=tmp_path)
    with pytest.raises(workbench_latency.BaselineError):
        workbench_latency.resolve_output_path(str(tmp_path / "notes.json"), root=tmp_path)

    with pytest.raises(workbench_latency.BaselineError):
        workbench_latency.resolve_output_path("notes/baseline.json", root=tmp_path)

    document, _ = workbench_latency.capture_baseline(
        _write_json(tmp_path / "m.json", _fake_metrics()),
        measurement="real",
        label="x",
        root=tmp_path,
    )
    workbench_latency.write_baseline(document, second)
    with pytest.raises(workbench_latency.BaselineError):
        workbench_latency.write_baseline(document, second)


def test_baseline_document_is_sanitized(tmp_path) -> None:
    metrics = _write_json(tmp_path / "streaming_metrics.json", _fake_metrics())

    document, _ = workbench_latency.capture_baseline(
        metrics, measurement="real", label="sanitized", root=tmp_path / "other-project"
    )
    serialized = json.dumps(document, ensure_ascii=False)

    assert document["source"]["location"] == "<outside-project>"
    assert document["source"]["sha256"]
    assert "dialog" not in serialized.lower()
    assert "prompt" not in serialized.lower()
    assert "http" not in serialized.lower()
    assert str(tmp_path) not in serialized


def test_baseline_comparison_requires_comparable_measurement_and_config(tmp_path) -> None:
    metrics = _write_json(tmp_path / "m.json", _fake_metrics())
    base, _ = workbench_latency.capture_baseline(
        metrics, measurement="real", label="a", root=PROJECT_ROOT
    )
    same = copy.deepcopy(base)
    for metric in same["summary"].values():
        for stat in ("min", "p50", "p95", "max"):
            metric[stat] += 100.0  # valid shifted distribution, not inverted percentiles

    context = {
        "git_head": "a" * 40,
        "config_fingerprint": base["config_fingerprint"],
        "python": base["capture_context"]["python"],
        "os": base["capture_context"]["os"],
    }
    base["recording_context"] = dict(context)
    same["recording_context"] = dict(context)

    comparable = workbench_latency.compare_baselines(same, base)
    assert comparable["comparable"] is True
    assert comparable["metrics"]["endpoint_to_playback_ms"]["p50"] == 100.0
    assert comparable["per_turn"]["groups"]["first_observed"]["latency_ms"][
        "endpoint_to_playback_ms"
    ]["p50"] == 0.0
    assert set(comparable["per_turn"]["groups"]) == {"first_observed", "subsequent"}

    other_measurement = copy.deepcopy(same)
    other_measurement["measurement"] = "simulated"
    assert workbench_latency.compare_baselines(other_measurement, base)["comparable"] is False

    other_config = copy.deepcopy(same)
    # Provenance is judged from the real recording context, not the imported
    # capture fingerprint, so exercise a changed recording config here.
    other_config["recording_context"]["config_fingerprint"] = "deadbeef"
    result = workbench_latency.compare_baselines(other_config, base)
    assert result["comparable"] is False
    assert "recording config_fingerprint differs" in result["reasons"]

    no_turns = copy.deepcopy(same)
    no_turns["per_turn_available"] = False
    assert workbench_latency.compare_baselines(no_turns, base)["comparable"] is False


def test_baseline_cli_writes_json_and_reports_missing_turns(tmp_path, capsys) -> None:
    metrics = _write_json(tmp_path / "metrics.json", _fake_metrics(turns=False))
    output = PROJECT_ROOT / "outputs" / f"workbench-pytest-{tmp_path.name}.json"
    created: list[Path] = []
    try:
        code = workbench.main(
            [
                "baseline",
                "--metrics",
                str(metrics),
                "--output",
                str(output.relative_to(PROJECT_ROOT)),
                "--measurement",
                "real",
                "--label",
                "cli",
            ]
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "baseline written" in out
        assert "per_turn_unavailable" in out
        written = sorted(output.parent.glob(output.stem + "*.json"))
        created.extend(written)
        payload = json.loads(written[0].read_text(encoding="utf-8"))
        assert payload["measurement"] == "real"
        assert payload["label"] == "cli"
        assert payload["capture_context"]["git"]["available"] in {True, False}
    finally:
        for path in created:
            path.unlink(missing_ok=True)


def test_baseline_cli_rejects_escaping_paths(tmp_path, capsys) -> None:
    metrics = _write_json(tmp_path / "metrics.json", _fake_metrics())

    code = workbench.main(
        [
            "baseline",
            "--metrics",
            str(metrics),
            "--output",
            "../outside.json",
        ]
    )

    assert code == 2
    assert "baseline error" in capsys.readouterr().out


def test_workbench_parser_exposes_three_subcommands() -> None:
    parser = workbench.build_parser()
    for command in ("doctor", "validate", "baseline"):
        assert parser.parse_args([command, *_minimal_args(command)])


def _minimal_args(command: str) -> list[str]:
    if command == "validate":
        return ["--profile", "offline"]
    if command == "baseline":
        return ["--metrics", "outputs/none.json", "--output", "outputs/none-out.json"]
    if command == "snapshot":
        return ["--output", "outputs/none-snapshot.json"]
    return []


# --------------------------------------------------------------------------- #
# review regression tests (these must fail before the fixes below)
# --------------------------------------------------------------------------- #
def test_doctor_never_initializes_heavy_optional_modules(monkeypatch) -> None:
    initialized: list[str] = []
    real_import = importlib.import_module

    def guard(name: str, *args, **kwargs):
        if name in {"torch", "funasr", "faster_whisper", "piper"}:
            initialized.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", guard)
    report = workbench_doctor.collect_doctor_report(
        collect=_fake_diagnostics(),
        project_root=PROJECT_ROOT,
        python_version=(3, 11, 9),
    )

    assert initialized == []
    assert {entry["name"] for entry in report["dependencies"]["base"]} >= {
        "pyserial",
        "edge-tts",
    }
    assert "pyserial" not in {entry["name"] for entry in report["dependencies"]["optional"]}
    assert "torch" in {entry["name"] for entry in report["dependencies"]["optional"]}


def test_doctor_newer_untested_python_is_warning_not_error() -> None:
    assert workbench_doctor.check_python((3, 10, 0))["status"] == "error"
    assert workbench_doctor.check_python((3, 11, 9))["status"] == "ok"
    newer = workbench_doctor.check_python((3, 15, 0))
    assert newer["status"] == "warning"
    assert newer["supported"] is True
    assert newer["reason"] == "newer_than_tested"


def test_doctor_online_uses_configured_chat_endpoint_origin() -> None:
    seen: list[str] = []

    def fetch(url: str, timeout: float):
        seen.append(url)
        return {"models": [{"name": "qwen3:4b-instruct"}]}

    result = workbench_doctor.check_online(
        "http://127.0.0.1:11500/api/chat", "qwen3:4b-instruct", fetch=fetch
    )

    assert seen and seen[0] == "http://127.0.0.1:11500/api/tags"
    assert result["status"] == "ok"


def test_doctor_online_rejects_credentials_with_empty_parts_and_bad_port() -> None:
    seen: list[str] = []

    def fetch(url: str, timeout: float):
        seen.append(url)
        return {"models": []}

    empty_user = workbench_doctor.check_online(
        "http://@127.0.0.1:11434", "qwen3:4b", fetch=fetch
    )
    empty_password = workbench_doctor.check_online(
        "http://user:@127.0.0.1:11434", "qwen3:4b", fetch=fetch
    )
    bad_port = workbench_doctor.check_online("http://127.0.0.1:99999", "qwen3:4b", fetch=fetch)
    remote = workbench_doctor.check_online("http://10.0.0.5:11434", "qwen3:4b", fetch=fetch)

    assert seen == []
    for rejection in (empty_user, empty_password):
        assert rejection["status"] == "warning" and rejection["checked"] is False
        # Credential-bearing endpoints are refused before any request; only the
        # coarse category is reported, never the host, user or URL.
        assert rejection["reason"] == "credentials_present"
    assert bad_port["reason"] == "invalid_port"
    assert remote["reason"] == "not_loopback"
    for report in (empty_user, empty_password, bad_port, remote):
        assert "10.0.0.5" not in json.dumps(report)
        assert "user" not in json.dumps(report)


def test_doctor_relative_ser_model_resolves_against_project_root(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    model = tmp_path / "models" / "ser" / "model.onnx"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"fake")

    inside = workbench_doctor.describe_path(Path("models/ser/model.onnx"), tmp_path)
    outside = workbench_doctor.describe_path(tmp_path.parent / "elsewhere.onnx", tmp_path)

    assert inside == {"location": "models/ser/model.onnx", "exists": True, "bytes": 4, "probed": True}
    assert outside == {
        "location": "<outside-project>",
        "exists": False,
        "bytes": None,
        "probed": True,
    }


def test_doctor_uses_resolved_ser_path_from_diagnostics_without_leaking(tmp_path) -> None:
    def collect(_cfg):
        return {
            "paths": {
                "ser_model": {"path": "C:\\Users\\somebody\\secret\\ser.bin", "exists": True},
                "piper_executable": {"path": "x", "exists": False},
                "piper_model": {"path": "y", "exists": False},
            },
            "audio": {},
        }

    report = workbench_doctor.collect_doctor_report(
        collect=collect, project_root=PROJECT_ROOT, python_version=(3, 11, 9)
    )
    serialized = json.dumps(report)

    assert report["models"]["ser_model"] == {
        "location": "<outside-project>",
        "exists": True,
        "bytes": None,
        "probed": Path(r"C:\Users\somebody\secret\ser.bin").is_absolute(),
    }
    assert "secret" not in serialized
    assert "C:\\Users" not in serialized


def test_baseline_rejects_inconsistent_summary_and_unknown_metrics(tmp_path) -> None:
    negative = _write_json(
        tmp_path / "neg-summary.json",
        {"summary": {"endpoint_to_playback_ms": {"count": 2, "min": -1.0, "p50": 5.0}}},
    )
    fractional_count = _write_json(
        tmp_path / "frac-count.json",
        {"summary": {"endpoint_to_playback_ms": {"count": 1.5, "p50": 5.0}}},
    )
    inconsistent = _write_json(
        tmp_path / "inconsistent.json",
        {
            "summary": {
                "asr_final_ms": {"count": 2, "min": 10.0, "p50": 5.0, "p95": 20.0, "max": 30.0}
            }
        },
    )
    unknown_metric = _write_json(
        tmp_path / "unknown-metric.json",
        {"summary": {"evil_metric_with_token": {"count": 1, "p50": 1.0}}},
    )

    for path in (negative, fractional_count, inconsistent):
        with pytest.raises(workbench_latency.BaselineError):
            workbench_latency.capture_baseline(
                path, measurement="real", label="x", root=PROJECT_ROOT
            )

    document, notes = workbench_latency.capture_baseline(
        unknown_metric, measurement="real", label="x", root=PROJECT_ROOT
    )
    assert document["summary"] == {}
    assert document["ignored_key_count"] == 1
    assert "evil_metric_with_token" not in json.dumps(document)
    assert all("evil_metric" not in note for note in notes)


def test_baseline_unknown_keys_marks_and_turn_ids_are_not_copied(tmp_path) -> None:
    payload = {
        "summary": {"endpoint_to_playback_ms": {"count": 1, "p50": 3.0}},
        "secret_token_key": "Bearer abc123",
        "turns": [
            {
                "turn_id": "turn-with-a-secret-path-/etc/passwd",
                "marks": {"turn_started": 0.0, "secret_mark_key": 1.0},
                "latencies_ms": {"endpoint_to_playback_ms": 100.0, "totally_unknown_ms": 5.0},
            },
            {
                "turn_id": "b98fbce5550a44289a1407789a0c4ea3",
                "marks": {"turn_started": 1.0},
                "latencies_ms": {"endpoint_to_playback_ms": 200.0},
            },
        ],
    }
    metrics = _write_json(tmp_path / "metrics.json", payload)

    document, notes = workbench_latency.capture_baseline(
        metrics, measurement="real", label="safe-label", root=PROJECT_ROOT
    )
    serialized = json.dumps(document)

    assert "Bearer abc123" not in serialized
    assert "secret_token_key" not in serialized
    assert "secret_mark_key" not in serialized
    assert "totally_unknown_ms" not in serialized
    assert "passwd" not in serialized
    assert "/etc/passwd" not in serialized
    # Three dropped fields; replacing the unsafe ID is tracked separately.
    assert document["ignored_key_count"] == 3
    assert document["renamed_turn_ids"] == 1
    assert document["per_turn"][0]["turn_id"] == "turn-1"
    assert document["per_turn"][1]["turn_id"] == "b98fbce5550a44289a1407789a0c4ea3"
    assert "ignored 1" in " ".join(notes)
    assert "totally_unknown" not in " ".join(notes)
    assert "secret" not in " ".join(notes)


def test_baseline_label_and_config_strings_are_restricted(tmp_path) -> None:
    metrics = _write_json(tmp_path / "metrics.json", _fake_metrics())

    for bad_label in ("C:\\Users\\x\\secret", "http://host/path", "token=abc", "a" * 80):
        with pytest.raises(workbench_latency.BaselineError):
            workbench_latency.capture_baseline(
                metrics, measurement="real", label=bad_label, root=PROJECT_ROOT
            )

    cfg = AppConfig()
    cfg.asr_model = "C:\\Users\\x\\secret\\model.bin"
    cfg.llm_model = "http://user:pw@host/model"
    document, _ = workbench_latency.capture_baseline(
        metrics, measurement="real", label="ok-label", cfg=cfg, root=PROJECT_ROOT
    )
    serialized = json.dumps(document)

    assert "secret" not in serialized
    assert "user:pw" not in serialized
    assert document["config"]["asr_model"] == "<redacted>"
    assert document["config"]["llm_model"] == "<redacted>"


def test_baseline_environment_is_capture_context_and_comparison_defaults_unknown(tmp_path) -> None:
    metrics = _write_json(tmp_path / "m.json", _fake_metrics())
    first, _ = workbench_latency.capture_baseline(
        metrics, measurement="real", label="a", root=PROJECT_ROOT
    )
    second, _ = workbench_latency.capture_baseline(
        metrics, measurement="real", label="b", root=PROJECT_ROOT
    )

    assert first["capture_context"]["git"]["available"] in {True, False}
    assert first["capture_context"]["python"]
    assert first["capture_context"]["os"]
    assert first["recording_context"] is None
    assert workbench_latency.compare_baselines(second, first)["comparable"] is False

    unknown_measurement = copy.deepcopy(second)
    unknown_measurement["measurement"] = "unknown"
    assert workbench_latency.compare_baselines(unknown_measurement, first)["comparable"] is False


def test_baseline_comparison_requires_matching_recording_context(tmp_path) -> None:
    metrics = _write_json(tmp_path / "m.json", _fake_metrics())
    base, _ = workbench_latency.capture_baseline(
        metrics, measurement="real", label="a", root=PROJECT_ROOT
    )

    def with_context(document, **overrides):
        clone = copy.deepcopy(document)
        context = {
            "git_head": overrides.pop("git_head", "a" * 40),
            "config_fingerprint": clone["config_fingerprint"],
            "python": clone["capture_context"]["python"],
            "os": clone["capture_context"]["os"],
        }
        context.update(overrides)
        clone["recording_context"] = context
        return clone

    previous = with_context(base)
    current = with_context(
        base, git_head="b" * 40, config_fingerprint=base["config_fingerprint"]
    )
    current["summary"]["endpoint_to_playback_ms"]["p50"] += 50.0

    comparable = workbench_latency.compare_baselines(current, previous)
    assert comparable["comparable"] is True
    assert comparable["metrics"]["endpoint_to_playback_ms"]["p50"] == 50.0

    other_os = with_context(base, os="Plan9")
    assert workbench_latency.compare_baselines(other_os, previous)["comparable"] is False

    no_context = copy.deepcopy(current)
    no_context["recording_context"] = None
    assert workbench_latency.compare_baselines(no_context, previous)["comparable"] is False


def test_baseline_subsequent_group_aggregates_all_later_turns(tmp_path) -> None:
    payload = _fake_metrics()
    payload["turns"].append(
        {
            "turn_id": "turn-c",
            "marks": {"turn_started": 60.0},
            "latencies_ms": {"endpoint_to_playback_ms": 5000.0},
        }
    )
    metrics = _write_json(tmp_path / "m.json", payload)
    base, _ = workbench_latency.capture_baseline(
        metrics, measurement="real", label="a", root=PROJECT_ROOT
    )
    context = {
        "git_head": "a" * 40,
        "config_fingerprint": base["config_fingerprint"],
        "python": base["capture_context"]["python"],
        "os": base["capture_context"]["os"],
    }
    previous = copy.deepcopy(base)
    previous["recording_context"] = context
    current = copy.deepcopy(base)
    current["recording_context"] = context
    for turn in current["per_turn"][1:]:
        turn["latencies_ms"]["endpoint_to_playback_ms"] += 1000.0

    comparison = workbench_latency.compare_baselines(current, previous)

    assert comparison["comparable"] is True
    group = comparison["per_turn"]["groups"]["subsequent"]
    assert group["turns_current"] == 2
    assert group["latency_ms"]["endpoint_to_playback_ms"]["count"] == 2
    assert group["latency_ms"]["endpoint_to_playback_ms"]["p50"] == 1000.0
    assert group["latency_ms"]["endpoint_to_playback_ms"]["p95"] == 1000.0


def test_baseline_cli_reports_bad_compare_file_without_traceback(tmp_path, capsys) -> None:
    metrics = _write_json(tmp_path / "m.json", _fake_metrics())
    broken = _write_json(tmp_path / "broken-compare.json", {"schema_version": 2, "turns": 5})
    broken = _write_json(tmp_path / "broken-compare.json", {"turns": 5})
    output = PROJECT_ROOT / "outputs" / "workbench" / "never-written.json"

    code = workbench.main(
        [
            "baseline",
            "--metrics",
            str(metrics),
            "--output",
            output.relative_to(PROJECT_ROOT).as_posix(),
            "--compare",
            str(broken),
        ]
    )
    out = capsys.readouterr().out
    assert code == 2
    assert "baseline error" in out
    assert "Traceback" not in out
    assert not output.exists()


def test_validate_render_hides_interpreter_path_and_rejects_bad_targets(tmp_path) -> None:
    runner = _RecordingRunner([0, 0])
    outcome = workbench_validate.run_validation("offline", runner=runner)
    rendered = workbench_validate.render_outcome(outcome)

    assert sys.executable not in rendered
    assert rendered.count("python -m") == 2

    missing = workbench_validate.resolve_test_targets(
        ["tests/test_workbench.py"], root=PROJECT_ROOT
    )
    assert missing == ["tests/test_workbench.py"]
    with pytest.raises(workbench_validate.ValidationError):
        workbench_validate.resolve_test_targets(["tests/does_not_exist.py"])
    with pytest.raises(workbench_validate.ValidationError):
        workbench_validate.resolve_test_targets(["main.py"])

    with pytest.raises(workbench_validate.ValidationError):
        workbench_validate.build_plan("offline", ["tests/test_workbench.py"])
    with pytest.raises(workbench_validate.ValidationError):
        workbench_validate.build_plan("full", ["tests/test_workbench.py"])


def test_validate_run_plan_reports_process_start_failure_safely() -> None:
    def failing_runner(argv, cwd=None, shell=None, **kwargs):
        raise OSError("cannot start process at C:\\Users\\x\\secret\\python.exe")

    outcome = workbench_validate.run_validation("offline", runner=failing_runner)

    assert outcome.status == "failed"
    assert outcome.commands[0].status == "OSError:process_start"
    assert workbench_validate.exit_code_for(outcome) != 0
    assert "secret" not in workbench_validate.render_outcome(outcome)


def test_snapshot_cli_writes_sanitized_context(tmp_path, capsys) -> None:
    output = PROJECT_ROOT / "outputs" / f"workbench-snapshot-{tmp_path.name}.json"
    created: list[Path] = []
    try:
        code = workbench.main(
            ["snapshot", "--output", output.relative_to(PROJECT_ROOT).as_posix()]
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "snapshot written" in out
        written = sorted(output.parent.glob(output.stem + "*.json"))
        created.extend(written)
        payload = json.loads(written[0].read_text(encoding="utf-8"))
        assert payload["capture_context"]["python"]
        assert payload["capture_context"]["os"]
        assert "config_fingerprint" in payload
        assert payload["status"] == "not_measured"
        assert payload["measurement"] is None
        assert payload["kind"] == workbench_latency.SNAPSHOT_KIND
        assert str(PROJECT_ROOT) not in json.dumps(payload)
    finally:
        for path in created:
            path.unlink(missing_ok=True)
