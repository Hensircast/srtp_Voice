"""Own pooling tests for the streaming Ollama HTTP session.

Fakes only: no network, no real Ollama, no model. They assert ownership and
lifecycle (lazy creation, one leased session, concurrent fallback, deferred
close) rather than any real latency improvement.
"""

from __future__ import annotations

import json
import threading

import pytest

from srtp_voice import llm as llm_module
from srtp_voice.config import AppConfig
from srtp_voice.llm import StrategyGenerator
from srtp_voice.types import EmotionResult


class _HTTPError(Exception):
    def __init__(self, message: str, response=None) -> None:
        super().__init__(message)
        self.response = response
        self.status_code = 500
        self.text = "upstream failure"


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.closed = 0

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload

    def close(self) -> None:
        self.closed += 1


class _FakeSession:
    """Records construction and every call so ownership can be asserted."""

    instances: list["_FakeSession"] = []
    fail_construction = False

    def __init__(self) -> None:
        if _FakeSession.fail_construction:
            raise RuntimeError("synthetic session construction failure")
        self.calls: list[tuple[str, str]] = []
        self.closed = 0
        self._tags = _Response({"models": [{"name": "test-model"}]})
        self._stream = _Response({"unused": True})
        _FakeSession.instances.append(self)

    def get(self, url, **kwargs):
        self.calls.append(("get", url))
        return self._tags

    def post(self, url, **kwargs):
        self.calls.append(("post", url))
        return _StreamResponse()

    def close(self) -> None:
        self.closed += 1


class _StreamResponse:
    def __init__(self) -> None:
        self.closed = 0
        self.iterators = 0
        self.release = threading.Event()
        self.started = threading.Event()

    def raise_for_status(self) -> None:
        return None

    def close(self) -> None:
        self.closed += 1

    def iter_content(self, chunk_size=None):
        self.iterators += 1
        response = self

        class _Iterator:
            def __init__(self) -> None:
                self.index = 0
                self.closed = False

            def __iter__(self):
                return self

            def __next__(self) -> bytes:
                if self.index == 0:
                    response.started.set()
                    response.release.wait(3)
                    self.index += 1
                    return (
                        json.dumps({"message": {"content": "可以。"}, "done": False}) + "\n"
                    ).encode("utf-8")
                if self.index == 1:
                    self.index += 1
                    return (
                        json.dumps({"message": {"content": ""}, "done": True}) + "\n"
                    ).encode("utf-8")
                raise StopIteration

            def close(self) -> None:
                self.closed = True

        return _Iterator()


class _Module:
    """Stand-in for the requests module (no session reuse)."""

    Timeout = TimeoutError
    RequestException = OSError
    HTTPError = _HTTPError
    Session = _FakeSession

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return _Response({"models": [{"name": "test-model"}]})

    def post(self, url, **kwargs):
        self.calls.append(url)
        return _StreamResponse()


@pytest.fixture(autouse=True)
def _reset_sessions():
    _FakeSession.instances = []
    _FakeSession.fail_construction = False
    yield
    _FakeSession.instances = []
    _FakeSession.fail_construction = False


def _install(monkeypatch) -> _Module:
    module = _Module()
    monkeypatch.setattr(llm_module, "requests", module)
    monkeypatch.setattr(StrategyGenerator, "_check_ollama", lambda self, req=None: None)
    return module


def _generator(**overrides) -> StrategyGenerator:
    return StrategyGenerator(AppConfig(llm_backend="ollama", **overrides))


def _emotion() -> EmotionResult:
    return EmotionResult("neutral", 0.2, 0.8, {})


def test_plain_generator_never_creates_a_session(monkeypatch) -> None:
    module = _install(monkeypatch)
    generator = _generator()

    assert generator._http_client is None
    chunks = list(generator.generate_stream("建议？", _emotion(), [], turn_id="t"))
    assert [chunk.text for chunk in chunks if chunk.text] == ["可以。"]
    assert _FakeSession.instances == []
    assert module.calls  # the module itself served the request


def test_enable_reuse_is_lazy_and_reuses_one_session(monkeypatch) -> None:
    _install(monkeypatch)
    generator = _generator()

    assert generator.enable_http_reuse() is True
    assert generator._http_client is None  # still lazy

    for turn in range(3):
        chunks = list(generator.generate_stream("建议？", _emotion(), [], turn_id=f"t{turn}"))
        assert [chunk.text for chunk in chunks if chunk.text] == ["可以。"]

    assert len(_FakeSession.instances) == 1
    session = _FakeSession.instances[0]
    assert [kind for kind, _url in session.calls].count("post") == 3
    assert session.closed == 0
    generator.close()
    assert session.closed == 1


