"""``tools.dsh_client``: minimal, whitelisted dsh session client.

Verified against @deepseek-ai/dsh 0.1.7-rc.2:
``dsh-client-connection/lib/index.js`` posts a ``client-request`` envelope to
``/api/<method>`` and answers with ``server-response.result`` as
``{ok: true, value}`` or ``{ok: false, error}``; the outer payload is always
``{args: {...named parameters...}}`` with the per-method request key
(``_request`` for ``session/list``, ``request`` for ``session/projections`` and
``session/prompt``, empty ``args`` for ``session/modelCatalog``).

Only ``session/list``, ``session/projections``, ``session/prompt`` and the
read-only ``session/modelCatalog`` are addressable. Tokens come from hidden
``getpass`` input only, live in process memory, and are never written to disk,
argv, environment or logs. The network surface is a loopback origin with no
userinfo, query or fragment, no proxy and no automatic redirect.
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import hashlib
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .workbench import PROJECT_ROOT, PathEscapeError, resolve_project_path

DEFAULT_BASE_URL = "http://127.0.0.1:3080"
ALLOWED_METHODS: tuple[str, ...] = (
    "session/list",
    "session/projections",
    "session/prompt",
    "session/modelCatalog",
)
WRITE_METHODS: tuple[str, ...] = ("session/prompt",)
LEDGER_RELATIVE = Path("outputs") / "workbench" / "dsh"
TASK_FILE_ROOTS = ("docs", "outputs")
MAX_TASK_FILE_BYTES = 200_000
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# Outer RPC payload shape per method; verified against the installed service.
_RPC_ARG_KEYS: dict[str, str | None] = {
    "session/list": "_request",
    "session/projections": "request",
    "session/prompt": "request",
    "session/modelCatalog": None,
}

_TOKEN_USAGE_FIELDS = (
    "uncachedInputTokens",
    "cacheReadTokens",
    "cacheWriteTokens",
    "outputTokens",
    "inputTokens",
    "totalTokens",
)


class DshClientError(ValueError):
    """Raised for unsafe or invalid client use; messages never echo secrets."""


def _safe_type(exc: BaseException) -> str:
    return f"{type(exc).__name__}"


# --------------------------------------------------------------------------- #
# transport
# --------------------------------------------------------------------------- #
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


@dataclass
class HttpTransport:
    """In-memory cookie transport with proxy and redirect protection."""

    base_url: str = DEFAULT_BASE_URL
    timeout: float = 15.0
    opener: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.base_url = validate_base_url(self.base_url)
        self.jar = http.cookiejar.CookieJar()
        if self.opener is None:
            self.opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}),
                _NoRedirect(),
                urllib.request.HTTPCookieProcessor(self.jar),
            )

    def request(self, url: str, *, data: bytes | None = None, method: str = "GET"):
        request = urllib.request.Request(url=url, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        return self.opener.open(request, timeout=self.timeout)


def validate_base_url(url: Any) -> str:
    if isinstance(url, str) and "://" not in url:
        url = f"http://{url}"
    try:
        parts = urllib.parse.urlsplit(os.fspath(url))
    except (TypeError, ValueError) as exc:
        raise DshClientError("base url is not parsable") from exc
    if parts.scheme not in {"http", "https"}:
        raise DshClientError("base url must use http or https")
    host = (parts.hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise DshClientError("base url must be loopback")
    if parts.username is not None or parts.password is not None:
        raise DshClientError("base url must not carry credentials")
    if parts.query or parts.fragment:
        raise DshClientError("base url must not carry query or fragment")
    try:
        port = parts.port
    except ValueError as exc:
        raise DshClientError("base url port is invalid") from exc
    if port is None:
        port = 3080
    literal = f"[{host}]" if ":" in host else host
    return urllib.parse.urlunsplit((parts.scheme, f"{literal}:{port}", "", "", ""))


def _is_same_origin_root(location: str, base_url: str) -> bool:
    try:
        target = urllib.parse.urlsplit(urllib.parse.urljoin(base_url + "/", location))
        origin = urllib.parse.urlsplit(base_url)
    except ValueError:
        return False
    if (target.scheme, target.hostname, target.port) != (origin.scheme, origin.hostname, origin.port):
        return False
    if target.username is not None or target.password is not None:
        return False
    if target.query or target.fragment:
        return False
    return target.path in {"", "/"}


class DshSession:
    """One authenticated connection; the token never leaves this object."""

    def __init__(
        self,
        base_url: Any = DEFAULT_BASE_URL,
        *,
        transport: HttpTransport | None = None,
        prompt_token: Callable[[], str] | None = None,
    ) -> None:
        self.base_url = validate_base_url(base_url)
        self.transport = transport or HttpTransport(self.base_url)
        self._prompt_token = prompt_token or (
            lambda: getpass.getpass("Local dsh authentication token (hidden): ")
        )
        self.authenticated = False

    # -- authentication ---------------------------------------------------- #
    def authenticate(self) -> bool:
        token = self._prompt_token().strip()
        if not token:
            raise DshClientError("no token supplied")
        query = urllib.parse.urlencode({"token": token})
        url = f"{self.base_url}/?{query}"
        try:
            response = self.transport.request(url)
        except urllib.error.HTTPError as exc:
            if exc.code != 303:
                raise DshClientError(f"authentication failed: {_safe_type(exc)}") from exc
            location = exc.headers.get("Location", "") if exc.headers else ""
            if not _is_same_origin_root(location, self.base_url):
                raise DshClientError("token exchange redirect was not same-origin root") from exc
            self.authenticated = True
            return True
        except Exception as exc:  # noqa: BLE001 - category only
            raise DshClientError(f"authentication failed: {_safe_type(exc)}") from exc
        finally:
            token = ""
            query = ""
            url = ""
        try:
            self.authenticated = True
            return True
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    # -- rpc --------------------------------------------------------------- #
    def rpc(
        self,
        method: str,
        parameters: Mapping[str, Any] | None = None,
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        if method not in ALLOWED_METHODS:
            raise DshClientError("method is outside the whitelist")
        arg_key = _RPC_ARG_KEYS[method]
        payload: dict[str, Any] = {"args": {} if arg_key is None else {arg_key: dict(parameters or {})}}
        rpc_id = request_id or uuid.uuid4().hex
        envelope = {
            "type": "client-request",
            "rpcId": rpc_id,
            "method": method,
            "payload": payload,
        }
        encoded = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        try:
            response = self.transport.request(
                f"{self.base_url}/api/{method}", data=encoded, method="POST"
            )
            try:
                raw = response.read()
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
            body = json.loads(raw.decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            return {"ok": False, "error": {"code": f"http/{exc.code}"}, "rpc_id": rpc_id}
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "error": {"code": f"transport/{_safe_type(exc)}"},
                "rpc_id": rpc_id,
            }
        result = body.get("result") if isinstance(body, Mapping) else None
        if not isinstance(result, Mapping):
            return {"ok": False, "error": {"code": "invalid-response"}, "rpc_id": rpc_id}
        if result.get("ok"):
            return {"ok": True, "value": result.get("value"), "rpc_id": rpc_id}
        error = result.get("error") if isinstance(result.get("error"), Mapping) else {}
        return {"ok": False, "error": {"code": _error_category(error)}, "rpc_id": rpc_id}


def _error_category(error: Mapping[str, Any]) -> str:
    """Map a server error to a coarse, non-echoing category."""

    code = str(error.get("code", "")).lower()
    if "busy" in code or "conflict" in code or "writer-held" in code:
        return "conflict"
    if "invalid" in code or "bad-request" in code:
        return "invalid-request"
    if "not-found" in code or "unavailable" in code:
        return "unavailable"
    if "credentials" in code:
        return "credentials"
    if "timeout" in code:
        return "timeout"
    return "rpc-error"


# --------------------------------------------------------------------------- #
# session selection and status
# --------------------------------------------------------------------------- #
def _canonical(path: Any) -> str | None:
    if path is None:
        return None
    try:
        raw = os.fspath(path)
    except TypeError:
        return None
    if not isinstance(raw, (str, bytes)):
        return None
    if not raw:
        return None
    try:
        return os.path.normcase(str(Path(raw).resolve()))
    except (OSError, ValueError):
        return None


def list_sessions(session: Any) -> list[dict[str, Any]]:
    result = session.rpc("session/list")
    if not result.get("ok"):
        raise DshClientError("session list failed")
    value = result.get("value")
    items = value.get("items") if isinstance(value, Mapping) else None
    if not isinstance(items, list):
        raise DshClientError("session list shape was unexpected")
    return [item for item in items if isinstance(item, Mapping)]


def find_session(
    session: Any,
    session_id: str | None,
    *,
    project_root: Path | None = None,
) -> dict[str, Any] | None:
    """Return a matching session summary, or None when the id is not present.

    Raises when an explicit id exists but belongs to another workspace, so the
    refusal is never silent.
    """

    root = _canonical(project_root if project_root is not None else PROJECT_ROOT)
    for item in list_sessions(session):
        candidate_id = item.get("sessionId") or item.get("session_id")
        if session_id is not None and candidate_id != session_id:
            continue
        if root is None or _canonical(item.get("cwd")) != root:
            continue
        return dict(item)
    return None


def select_project_session(
    session: Any,
    session_id: str | None,
    *,
    project_root: Path | None = None,
) -> dict[str, Any]:
    found = find_session(session, session_id, project_root=project_root)
    if found is None:
        if session_id is not None:
            raise DshClientError("requested session is not an existing session of this project")
        raise DshClientError("no existing session matches this project workspace")
    cwd = found.get("sessionId") or found.get("session_id")
    return {
        "session_id": str(cwd),
        "running": bool(found.get("running")),
        "updated_at": found.get("updatedAt") or found.get("updated_at"),
        "projections_kind": (
            found.get("projections", {}).get("kind")
            if isinstance(found.get("projections"), Mapping)
            else None
        ),
        "cwd_matches": True,
    }


def summarize_projections(value: Any) -> dict[str, Any]:
    """Curated projection facts; inbox bodies and turn outlines are dropped."""

    summary: dict[str, Any] = {
        "model": None,
        "token_usage": None,
        "permissions": None,
        "session_stats": None,
        "queue_count": None,
        "queue_known": False,
    }
    if not isinstance(value, Mapping):
        return summary
    values = value.get("values")
    if not isinstance(values, Mapping):
        return summary

    selection = values.get("modelSelection")
    if isinstance(selection, Mapping):
        chosen = selection.get("next") or selection.get("lastUsed")
        if isinstance(chosen, Mapping):
            summary["model"] = {
                "provider": chosen.get("provider"),
                "model": chosen.get("model"),
                "effort": chosen.get("reasoningEffort"),
            }

    usage = values.get("tokenUsage")
    if isinstance(usage, Mapping):
        summary["token_usage"] = {
            key: usage.get(key) for key in _TOKEN_USAGE_FIELDS if key in usage
        }

    permissions = values.get("permissions")
    if isinstance(permissions, Mapping):
        current = permissions.get("currentValue", permissions.get("current"))
        summary["permissions"] = {"currentValue": current} if current is not None else {
            key: permissions.get(key)
            for key in ("mode", "profile", "level")
            if key in permissions
        }

    stats = values.get("sessionStats")
    if isinstance(stats, Mapping):
        summary["session_stats"] = {
            key: item
            for key, item in stats.items()
            if isinstance(item, (int, float)) and not isinstance(item, bool)
        }

    inbox = values.get("inbox")
    if isinstance(inbox, Mapping):
        next_turn = inbox.get("next-turn")
        next_step = inbox.get("next-step")
        if isinstance(next_turn, list) and isinstance(next_step, list):
            summary["queue_count"] = len(next_turn) + len(next_step)
            summary["queue_known"] = True
    return summary


def session_status(
    session: Any,
    session_id: str,
    *,
    project_root: Path | None = None,
) -> dict[str, Any]:
    try:
        found = find_session(session, session_id, project_root=project_root)
    except DshClientError:
        found = None
    if found is None:
        raise DshClientError("requested session is not an existing session of this project")
    bound_id = str(found.get("sessionId") or found.get("session_id"))
    status: dict[str, Any] = {
        "session_id": bound_id,
        "running": bool(found.get("running")),
        "updated_at": found.get("updatedAt") or found.get("updated_at"),
        "cwd_matches": True,
        "projection_available": False,
        "model": None,
        "token_usage": None,
        "permissions": None,
        "session_stats": None,
        "queue_count": None,
        "queue_known": False,
    }
    projections = session.rpc("session/projections", {"sessionId": bound_id})
    if projections.get("ok"):
        status.update(summarize_projections(projections.get("value")))
        status["projection_available"] = True
    return status


# --------------------------------------------------------------------------- #
# ledger + dispatch
# --------------------------------------------------------------------------- #
def safe_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_ID_PATTERN.fullmatch(value))


def ledger_path(dispatch_id: str, *, root: Path | None = None) -> Path:
    base = (Path(root) if root is not None else PROJECT_ROOT).resolve()
    if not safe_id(dispatch_id):
        raise DshClientError("dispatch id must be a plain safe basename")
    ledger_root = base / LEDGER_RELATIVE
    resolved_root = ledger_root.resolve()
    if resolved_root != base and base not in resolved_root.parents:
        raise DshClientError("ledger root escapes the project")
    resolved_root.mkdir(parents=True, exist_ok=True)
    candidate = resolved_root / f"{dispatch_id}.json"
    if candidate.parent != resolved_root:
        raise DshClientError("ledger path escapes the ledger root")
    return candidate


def _write_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "x", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, ensure_ascii=False, indent=2, sort_keys=True)
    except FileExistsError as exc:
        raise DshClientError("dispatch ledger already exists for this id") from exc


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def validate_task_file(relative: Any, *, root: Path | None = None) -> tuple[Path, str, str]:
    """Return (path, sha256, text) reading the file exactly once."""

    base = (Path(root) if root is not None else PROJECT_ROOT).resolve()
    try:
        raw_path = base / os.fspath(relative)
        resolved = resolve_project_path(os.fspath(relative), root=base)
    except (PathEscapeError, OSError) as exc:
        raise DshClientError("task file must live inside the project") from exc
    if any(part.is_symlink() for part in (raw_path, *raw_path.parents) if part != base):
        raise DshClientError("task file must not be a symlink")
    try:
        inside = resolved.relative_to(base).as_posix()
    except ValueError as exc:
        raise DshClientError("task file must live inside the project") from exc
    if not inside.startswith(tuple(f"{prefix}/" for prefix in TASK_FILE_ROOTS)):
        raise DshClientError("task file must live under docs/ or outputs/")
    if resolved.suffix.lower() != ".md":
        raise DshClientError("task file must be markdown")
    if not resolved.is_file():
        raise DshClientError("task file does not exist")
    if resolved.stat().st_size > MAX_TASK_FILE_BYTES:
        raise DshClientError("task file is too large")
    try:
        text = resolved.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise DshClientError(f"task file unreadable: {_safe_type(exc)}") from exc
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return resolved, digest, text


def _load_ledger(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise DshClientError("dispatch ledger is unreadable") from exc
    if not isinstance(payload, dict):
        raise DshClientError("dispatch ledger has an unexpected shape")
    return payload


def _process_alive(pid: Any) -> str:
    """Reuse the cross-platform read-only process probe."""
    from .workbench_tasks import process_status
    return process_status(pid)


@contextlib.contextmanager
def _file_lock(lock_path: Path):
    """OS advisory lock, released even on process death; never force-cleared."""

    if lock_path.is_symlink() or lock_path.resolve().parent != lock_path.parent:
        raise DshClientError("dispatch lock escapes the ledger root")
    with open(lock_path, "a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise DshClientError("dispatch is already in progress or lock is unavailable") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def _dispatch_lock(ledger: Path):
    """Per-ledger lock; kept for compatibility with dispatch-id-level callers."""

    with _file_lock(ledger.with_suffix(".lock")):
        yield


def session_lock_name(session_id: str) -> str:
    """Sidecar lock name for one session; never expressible as a dispatch id."""

    if not isinstance(session_id, str) or not session_id:
        raise DshClientError("a session id is required for the session lock")
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32]
    # The leading dot keeps the stem outside the dispatch-id alphabet, so no
    # valid dispatch id can ever map onto this lock file.
    return f".session-{digest}.lock"


@contextlib.contextmanager
def session_dispatch_lock(session_id: str, *, root: Path | None = None):
    """One canonical project/session-level OS lock around prompt submission."""

    base = (Path(root) if root is not None else PROJECT_ROOT).resolve()
    lock_root = _ledger_root(base)
    lock_path = lock_root / session_lock_name(session_id)
    if lock_path.parent != lock_root:
        raise DshClientError("session lock escapes the ledger root")
    with _file_lock(lock_path):
        yield


def _ledger_root(base: Path) -> Path:
    ledger_root = base / LEDGER_RELATIVE
    ledger_root.mkdir(parents=True, exist_ok=True)
    resolved_root = ledger_root.resolve()
    if resolved_root != base and base not in resolved_root.parents:
        raise DshClientError("ledger root escapes the project")
    return resolved_root


def dispatch_task(
    session: Any,
    dispatch_id: str,
    task_file: Any,
    *,
    session_id: str,
    project_root: Path | None = None,
    retry: bool = False,
) -> dict[str, Any]:
    """Send one markdown task to an idle project session, at most once."""

    root = (Path(project_root) if project_root is not None else PROJECT_ROOT).resolve()
    ledger = ledger_path(dispatch_id, root=root)
    task_path, digest, text = validate_task_file(task_file, root=root)
    # Fixed lock order: per-dispatch ledger first, then the shared session lock,
    # which covers the queue verification, ledger writes and prompt submission.
    with _dispatch_lock(ledger):
        with session_dispatch_lock(session_id, root=root):
            return _dispatch_locked(
                session,
                dispatch_id,
                task_path,
                digest,
                text,
                session_id=session_id,
                root=root,
                ledger=ledger,
                retry=retry,
            )


def _dispatch_locked(session, dispatch_id, task_path, digest, text, *, session_id, root, ledger, retry):
    existing = _load_ledger(ledger)

    request_id: str | None = None
    created_at = time.time()
    if existing is not None:
        if existing.get("task_sha256") not in {None, digest}:
            raise DshClientError("task file changed since the ledger was written")
        state = str(existing.get("state"))
        if state == "accepted":
            raise DshClientError("already_accepted: this dispatch id already entered the session inbox")
        if state in {"pending", "ambiguous"}:
            if not retry:
                raise DshClientError("dispatch is unresolved; pass an explicit retry to reuse its request id")
            alive = ("dead" if state == "ambiguous" and existing.get("pid") is None
                     else _process_alive(existing.get("pid")))
            if alive != "dead":
                raise DshClientError("dispatch liveness is not confirmed dead; refusing to retry")
            request_id = existing.get("request_id")
            if not isinstance(request_id, str) or not request_id:
                raise DshClientError("recorded request id is unusable")
            created_at = existing.get("created_at") or created_at
        elif state == "rejected":
            if not retry:
                raise DshClientError("rejected dispatch requires an explicit retry")
            request_id = existing.get("request_id")
            if not isinstance(request_id, str) or not request_id:
                raise DshClientError("recorded request id is unusable")
            created_at = existing.get("created_at") or created_at
        else:
            raise DshClientError("dispatch ledger state is not resumable")

    status = session_status(session, session_id, project_root=root)
    bound_id = str(status.get("session_id"))
    if existing is not None and existing.get("session_id") not in {None, bound_id}:
        raise DshClientError("bound session changed; refusing to dispatch")
    if status.get("running"):
        raise DshClientError("the target session is running; refusing to dispatch")
    if not status.get("queue_known"):
        raise DshClientError("session queue state is unknown; refusing to dispatch")
    if status.get("queue_count"):
        raise DshClientError("the target session has queued work; refusing to dispatch")

    if request_id is None:
        request_id = uuid.uuid4().hex
        record = {
            "dispatch_id": dispatch_id,
            "session_id": bound_id,
            "task_file": task_path.relative_to(root).as_posix(),
            "task_sha256": digest,
            "request_id": request_id,
            "state": "pending",
            "created_at": created_at,
            "updated_at": time.time(),
            "pid": os.getpid(),
            "attempts": 0,
            "last_result": None,
        }
        _write_exclusive(ledger, record)
    else:
        record = dict(existing or {})
        record["request_id"] = request_id
        record["session_id"] = bound_id
        record["task_sha256"] = digest
        record["state"] = "pending"
        record["updated_at"] = time.time()
        record["pid"] = os.getpid()
        _write_json_atomic(ledger, record)

    result = session.rpc(
        "session/prompt",
        {
            "sessionId": bound_id,
            "requestId": request_id,
            "mode": "queue",
            "content": [{"type": "text", "text": text}],
        },
        request_id=request_id,
    )
    attempts = int(record.get("attempts", 0)) + 1
    receipt = result.get("value")
    if result.get("ok") and isinstance(receipt, Mapping) and receipt.get("accepted") is True:
        record.update({"state": "accepted", "accepted": True, "attempts": attempts})
    else:
        code = str((result.get("error") or {}).get("code", ""))
        record["accepted"] = False
        record["attempts"] = attempts
        record["state"] = "ambiguous" if result.get("ok") or code.startswith(("transport/", "http/5")) else "rejected"
    record["last_result"] = {"ok": bool(result.get("ok")), "error_category": (result.get("error") or {}).get("code")}
    record["updated_at"] = time.time()
    record["pid"] = None
    _write_json_atomic(ledger, record)
    return record


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.dsh_client")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--console",
        action="store_true",
        help="keep one authenticated connection and run short commands from stdin",
    )
    actions = parser.add_subparsers(dest="action", required=False)

    status = actions.add_parser("status")
    status.add_argument("--session", required=True)
    status.add_argument("--json", action="store_true")

    dispatch = actions.add_parser("dispatch")
    dispatch.add_argument("--id", required=True)
    dispatch.add_argument("--task-file", required=True)
    dispatch.add_argument("--session", required=True)
    dispatch.add_argument("--retry", action="store_true")
    dispatch.add_argument("--json", action="store_true")
    return parser


def _emit(payload: Mapping[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")


_HELP = "commands: status <session> | dispatch <id> <task-file> <session> [retry] | quit"


def run_console(session: DshSession, stream: Any, *, emit: Callable[[str], None] = print) -> int:
    """Authenticated short-command loop; one connection for many commands."""

    emit(_HELP)
    for raw in stream:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        command = parts[0].lower()
        try:
            if command in {"quit", "exit"}:
                return 0
            if command == "status" and len(parts) == 2:
                payload = session_status(session, parts[1])
            elif command == "dispatch" and len(parts) in {4, 5}:
                payload = dispatch_task(
                    session,
                    parts[1],
                    parts[2],
                    session_id=parts[3],
                    retry=len(parts) == 5 and parts[4] == "retry",
                )
            else:
                emit(f"error: {_HELP}")
                continue
        except DshClientError as exc:
            emit(f"error: {exc}")
            continue
        _emit(payload, False)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    if not args.console and not args.action:
        print("dsh error: choose status, dispatch or --console")
        return 2
    try:
        session = DshSession(args.base_url)
        session.authenticate()
        if args.console:
            if args.action == "status":
                _emit(session_status(session, args.session), bool(getattr(args, "json", False)))
            return run_console(session, sys.stdin)
        if args.action == "status":
            payload = session_status(session, args.session)
        else:
            payload = dispatch_task(
                session, args.id, args.task_file, session_id=args.session, retry=bool(args.retry)
            )
    except DshClientError as exc:
        print(f"dsh error: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001 - category only, never a traceback
        print(f"dsh error: {_safe_type(exc)}")
        return 1
    _emit(payload, bool(getattr(args, "json", False)))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
