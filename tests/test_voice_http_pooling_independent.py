"""Independent ownership checks and real loopback HTTP connection counting."""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

import main as main_module
import srtp_voice.llm as llm_module
from srtp_voice.config import AppConfig
from srtp_voice.llm import StrategyGenerator
from srtp_voice.types import EmotionResult
from tools.workbench_latency import _config_view, _fingerprint


EMOTION = EmotionResult("neutral", 0.2, 0.8, {})
TEXT = "直接答复。"


def test_connection_reuse_survives_sanitized_provenance_without_url_keys():
    cfg = AppConfig()
    enabled = _config_view(replace(cfg, stream_llm_reuse_http=True))
    disabled = _config_view(replace(cfg, stream_llm_reuse_http=False))
    assert enabled["stream_llm_reuse_connections"] is True
    assert disabled["stream_llm_reuse_connections"] is False
    assert _fingerprint(enabled) != _fingerprint(disabled)
    assert "http" not in json.dumps(enabled).lower()


def _records():
    return b"".join((json.dumps(row, ensure_ascii=False) + "\n").encode() for row in [
        {"message": {"content": TEXT}, "done": False},
        {"message": {"content": ""}, "done": True},
    ])


class FakeResponse:
    def __init__(self):
        self.closed = 0

    def raise_for_status(self):
        pass

    def json(self):
        return {"models": [{"name": "qwen3:4b-instruct"}]}

    def iter_content(self, chunk_size):
        assert chunk_size == 1
        yield _records()

    def close(self):
        self.closed += 1


class FakeRequests:
    class RequestException(Exception):
        pass

    class Timeout(RequestException):
        pass

    class HTTPError(RequestException):
        pass

    def __init__(self):
        self.sessions = []
        self.responses = []
        self.borrowed_posts = 0
        self.fail_creation = False

    def get(self, *args, **kwargs):
        response = FakeResponse()
        self.responses.append(response)
        return response

    def post(self, *args, **kwargs):
        self.borrowed_posts += 1
        return self.get()

    def Session(self):
        if self.fail_creation:
            raise RuntimeError("factory failed")
        owner = self

        class Session:
            closes = 0
            posts = 0

            def get(self, *args, **kwargs):
                return owner.get()

            def post(self, *args, **kwargs):
                self.posts += 1
                return owner.get()

            def close(self):
                self.closes += 1

        session = Session()
        self.sessions.append(session)
        return session

    def close(self):
        raise AssertionError("must never close the borrowed requests module")


def _fake_generator(monkeypatch):
    requests = FakeRequests()
    monkeypatch.setattr(llm_module, "requests", requests)
    generator = StrategyGenerator(AppConfig(llm_backend="ollama", llm_fallback_to_mock=False))
    return generator, requests


def _stream(generator):
    return generator.generate_stream("公开问题", EMOTION, [])


def test_lazy_enable_and_repeat_use_one_owned_session(monkeypatch):
    generator, req = _fake_generator(monkeypatch)
    generator.enable_http_reuse()
    generator.enable_http_reuse()
    assert req.sessions == []
    try:
        for _ in range(3):
            assert "".join(x.text for x in _stream(generator)) == TEXT
        assert len(req.sessions) == 1
        assert req.sessions[0].posts == 3
        assert req.borrowed_posts == 0
        assert all(response.closed == 1 for response in req.responses)
    finally:
        generator.close()
        generator.close()
    assert req.sessions[0].closes == 1


def test_deferred_close_rejects_new_streams_without_mock_fallback(monkeypatch):
    generator, req = _fake_generator(monkeypatch)
    generator.cfg.llm_fallback_to_mock = True
    generator.enable_http_reuse()
    stream = _stream(generator)
    assert next(stream).text == TEXT
    generator.close()
    assert req.sessions[0].closes == 0
    with pytest.raises(RuntimeError, match="closed"):
        list(_stream(generator))
    stream.close()
    assert req.sessions[0].closes == 1
    generator.close()
    assert req.sessions[0].closes == 1


def test_overlap_uses_independent_borrowed_requests_not_shared_session(monkeypatch):
    generator, req = _fake_generator(monkeypatch)
    generator.enable_http_reuse()
    first = _stream(generator)
    assert next(first).text == TEXT
    try:
        assert "".join(x.text for x in _stream(generator)) == TEXT
        assert req.borrowed_posts == 1
        assert req.sessions[0].posts == 1
        assert req.sessions[0].closes == 0
    finally:
        first.close()
        generator.close()
    assert req.sessions[0].closes == 1


def test_failed_session_factory_does_not_leave_a_busy_lease(monkeypatch):
    generator, req = _fake_generator(monkeypatch)
    generator.enable_http_reuse()
    req.fail_creation = True
    with pytest.raises(RuntimeError, match="factory failed"):
        list(_stream(generator))
    req.fail_creation = False
    try:
        assert "".join(x.text for x in _stream(generator)) == TEXT
        assert len(req.sessions) == 1
        assert req.borrowed_posts == 0
    finally:
        generator.close()