def test_close_is_idempotent_and_blocks_new_streams(monkeypatch) -> None:
    _install(monkeypatch)
    generator = _generator()
    generator.enable_http_reuse()
    list(generator.generate_stream("建议？", _emotion(), [], turn_id="t"))
    session = _FakeSession.instances[0]

    generator.close()
    generator.close()
    assert session.closed == 1
    assert generator.enable_http_reuse() is False

    with pytest.raises(RuntimeError):
        list(generator.generate_stream("建议？", _emotion(), [], turn_id="t2"))
    assert session.closed == 1


def test_concurrent_stream_falls_back_to_the_module(monkeypatch) -> None:
    module = _install(monkeypatch)
    generator = _generator()
    generator.enable_http_reuse()

    first = generator.generate_stream("建议？", _emotion(), [], turn_id="a")
    assert next(first).text == "可以。"
    session = _FakeSession.instances[0]

    # The pooled session is leased by the first stream, so a second stream must
    # use independent module requests instead of sharing it.
    second = generator.generate_stream("建议？", _emotion(), [], turn_id="b")
    second_chunks = list(second)
    assert [chunk.text for chunk in second_chunks if chunk.text] == ["可以。"]
    assert module.calls

    first.close()
    assert session.closed == 0
    generator.close()
    assert session.closed == 1


def test_close_while_streaming_is_deferred_until_the_lease_returns(monkeypatch) -> None:
    _install(monkeypatch)
    generator = _generator()
    generator.enable_http_reuse()

    stream = generator.generate_stream("建议？", _emotion(), [], turn_id="t")
    assert next(stream).text == "可以。"
    session = _FakeSession.instances[0]

    generator.close()  # leased: must not close the session under the reader
    assert session.closed == 0

    stream.close()  # lease returns, deferred close happens once
    assert session.closed == 1
    assert generator._http_client is None
    generator.close()
    assert session.closed == 1


def test_session_construction_failure_is_not_silently_downgraded(monkeypatch) -> None:
    _install(monkeypatch)
    _FakeSession.fail_construction = True
    generator = _generator()
    generator.enable_http_reuse()

    # A failed construction must not leave a half-owned session or a stuck
    # lease, and it must not silently pretend pooling works.
    try:
        list(generator.generate_stream("建议？", _emotion(), [], turn_id="t"))
    except Exception:
        pass
    assert generator._http_client is None
    assert generator._http_busy is False

    # The generator stays usable: a later attempt constructs its session.
    _FakeSession.fail_construction = False
    chunks = list(generator.generate_stream("建议？", _emotion(), [], turn_id="t2"))
    assert [chunk.text for chunk in chunks if chunk.text] == ["可以。"]
    assert len(_FakeSession.instances) == 1
    generator.close()


def test_wrapper_failure_closes_the_fresh_session(monkeypatch) -> None:
    _install(monkeypatch)
    created: list[_FakeSession] = []

    class _BrokenClient:
        def __init__(self, session, module):
            created.append(session)
            raise RuntimeError("synthetic wrapper failure")

    monkeypatch.setattr(llm_module, "_PooledHTTPClient", _BrokenClient)
    generator = _generator()
    generator.enable_http_reuse()

    with pytest.raises(Exception):
        list(generator.generate_stream("建议？", _emotion(), [], turn_id="t"))

    assert len(created) == 1
    assert created[0].closed == 1  # the freshly created session is not leaked
    assert generator._http_client is None
    assert generator._http_busy is False


def test_config_switch_and_startup_helper(monkeypatch) -> None:
    import main as main_module

    from srtp_voice import config as config_module

    monkeypatch.setattr(config_module, "load_dotenv", None)
    monkeypatch.delenv("STREAM_LLM_REUSE_HTTP", raising=False)
    assert AppConfig().stream_llm_reuse_http is True
    assert AppConfig.from_env().stream_llm_reuse_http is True
    monkeypatch.setenv("STREAM_LLM_REUSE_HTTP", "0")
    assert AppConfig.from_env().stream_llm_reuse_http is False
    monkeypatch.setenv("STREAM_LLM_REUSE_HTTP", "maybe")
    assert AppConfig.from_env().stream_llm_reuse_http is False

    _install(monkeypatch)
    generator = _generator()
    assert main_module._maybe_enable_llm_http_reuse(AppConfig(llm_backend="ollama"), generator, streaming=True) is True
    assert generator._http_reuse_enabled is True

    disabled = _generator()
    assert main_module._maybe_enable_llm_http_reuse(
        AppConfig(llm_backend="ollama", stream_llm_reuse_http=False), disabled, streaming=True
    ) is False
    assert main_module._maybe_enable_llm_http_reuse(
        AppConfig(llm_backend="ollama"), disabled, streaming=False
    ) is False
    assert main_module._maybe_enable_llm_http_reuse(
        AppConfig(llm_backend="mock"), disabled, streaming=True
    ) is False


def test_missing_hook_reports_false() -> None:
    import main as main_module

    class _Plain:
        pass

    assert main_module._maybe_enable_llm_http_reuse(
        AppConfig(llm_backend="ollama"), _Plain(), streaming=True
    ) is False
