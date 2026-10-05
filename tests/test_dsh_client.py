"""Offline tests for the whitelisted dsh client (no network, no real tokens).

Protocol shapes follow the installed @deepseek-ai/dsh 0.1.7-rc.2 service:
outer payload ``{args: {...}}`` and per-method request keys. Scratch
directories live under ``outputs/workbench/pytest-local/`` so the tests do not
depend on the system temporary root being writable.
"""

from __future__ import annotations

import io
import json
import shutil
import urllib.error
import uuid
from pathlib import Path
from typing import Any

import pytest

from tools import dsh_client, workbench

PROJECT_ROOT = workbench.PROJECT_ROOT


def _workdir(request, name: str = "root") -> Path:
    base = PROJECT_ROOT / "outputs" / "workbench" / "pytest-local" / uuid.uuid4().hex
    root = base / name
    root.mkdir(parents=True, exist_ok=True)
    request.addfinalizer(lambda: shutil.rmtree(base, ignore_errors=True))
    return root


class _FakeResponse:
    def __init__(self, payload: Any = None) -> None:
        self._payload = payload
        self.closed = False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def close(self) -> None:
        self.closed = True


class _FakeOpener:
    def __init__(self, responses: list[Any] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[dict] = []
        self.last_response: _FakeResponse | None = None

    def open(self, request, timeout=None):  # noqa: ANN001
        self.calls.append(
            {
                "url": request.full_url,
                "method": request.get_method(),
                "body": request.data.decode("utf-8") if request.data else None,
            }
        )
        item = self.responses.pop(0) if self.responses else {"result": {"ok": True, "value": {}}}
        if isinstance(item, Exception):
            raise item
        self.last_response = _FakeResponse(item)
        return self.last_response


def _session(opener: _FakeOpener, token: str = "unit-test-token-value") -> dsh_client.DshSession:
    transport = dsh_client.HttpTransport("http://127.0.0.1:3080", opener=opener)
    return dsh_client.DshSession(
        "http://127.0.0.1:3080", transport=transport, prompt_token=lambda: token
    )


def _wire_ok(value: Any) -> dict:
    return {"type": "server-response", "rpcId": "x", "result": {"ok": True, "value": value}}


def _wire_error(code: str) -> dict:
    return {
        "type": "server-response",
        "rpcId": "x",
        "result": {"ok": False, "error": {"code": code, "message": "PRIVATE server text", "details": {}}},
    }


def _project_session(root: Path, **overrides) -> dict:
    item = {
        "sessionId": "sess-project",
        "running": False,
        "updatedAt": 1,
        "cwd": str(root),
        "projections": {"kind": "sequenced"},
    }
    item.update(overrides)
    return item


_WIRE_PROJECTION = {
    "asOfSeq": 9,
    "values": {
        "modelSelection": {
            "next": {"provider": "deepseek", "model": "deepseek-flash", "reasoningEffort": "medium"},
            "lastUsed": {"provider": "deepseek", "model": "deepseek-flash"},
        },
        "permissions": {"currentValue": "never"},
        "tokenUsage": {
            "uncachedInputTokens": 1200,
            "cacheReadTokens": 300,
            "cacheWriteTokens": 40,
            "outputTokens": 250,
        },
        "sessionStats": {"steps": 7, "turns": 3, "note": "not numeric"},
        "inbox": {"next-turn": [], "next-step": []},
        "turnOutline": [{"title": "PRIVATE OUTLINE"}],
    },
}

_WIRE_PROJECTION_WITH_QUEUE = json.loads(json.dumps(_WIRE_PROJECTION))
_WIRE_PROJECTION_WITH_QUEUE["values"]["inbox"] = {
    "next-turn": [{"id": "m1", "text": "PRIVATE CONVERSATION TEXT"}],
    "next-step": [{"id": "m2", "text": "PRIVATE"}],
}


class _PromptRecorder:
    """Session stub returning wire-shaped results keyed by method."""

    def __init__(self, sessions: list[dict], projection: Any = _WIRE_PROJECTION) -> None:
        self.sessions = sessions
        self.projection = projection
        self.calls: list[dict] = []
        self.prompt_result: dict = {"ok": True, "value": {"accepted": True}}

    def rpc(self, method, parameters=None, *, request_id=None):  # noqa: ANN001
        self.calls.append({"method": method, "parameters": parameters, "request_id": request_id})
        if method == "session/list":
            return {"ok": True, "value": {"items": self.sessions}, "rpc_id": request_id}
        if method == "session/projections":
            return {"ok": True, "value": self.projection, "rpc_id": request_id}
        if method == "session/prompt":
            return dict(self.prompt_result, rpc_id=request_id)
        return {"ok": False, "error": {"code": "unsupported"}, "rpc_id": request_id}


def _task_file(root: Path, name: str = "docs/task.md", text: str = "# 任务\n") -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return name


# --------------------------------------------------------------------------- #
# transport, auth and wire contract
# --------------------------------------------------------------------------- #
def test_base_url_must_be_clean_loopback() -> None:
    assert dsh_client.validate_base_url("http://127.0.0.1:3080") == "http://127.0.0.1:3080"
    assert dsh_client.validate_base_url("127.0.0.1") == "http://127.0.0.1:3080"
    assert dsh_client.validate_base_url("http://[::1]:3080") == "http://[::1]:3080"
    for bad in (
        "http://10.0.0.5:3080",
        "ftp://127.0.0.1:3080",
        "http://user:pass@127.0.0.1:3080",
        "http://127.0.0.1:3080?token=abc",
        "http://127.0.0.1:3080/#frag",
    ):
        with pytest.raises(dsh_client.DshClientError):
            dsh_client.validate_base_url(bad)


def test_authenticate_accepts_only_same_origin_root_redirect_and_closes() -> None:
    opener = _FakeOpener([urllib.error.HTTPError("u", 303, "See Other", {"Location": "/"}, None)])
    assert _session(opener).authenticate() is True
    assert opener.calls[0]["url"].startswith("http://127.0.0.1:3080/?")

    for location in ("http://evil.example/", "/?token=leak", "//evil.example/", "/app"):
        failing = _FakeOpener(
            [urllib.error.HTTPError("u", 303, "See Other", {"Location": location}, None)]
        )
        with pytest.raises(dsh_client.DshClientError):
            _session(failing).authenticate()

    ok = _FakeOpener([_wire_ok({})])
    session = _session(ok)
    assert session.authenticate() is True
    assert ok.last_response is not None and ok.last_response.closed is True
    with pytest.raises(dsh_client.DshClientError):
        _session(_FakeOpener(), token="   ").authenticate()


def test_authentication_failure_never_echoes_secret() -> None:
    secret = "unit-test-token-value"
    opener = _FakeOpener([urllib.error.URLError(f"refused for {secret}")])

    with pytest.raises(dsh_client.DshClientError) as excinfo:
        _session(opener, token=secret).authenticate()

    assert secret not in str(excinfo.value)
    assert "URLError" in str(excinfo.value)


def test_rpc_uses_verified_args_envelope_per_method() -> None:
    opener = _FakeOpener([_wire_ok({"items": []}), _wire_ok({"asOfSeq": 1}), _wire_ok({"accepted": True})])
    session = _session(opener)
    session.authenticated = True

    session.rpc("session/list")
    session.rpc("session/projections", {"sessionId": "s1"})
    session.rpc("session/prompt", {"sessionId": "s1", "requestId": "r1", "mode": "queue", "content": []})

    list_body, projections_body, prompt_body = (
        json.loads(call["body"]) for call in opener.calls
    )
    assert set(list_body["payload"]) == {"args"}
    assert list_body["payload"]["args"] == {"_request": {}}
    assert projections_body["payload"]["args"] == {"request": {"sessionId": "s1"}}
    assert prompt_body["payload"]["args"]["request"]["requestId"] == "r1"
    assert list_body["type"] == "client-request"
    assert opener.calls[0]["url"].endswith("/api/session/list")
    assert opener.calls[1]["url"].endswith("/api/session/projections")


def test_rpc_rejects_non_whitelisted_methods() -> None:
    session = _session(_FakeOpener())
    for forbidden in ("session/create", "session/cancel", "session/selectModel", "settings/set"):
        with pytest.raises(dsh_client.DshClientError):
            session.rpc(forbidden, {})


def test_rpc_maps_server_error_to_category_without_message() -> None:
    session = _session(_FakeOpener([_wire_error("session/agent-busy")]))

    result = session.rpc("session/prompt", {"sessionId": "s"})

    assert result["ok"] is False
    assert result["error"]["code"] == "conflict"
    assert "PRIVATE server text" not in json.dumps(result)


def test_rpc_transport_failure_is_a_category() -> None:
    session = _session(_FakeOpener([urllib.error.URLError("connection refused at secret-host")]))

    result = session.rpc("session/list")

    assert result["ok"] is False
    assert result["error"]["code"].startswith("transport/")
    assert "secret-host" not in json.dumps(result)


# --------------------------------------------------------------------------- #
# session selection and status
# --------------------------------------------------------------------------- #
def test_workspace_match_accepts_path_objects(request) -> None:
    workspace = _workdir(request)
    root = workspace / "proj"
    root.mkdir()
    other = workspace / "other"
    other.mkdir()
    recorder = _PromptRecorder(
        [_project_session(other, sessionId="sess-other"), _project_session(root)]
    )

    found = dsh_client.find_session(recorder, None, project_root=root)
    assert found is not None and found["sessionId"] == "sess-project"

    selected = dsh_client.select_project_session(recorder, None, project_root=root)
    assert selected["session_id"] == "sess-project"
    assert selected["cwd_matches"] is True

    status = dsh_client.session_status(recorder, "sess-project", project_root=root)
    assert status["session_id"] == "sess-project"
    assert status["projection_available"] is True

    # A project-only lookup returns None when the id lives in another
    # workspace; the status helper refuses it explicitly.
    assert dsh_client.find_session(recorder, "sess-other", project_root=root) is None
    with pytest.raises(dsh_client.DshClientError):
        dsh_client.session_status(recorder, "sess-other", project_root=root)
    assert dsh_client.find_session(recorder, "sess-other", project_root=other) is not None


def test_status_reads_real_projection_fields_without_leaking_content(request) -> None:
    root = _workdir(request, "status")
    recorder = _PromptRecorder([_project_session(root)], projection=_WIRE_PROJECTION_WITH_QUEUE)

    status = dsh_client.session_status(recorder, "sess-project", project_root=root)
    dumped = json.dumps(status)

    assert status["model"]["model"] == "deepseek-flash"
    assert status["model"]["effort"] == "medium"
    assert status["permissions"]["currentValue"] == "never"
    assert status["token_usage"]["uncachedInputTokens"] == 1200
    assert status["token_usage"]["cacheReadTokens"] == 300
    assert status["token_usage"]["outputTokens"] == 250
    assert status["session_stats"] == {"steps": 7, "turns": 3}
    assert status["queue_count"] == 2
    assert status["queue_known"] is True
    for leaked in ("PRIVATE CONVERSATION TEXT", "PRIVATE OUTLINE", "nextTurn", "next-turn", "turnOutline"):
        assert leaked not in dumped


def test_projection_summary_marks_unknown_queue() -> None:
    summary = dsh_client.summarize_projections({"asOfSeq": 1, "values": {}})

    assert summary["queue_count"] is None
    assert summary["queue_known"] is False
    assert dsh_client.summarize_projections(None)["model"] is None
    assert dsh_client.summarize_projections({"values": {"sessionStats": {"a": "text"}}})[
        "session_stats"
    ] == {}


# --------------------------------------------------------------------------- #
# dispatch ledger
# --------------------------------------------------------------------------- #
def test_task_file_must_be_project_markdown_and_not_symlink(request) -> None:
    root = _workdir(request, "taskfile")
    _task_file(root)
    (root / "outputs").mkdir(exist_ok=True)
    (root / "outputs" / "note.txt").write_text("x", encoding="utf-8")

    path, digest, text = dsh_client.validate_task_file("docs/task.md", root=root)
    assert path.name == "task.md" and digest and text.startswith("#")
    for bad in ("outputs/note.txt", "srtp_voice/config.py", "docs/missing.md", "../outside.md"):
        with pytest.raises(dsh_client.DshClientError):
            dsh_client.validate_task_file(bad, root=root)


def test_dispatch_rejects_duplicate_accepted_and_changed_file(request) -> None:
    root = _workdir(request, "dup")
    name = _task_file(root)
    recorder = _PromptRecorder([_project_session(root)])

    first = dsh_client.dispatch_task(
        recorder, "d-1", name, session_id="sess-project", project_root=root
    )
    assert first["state"] == "accepted"
    assert first["attempts"] == 1
    prompts = [call for call in recorder.calls if call["method"] == "session/prompt"]
    assert len(prompts) == 1

    with pytest.raises(dsh_client.DshClientError) as excinfo:
        dsh_client.dispatch_task(recorder, "d-1", name, session_id="sess-project", project_root=root)
    assert "already_accepted" in str(excinfo.value)
    assert len([call for call in recorder.calls if call["method"] == "session/prompt"]) == 1

    _task_file(root, name, "# changed\n")
    with pytest.raises(dsh_client.DshClientError):
        dsh_client.dispatch_task(recorder, "d-1", name, session_id="sess-project", project_root=root)


def test_dispatch_requires_explicit_retry_and_reuses_request_id(request, monkeypatch) -> None:
    root = _workdir(request, "retry")
    name = _task_file(root)
    recorder = _PromptRecorder([_project_session(root)])
    recorder.prompt_result = {"ok": False, "error": {"code": "transport/timeout"}}
    # The previous attempt's process identity must be provably gone before a
    # retry may reuse its request id; liveness is asserted here explicitly.
    monkeypatch.setattr(dsh_client, "_process_alive", lambda pid: "dead")

    first = dsh_client.dispatch_task(
        recorder, "d-2", name, session_id="sess-project", project_root=root
    )
    assert first["state"] == "ambiguous"
    request_id = first["request_id"]

    with pytest.raises(dsh_client.DshClientError):
        dsh_client.dispatch_task(recorder, "d-2", name, session_id="sess-project", project_root=root)

    recorder.prompt_result = {"ok": True, "value": {"accepted": True}}
    second = dsh_client.dispatch_task(
        recorder, "d-2", name, session_id="sess-project", project_root=root, retry=True
    )

    assert second["state"] == "accepted"
    assert second["request_id"] == request_id
    prompt_calls = [call for call in recorder.calls if call["method"] == "session/prompt"]
    assert [call["request_id"] for call in prompt_calls] == [request_id, request_id]
    assert [call["parameters"]["requestId"] for call in prompt_calls] == [request_id, request_id]
    assert {call["parameters"]["sessionId"] for call in prompt_calls} == {"sess-project"}


def test_dispatch_refuses_running_queued_and_unknown_queue(request) -> None:
    root = _workdir(request, "busy")
    name = _task_file(root)

    running = _PromptRecorder([_project_session(root, running=True)])
    with pytest.raises(dsh_client.DshClientError):
        dsh_client.dispatch_task(running, "d-3", name, session_id="sess-project", project_root=root)
    assert not [call for call in running.calls if call["method"] == "session/prompt"]

    queued = _PromptRecorder([_project_session(root)], projection=_WIRE_PROJECTION_WITH_QUEUE)
    with pytest.raises(dsh_client.DshClientError):
        dsh_client.dispatch_task(queued, "d-3", name, session_id="sess-project", project_root=root)
    assert not [call for call in queued.calls if call["method"] == "session/prompt"]
    unknown = _PromptRecorder([_project_session(root)], projection={"asOfSeq": 1, "values": {}})
    with pytest.raises(dsh_client.DshClientError):
        dsh_client.dispatch_task(unknown, "d-3", name, session_id="sess-project", project_root=root)
    assert not [call for call in unknown.calls if call["method"] == "session/prompt"]


def test_dispatch_refuses_other_workspace_and_unsafe_ids(request) -> None:
    workspace = _workdir(request)
    root = workspace / "proj"
    other = workspace / "other"
    root.mkdir()
    other.mkdir()
    name = _task_file(root)
    recorder = _PromptRecorder([_project_session(other)])

    with pytest.raises(dsh_client.DshClientError):
        dsh_client.dispatch_task(
            recorder, "d-4", name, session_id="sess-other", project_root=root
        )
    assert not [call for call in recorder.calls if call["method"] == "session/prompt"]

    for bad in ("../escape", "a/b", "", "x" * 80):
        with pytest.raises(dsh_client.DshClientError):
            dsh_client.ledger_path(bad, root=root)


def test_ledger_exclusive_creation_blocks_second_writer(request) -> None:
    root = _workdir(request, "exclusive")
    ledger = dsh_client.ledger_path("d-9", root=root)
    dsh_client._write_exclusive(ledger, {"dispatch_id": "d-9", "state": "pending"})

    with pytest.raises(dsh_client.DshClientError):
        dsh_client._write_exclusive(ledger, {"dispatch_id": "d-9", "state": "pending"})

    record = json.loads(ledger.read_text(encoding="utf-8"))
    assert record["state"] == "pending"


def test_dispatch_binds_session_and_records_request_id(request) -> None:
    root = _workdir(request, "bind")
    name = _task_file(root)
    recorder = _PromptRecorder([_project_session(root)])

    record = dsh_client.dispatch_task(
        recorder, "d-5", name, session_id="sess-project", project_root=root
    )

    ledger = root / "outputs" / "workbench" / "dsh" / "d-5.json"
    stored = json.loads(ledger.read_text(encoding="utf-8"))
    assert stored == record
    assert stored["session_id"] == "sess-project"
    assert stored["task_sha256"]
    assert stored["request_id"]
    assert stored["last_result"]["ok"] is True
    assert "error_message" not in json.dumps(stored)


def test_console_loop_reuses_one_session_and_handles_commands(request, monkeypatch) -> None:
    workspace = _workdir(request)
    root = workspace / "proj"
    root.mkdir()
    name = _task_file(root)
    monkeypatch.setattr(dsh_client, "PROJECT_ROOT", root)
    monkeypatch.setattr(workbench, "PROJECT_ROOT", root)
    recorder = _PromptRecorder([_project_session(root)])
    lines = [
        "status sess-project",
        f"dispatch d-6 {name} sess-project",
        "status sess-other",
        "bad command",
        "quit",
    ]

    code = dsh_client.run_console(recorder, io.StringIO("\n".join(lines) + "\n"))

    assert code == 0
    assert len([call for call in recorder.calls if call["method"] == "session/prompt"]) == 1
    assert (root / "outputs" / "workbench" / "dsh" / "d-6.json").exists()
    assert not (PROJECT_ROOT / "outputs" / "workbench" / "dsh" / "d-6.json").exists()


def test_cli_maps_client_error_to_exit_2(monkeypatch, capsys) -> None:
    def failing_authenticate(self):
        raise dsh_client.DshClientError("base url must be loopback")

    monkeypatch.setattr(dsh_client.DshSession, "authenticate", failing_authenticate)

    assert dsh_client.main(["status", "--session", "s"]) == 2
    out = capsys.readouterr().out
    assert "dsh error" in out
    assert "Traceback" not in out