@pytest.mark.parametrize("backend", ["mock", "ollama"])
def test_closed_streaming_generator_cannot_be_reopened_or_hidden_by_fallback(backend):
    generator = StrategyGenerator(AppConfig(llm_backend=backend, llm_fallback_to_mock=True))
    generator.close()
    assert generator.enable_http_reuse() is False
    with pytest.raises(RuntimeError, match="closed"):
        list(_stream(generator))


@pytest.mark.parametrize("failure_stage", ["tts_startup", "turn", "runtime_close"])
def test_main_closes_its_own_generator_on_every_failure(failure_stage, monkeypatch, tmp_path):
    cfg = AppConfig(output_dir=tmp_path, ser_backend="heuristic", llm_backend="ollama")
    args = SimpleNamespace(diagnose=False, continuous=False, streaming=True,
                           mode="vad", text="公开问题", no_play=True)
    closes = []

    class Generator:
        def __init__(self, cfg):
            pass

        def enable_http_reuse(self):
            return True

        def close(self):
            closes.append("generator")

    def fail(stage):
        raise RuntimeError(stage)

    monkeypatch.setattr(main_module, "parse_args", lambda: args)
    monkeypatch.setattr(main_module.AppConfig, "from_env", lambda: cfg)
    monkeypatch.setattr(main_module, "SpeechEmotionRecognizer", lambda cfg: object())
    monkeypatch.setattr(main_module, "StrategyGenerator", Generator)
    monkeypatch.setattr(main_module, "TTSAdapter", lambda cfg:
                        fail("tts_startup") if failure_stage == "tts_startup" else object())
    monkeypatch.setattr(main_module, "EmotionStateSmoother", lambda *a, **k: object())
    monkeypatch.setattr(main_module, "JsonMemory", lambda *a, **k: object())

    class Runtime:
        def close(self, **kwargs):
            if failure_stage == "runtime_close":
                fail("runtime_close")

    monkeypatch.setattr(main_module, "_initialize_streaming_runtime", lambda *a, **k: Runtime())
    monkeypatch.setattr(main_module, "run_one_streaming_turn", lambda **kwargs: fail("turn"))
    with pytest.raises(RuntimeError, match=failure_stage):
        main_module.main()
    assert closes == ["generator"]


@pytest.mark.parametrize("streaming,enabled,backend,expected", [
    (False, True, "ollama", False), (True, False, "ollama", False),
    (True, True, "mock", False), (True, True, "ollama", True),
])
def test_startup_enables_only_requested_streaming_ollama(streaming, enabled, backend, expected):
    class Generator:
        enables = 0

        def enable_http_reuse(self):
            self.enables += 1
            return True

    generator = Generator()
    cfg = AppConfig(llm_backend=backend, stream_llm_reuse_http=enabled)
    assert main_module._maybe_enable_llm_http_reuse(cfg, generator, streaming=streaming) is expected
    assert generator.enables == int(expected)


@pytest.mark.parametrize("framing", ["length", "chunked", "gated_chunked"])
@pytest.mark.parametrize("reuse", [False, True])
def test_real_http11_connection_count(framing, reuse, monkeypatch, record_property):
    # This is real TCP and Requests, not Ollama/model or microphone validation.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        block_on_close = False
        accepted = 0

        def __init__(self, *args):
            self.allow_eof = threading.Event()
            super().__init__(*args)

        def get_request(self):
            connection = super().get_request()
            self.accepted += 1
            return connection

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            body = json.dumps({"models": [{"name": "qwen3:4b-instruct"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            body = _records()
            self.send_response(200)
            if framing == "length":
                self.send_header("Content-Length", str(len(body)))
            else:
                self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.wfile.write(body if framing == "length" else
                             f"{len(body):X}\r\n".encode() + body + b"\r\n")
            self.wfile.flush()
            if framing != "length":
                if framing == "gated_chunked":
                    self.server.allow_eof.wait(3)
                try:
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass  # The client intentionally closes at done, before EOF.

    server = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    generator = StrategyGenerator(AppConfig(
        llm_backend="ollama", llm_ollama_base_url=base,
        llm_ollama_chat_url=base + "/api/chat", llm_timeout_seconds=2,
    ))
    if reuse:
        generator.enable_http_reuse()
    consumers = []
    try:
        for _ in range(2):
            if framing != "gated_chunked":
                assert "".join(x.text for x in _stream(generator)) == TEXT
                continue
            finished = threading.Event()
            outcomes = []

            def consume():
                try:
                    outcomes.append("".join(x.text for x in _stream(generator)))
                except Exception as exc:
                    outcomes.append(exc)
                finally:
                    finished.set()

            consumer = threading.Thread(target=consume, daemon=True)
            consumers.append(consumer)
            consumer.start()
            assert finished.wait(1), "done=true was held waiting for HTTP EOF"
            assert outcomes == [TEXT]
            assert not server.allow_eof.is_set()
        record_property("accepted_connections", server.accepted)
        expected = (1 if framing == "length" else 2) if reuse else 4
        assert server.accepted == expected
    finally:
        server.allow_eof.set()
        generator.close()
        for consumer in consumers:
            consumer.join(3)
        server.shutdown()
        server.server_close()
        thread.join(2)
    assert not thread.is_alive()
    assert all(not consumer.is_alive() for consumer in consumers)
