"""Round-3 review regressions: session lock, per-turn evidence, boolean counters,
and offline handling of UNC/network paths.

Scratch directories live under ``outputs/workbench/review3-desktop/`` so the
tests do not depend on the system temporary root.
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest

from srtp_voice.config import AppConfig
from tools import dsh_client, workbench, workbench_doctor, workbench_latency

PROJECT_ROOT = workbench.PROJECT_ROOT
SCRATCH_ROOT = PROJECT_ROOT / "outputs" / "workbench" / "review3-desktop"


def _workdir(request, name: str = "root") -> Path:
    base = SCRATCH_ROOT / uuid.uuid4().hex
    root = base / name
    root.mkdir(parents=True, exist_ok=True)
    request.addfinalizer(lambda: shutil.rmtree(base, ignore_errors=True))
    return root


class _SessionRecorder:
    """Wire-level stub recording RPC order; never touches the network."""

    def __init__(self, sessions: list[dict], projection: dict | None = None, on_list=None) -> None:
        self.sessions = sessions
        self.projection = projection or {
            "asOfSeq": 3,
            "values": {"inbox": {"next-turn": [], "next-step": []}},
        }
        self.calls: list[str] = []
        self.on_list = on_list
        self.prompt_receipts: list[dict] = []

    def rpc(self, method, parameters=None, *, request_id=None):  # noqa: ANN001
        self.calls.append(method)
        if method == "session/list":
            if self.on_list is not None:
                self.on_list()
            return {"ok": True, "value": {"items": self.sessions}}
        if method == "session/projections":
            return {"ok": True, "value": self.projection}
        self.prompt_receipts.append(dict(parameters or {}))
        return {"ok": True, "value": {"accepted": True}}


def _session(root: Path, session_id: str = "sess-project") -> dict:
    return {"sessionId": session_id, "running": False, "updatedAt": 1, "cwd": str(root)}


def _task_file(root: Path, name: str = "docs/task.md") -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# 任务\n", encoding="utf-8")
    return name


def _metrics_file(root: Path, payload: dict) -> Path:
    path = root / "metrics.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _baseline(root: Path, payload: dict) -> tuple[dict, list[str]]:
    return workbench_latency.capture_baseline(
        _metrics_file(root, payload), measurement="real", label="r3", root=root
    )


TURN = {
    "turn_id": "t1",
    "marks": {"turn_started": 0.0},
    "latencies_ms": {"endpoint_to_playback_ms": 100.0},
}


# --------------------------------------------------------------------------- #
# 1. session-level dispatch lock
# --------------------------------------------------------------------------- #
def test_session_lock_covers_status_and_prompt(request) -> None:
    """Real OS lock contention: a competing dispatch cannot even read the queue."""

    root = _workdir(request)
    name = _task_file(root)
    competitor = _SessionRecorder([_session(root)])
    refused: list[str] = []

    def contend() -> None:
        try:
            dsh_client.dispatch_task(
                competitor, "r3-competing", name, session_id="sess-project", project_root=root
            )
        except dsh_client.DshClientError as exc:
            refused.append(str(exc))
        else:  # pragma: no cover - the lock must refuse this
            raise AssertionError("a competing dispatch entered the locked session")

    owner = _SessionRecorder([_session(root)], on_list=contend)
    record = dsh_client.dispatch_task(
        owner, "r3-owner", name, session_id="sess-project", project_root=root
    )

    assert record["state"] == "accepted"
    assert refused and "unavailable" in refused[0]
    assert competitor.calls == []
    assert owner.calls.count("session/prompt") == 1
    # The lock is released after the serial attempt: the same lock can be taken.
    with dsh_client.session_dispatch_lock("sess-project", root=root):
        pass


def test_same_session_other_dispatch_id_is_excluded(request) -> None:
    root = _workdir(request)
    name = _task_file(root)
    recorder = _SessionRecorder([_session(root)])

    with dsh_client.session_dispatch_lock("sess-project", root=root):
        with pytest.raises(dsh_client.DshClientError):
            dsh_client.dispatch_task(
                recorder, "r3-b", name, session_id="sess-project", project_root=root
            )
    assert "session/prompt" not in recorder.calls


def test_different_session_is_not_mutually_excluded(request) -> None:
    root = _workdir(request)
    name = _task_file(root)
    holder = _workdir(request, "holder")
    recorder = _SessionRecorder([_session(root)])

    with dsh_client.session_dispatch_lock("sess-somebody-else", root=holder):
        record = dsh_client.dispatch_task(
            recorder, "r3-c", name, session_id="sess-project", project_root=root
        )
    assert record["state"] == "accepted"


def test_dispatch_lock_is_released_after_serial_failure(request) -> None:
    root = _workdir(request)
    name = _task_file(root)
    recorder = _SessionRecorder([_session(root, session_id="other-session")])

    with pytest.raises(dsh_client.DshClientError):
        dsh_client.dispatch_task(
            recorder, "r3-d", name, session_id="sess-project", project_root=root
        )

    with dsh_client.session_dispatch_lock("sess-project", root=root):
        pass  # a second acquisition proves the failed attempt released it


# --------------------------------------------------------------------------- #
# 2. per-turn evidence integrity
# --------------------------------------------------------------------------- #
def test_per_turn_available_requires_non_empty_groups(request) -> None:
    root = _workdir(request)
    document, _ = _baseline(root, {"summary": {}, "turns": [TURN]})
    assert document["per_turn_available"] is True

    bogus = json.loads(json.dumps(document))
    bogus["per_turn"] = []
    with pytest.raises(workbench_latency.BaselineError):
        workbench_latency._validate_comparison_document(bogus, label="current")

    bad_count = json.loads(json.dumps(document))
    bad_count["per_turn_groups"]["subsequent"] = 5
    with pytest.raises(workbench_latency.BaselineError):
        workbench_latency._validate_comparison_document(bad_count, label="current")


def test_per_turn_rejects_unknown_or_missing_groups(request) -> None:
    root = _workdir(request)
    document, _ = _baseline(root, {"summary": {}, "turns": [TURN]})

    unknown = json.loads(json.dumps(document))
    unknown["per_turn"][0]["group"] = "mystery"
    with pytest.raises(workbench_latency.BaselineError):
        workbench_latency._validate_comparison_document(unknown, label="current")

    missing = json.loads(json.dumps(document))
    missing["per_turn"][0]["group"] = None
    with pytest.raises(workbench_latency.BaselineError):
        workbench_latency._validate_comparison_document(missing, label="current")


def test_missing_turns_is_incomparable_not_an_error(request) -> None:
    root = _workdir(request)
    document, notes = _baseline(root, {"summary": {}})

    assert document["per_turn_available"] is False
    assert document["per_turn_groups"] == {"first_observed": 0, "subsequent": 0}
    assert any("per_turn_unavailable" in note for note in notes)
    workbench_latency._validate_comparison_document(document, label="current")


# --------------------------------------------------------------------------- #
# 3. cancelled / failed must be real JSON booleans
# --------------------------------------------------------------------------- #
def test_boolean_counters_reject_non_boolean_types(request) -> None:
    root = _workdir(request)
    for value in ("yes", "false", 1, 0, None, [], {}):
        payload = {"summary": {}, "cancelled": value}
        with pytest.raises(workbench_latency.BaselineError):
            _baseline(root, payload)


def test_boolean_counters_default_and_accept_true_boolean(request) -> None:
    root = _workdir(request)
    document, _ = _baseline(root, {"summary": {}})
    assert document["counters"]["cancelled"] is False
    assert document["counters"]["failed"] is False

    set_true, _ = _baseline(root, {"summary": {}, "cancelled": True, "failed": False})
    assert set_true["counters"]["cancelled"] is True
    assert set_true["counters"]["failed"] is False


def test_comparison_rejects_non_boolean_counters(request) -> None:
    root = _workdir(request)
    document, _ = _baseline(root, {"summary": {}, "turns": [TURN]})
    tampered = json.loads(json.dumps(document))
    tampered["counters"]["cancelled"] = "yes"

    with pytest.raises(workbench_latency.BaselineError):
        workbench_latency._validate_comparison_document(tampered, label="current")


# --------------------------------------------------------------------------- #
# 4. UNC / network namespaces are never probed
# --------------------------------------------------------------------------- #
class _UntouchablePath(type(Path())):
    """Path subclass that records every filesystem probe."""

    probes = 0

    def exists(self, *args, **kwargs):  # noqa: ANN002, ANN003
        type(self).probes += 1
        raise AssertionError("remote path must not be probed")

    def stat(self, *args, **kwargs):  # noqa: ANN002, ANN003
        type(self).probes += 1
        raise AssertionError("remote path must not be probed")

    def is_file(self, *args, **kwargs):  # noqa: ANN002, ANN003
        type(self).probes += 1
        raise AssertionError("remote path must not be probed")


@pytest.mark.parametrize(
    "remote",
    [
        r"\\server\share\model.onnx",
        r"\\?\UNC\server\share\model.onnx",
        "//server/share/voices",
    ],
)
def test_unc_paths_are_classified_without_probing(remote) -> None:
    assert workbench_doctor.is_remote_namespace(remote) is True
    _UntouchablePath.probes = 0
    report = workbench_doctor.describe_path(_UntouchablePath(remote), PROJECT_ROOT)
    assert _UntouchablePath.probes == 0
    assert report["exists"] is None
    assert report["probed"] is False
    assert report["location"] == "<network-path>"


def test_local_paths_still_resolve_inside_project(request) -> None:
    root = _workdir(request)
    model = root / "models" / "ser" / "model.onnx"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"fake")

    inside = workbench_doctor.describe_path(model, root)
    outside = workbench_doctor.describe_path(root.parent / "elsewhere.onnx", root)

    assert inside == {
        "location": "models/ser/model.onnx",
        "exists": True,
        "bytes": 4,
        "probed": True,
    }
    assert outside["probed"] is True
    assert outside["location"] == "<outside-project>"


def test_diagnostics_path_probing_can_be_disabled(monkeypatch) -> None:
    from srtp_voice import diagnostics

    probes: list[str] = []

    def fake_exists(self):  # noqa: ANN001
        probes.append(str(self))
        return False

    monkeypatch.setattr(Path, "exists", fake_exists)
    cfg = AppConfig()
    diagnostics.collect_diagnostics(cfg)

    assert probes, "the default diagnostics behaviour must keep probing local paths"
