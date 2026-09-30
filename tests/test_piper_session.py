"""Persistent Piper session tests with a scripted fake piper CLI.

The fake mirrors the verified real contract: line-delimited JSON on stdin and
one plain output path per stdout line. ``FAKE_PIPER_EARLY_ACK=1`` reproduces the
real hazard where the path is announced before the WAV stream is closed.
"""

from __future__ import annotations

import os
import sys
import textwrap
import time
import wave
from pathlib import Path

import pytest

from srtp_voice.config import AppConfig
from srtp_voice.piper_session import (
    ACK_QUEUE_MAX,
    PersistentPiperSession,
    PiperSessionError,
    PiperSessionFactory,
    build_piper_command,
)

FAKE_PIPER = textwrap.dedent(
    """
    import json, os, struct, sys, time, wave

    MODE = os.environ.get("FAKE_PIPER_MODE", "ok")
    DELAY = float(os.environ.get("FAKE_PIPER_DELAY", "0"))
    EARLY_ACK = os.environ.get("FAKE_PIPER_EARLY_ACK", "0") == "1"
    EXIT_AFTER = int(os.environ.get("FAKE_PIPER_EXIT_AFTER", "0"))
    NOISE = os.environ.get("FAKE_PIPER_NOISE", "0") == "1"
    count = 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        payload = json.loads(line)
        out = payload["output_file"]
        text = payload["text"]
        count += 1
        if DELAY:
            time.sleep(DELAY)
        if MODE == "exit":
            sys.exit(3)
        if MODE == "truncated":
            with open(out, "wb") as handle:
                handle.write(b"RIFF" + struct.pack("<I", 999) + b"WAVEfmt ")
                handle.flush()
            print("log noise that is not a path", flush=True)
            print(out, flush=True)
            continue
        frames = 160 + len(text.encode("utf-8")) * 8
        if EARLY_ACK:
            print(out, flush=True)
        with wave.open(out, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(22050)
            wav.writeframes(b"\\x00\\x01" * frames)
        if not EARLY_ACK:
            print(out, flush=True)
        if NOISE:
            print("[info] loading voice model", flush=True)
        sys.stderr.write("synthetic stderr line %d\\n" % count)
        sys.stderr.flush()
        if EXIT_AFTER and count >= EXIT_AFTER:
            sys.exit(0)
    """
)


def _fake_piper(tmp_path: Path, **env: object) -> tuple[list[str], Path, dict[str, str]]:
    """Write the fake CLI and return (launcher prefix, model path, session env)."""

    script = tmp_path / "fake_piper.py"
    script.write_text(FAKE_PIPER, encoding="utf-8")
    model = tmp_path / "model.onnx"
    model.write_bytes(b"model")
    settings = {
        "FAKE_PIPER_MODE": "ok",
        "FAKE_PIPER_DELAY": "0",
        "FAKE_PIPER_EARLY_ACK": "0",
        "FAKE_PIPER_EXIT_AFTER": "0",
        "FAKE_PIPER_NOISE": "0",
    }
    for key, value in env.items():
        settings[key.upper()] = str(value)
    return [sys.executable, str(script)], model, settings


def _cfg(tmp_path: Path, model: Path, **overrides) -> AppConfig:
    values = {
        "tts_backend": "piper",
        "tts_piper_exe": Path(sys.executable),
        "tts_piper_model": model,
        "tts_piper_use_json_input": True,
        "tts_piper_timeout_seconds": 20,
    }
    values.update(overrides)
    return AppConfig(**values)


def _session(cfg: AppConfig, prefix) -> PersistentPiperSession:
    return PersistentPiperSession(cfg, command_prefix=prefix)


@pytest.fixture()
def fake_env(tmp_path, monkeypatch):
    def build(session_env: dict | None = None, **cfg_overrides):
        prefix, model, settings = _fake_piper(tmp_path, **(session_env or {}))
        for key, value in settings.items():
            monkeypatch.setenv(key, value)
        return _cfg(tmp_path, model, **cfg_overrides), prefix

    return build


def _wav_frames(path: Path) -> int:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes()


# --------------------------------------------------------------------------- #
# reuse and laziness
# --------------------------------------------------------------------------- #
def test_lazy_session_starts_only_on_first_sentence(fake_env, tmp_path) -> None:
    cfg, prefix = fake_env()
    session = _session(cfg, prefix)

    assert session.started is False
    session.synthesize("你好", tmp_path / "a.wav")
    first_process = session._process
    assert session.started is True

    session.synthesize("第二句", tmp_path / "b.wav")
    assert session._process is first_process
    session.close()
    assert session.started is False


def test_many_sentences_reuse_one_process(fake_env, tmp_path) -> None:
    cfg, prefix = fake_env()
    session = _session(cfg, prefix)
    replies = [session.synthesize(f"第{i}句", tmp_path / f"c-{i}.wav") for i in range(5)]
    pid = session._process.pid if session._process is not None else None
    session.close()

    assert len(replies) == 5
    assert pid is not None
    assert all(reply.frames > 0 and reply.output_path.exists() for reply in replies)


def test_dead_session_is_recycled_and_next_sentence_gets_new_process(fake_env, tmp_path) -> None:
    cfg, prefix = fake_env(session_env={"FAKE_PIPER_EXIT_AFTER": 1})
    session = _session(cfg, prefix)

    session.synthesize("第一句", tmp_path / "one.wav")
    with pytest.raises(PiperSessionError):
        session.synthesize("第二句", tmp_path / "two.wav")
    assert session.started is False

    # A clean process is created for the following sentence.
    session.synthesize("第三句", tmp_path / "three.wav")
    assert session.started is True
    session.close()


def test_closed_session_refuses_more_work(fake_env, tmp_path) -> None:
    cfg, prefix = fake_env()
    session = _session(cfg, prefix)
    session.synthesize("句子", tmp_path / "a.wav")
    session.close()

    with pytest.raises(PiperSessionError):
        session.synthesize("再来一句", tmp_path / "b.wav")


# --------------------------------------------------------------------------- #
# protocol and integrity
# --------------------------------------------------------------------------- #
def test_plain_path_ack_is_parsed_and_log_noise_ignored(fake_env, tmp_path) -> None:
    cfg, prefix = fake_env(session_env={"FAKE_PIPER_NOISE": 1})
    session = _session(cfg, prefix)

    assert PersistentPiperSession._parse_ack(str(tmp_path / "a.wav")) == str(tmp_path / "a.wav")
    assert PersistentPiperSession._parse_ack('{"output_file": "/tmp/a.wav"}') == "/tmp/a.wav"
    assert PersistentPiperSession._parse_ack("[info] loading model") is None
    assert PersistentPiperSession._parse_ack("# comment") is None

    reply = session.synthesize("纯路径回执", tmp_path / "plain.wav")

    assert reply.output_path.is_absolute()
    assert reply.frames > 0
    session.close()


def test_ack_for_other_path_is_not_accepted(fake_env, tmp_path) -> None:
    """A reply naming a different file must not acknowledge this sentence."""

    cfg, prefix = fake_env(session_env={"FAKE_PIPER_DELAY": 3.0}, tts_piper_timeout_seconds=2)
    session = _session(cfg, prefix)
    session._path_queue.append(str(tmp_path / "someone-else.wav"))

    with pytest.raises(PiperSessionError):
        session.synthesize("等待自己的回执", tmp_path / "mine.wav")
    assert session.started is False
    session.close()


def test_early_ack_still_requires_a_complete_wav(fake_env, tmp_path) -> None:
    cfg, prefix = fake_env(session_env={"FAKE_PIPER_EARLY_ACK": 1})
    session = _session(cfg, prefix)

    reply = session.synthesize("完整校验", tmp_path / "early.wav")

    assert _wav_frames(reply.output_path) == reply.frames
    session.close()


def test_truncated_output_is_a_failure_not_a_success(fake_env, tmp_path) -> None:
    cfg, prefix = fake_env(
        session_env={"FAKE_PIPER_MODE": "truncated"}, tts_piper_timeout_seconds=2
    )
    session = _session(cfg, prefix)

    with pytest.raises(PiperSessionError):
        session.synthesize("坏输出", tmp_path / "broken.wav")
    assert session.started is False
    session.close()


def test_timeout_recycles_the_process(fake_env, tmp_path) -> None:
    cfg, prefix = fake_env(
        session_env={"FAKE_PIPER_DELAY": 3.0}, tts_piper_timeout_seconds=2
    )
    session = _session(cfg, prefix)

    with pytest.raises(PiperSessionError):
        session.synthesize("超时", tmp_path / "slow.wav")
    assert session.started is False
    session.close()


def test_crash_before_ack_is_reported(fake_env, tmp_path) -> None:
    cfg, prefix = fake_env(
        session_env={"FAKE_PIPER_MODE": "exit"}, tts_piper_timeout_seconds=5
    )
    session = _session(cfg, prefix)

    with pytest.raises(PiperSessionError):
        session.synthesize("崩溃", tmp_path / "crash.wav")
    assert session.started is False
    session.close()


def test_ack_queue_is_bounded(fake_env, tmp_path) -> None:
    cfg, prefix = fake_env()
    session = _session(cfg, prefix)

    assert session._path_queue.maxlen == ACK_QUEUE_MAX
    session.close()


def test_stderr_tail_is_bounded(fake_env, tmp_path) -> None:
    cfg, prefix = fake_env()
    session = _session(cfg, prefix)
    for index in range(45):
        session.synthesize(f"句子{index}", tmp_path / f"s-{index}.wav")
    tail = session.stderr_tail
    session.close()

    assert 0 < len(tail) <= 40


def test_json_payload_handles_newlines_chinese_and_spaces(fake_env, tmp_path) -> None:
    cfg, prefix = fake_env()
    session = _session(cfg, prefix)
    out_dir = tmp_path / "输出 目录 with spaces"
    text = "第一行。\n第二行，带空格与标点！"

    reply = session.synthesize(text, out_dir / "句子 一.wav")

    assert reply.output_path.exists()
    assert reply.frames > 0
    session.close()


# --------------------------------------------------------------------------- #
# command building and factory wiring
# --------------------------------------------------------------------------- #
def test_output_control_extra_args_are_refused(fake_env, tmp_path) -> None:
    cfg, _prefix = fake_env(tts_piper_extra_args="--output_file other.wav")
    with pytest.raises(PiperSessionError):
        build_piper_command(cfg)


def test_build_command_keeps_supported_parameters(fake_env, tmp_path) -> None:
    espeak = tmp_path / "espeak-ng-data"
    espeak.mkdir()
    cfg, _prefix = fake_env(
        tts_piper_espeak_data=espeak,
        tts_piper_extra_args="--length_scale 1.1 --sentence_silence 0.2",
    )

    cmd = build_piper_command(cfg)

    assert "--json-input" in cmd
    assert "--espeak_data" in cmd and str(espeak) in cmd
    assert "--length_scale" in cmd and "1.1" in cmd
    assert "--sentence_silence" in cmd and "0.2" in cmd
    assert "--output_file" not in cmd


def test_factory_is_lazy_and_can_be_disabled(fake_env, tmp_path) -> None:
    cfg, prefix = fake_env()
    calls: list[str] = []

    def one_shot(text, out_wav):
        calls.append(text)
        Path(out_wav).write_bytes(b"one-shot")

    factory = PiperSessionFactory(
        cfg,
        session_factory=lambda config: PersistentPiperSession(
            config, command_prefix=prefix
        ),
        one_shot=one_shot,
    )
    assert factory.session_started is False

    factory.synthesize("第一句", tmp_path / "x.wav")
    assert factory.session_started is True
    assert calls == []
    factory.close()

    disabled, _ = fake_env(tts_piper_persistent=False)
    fallback = PiperSessionFactory(disabled, one_shot=one_shot)
    fallback.synthesize("第二句", tmp_path / "y.wav")
    assert fallback.session_started is False
    assert calls == ["第二句"]


def test_adapter_for_streaming_returns_independent_adapter(fake_env, tmp_path) -> None:
    from srtp_voice import tts as tts_module

    cfg, prefix = fake_env()
    adapter = tts_module.TTSAdapter(cfg)
    assert adapter.supports_streaming() is False
    assert adapter.has_persistent_session() is False

    streaming = adapter.for_streaming()
    assert streaming is not adapter
    assert streaming.has_persistent_session() is True
    assert adapter.has_persistent_session() is False
    # The raw streaming API stays unimplemented on every adapter.
    assert adapter.supports_streaming() is False
    assert streaming.supports_streaming() is False
    with pytest.raises(NotImplementedError):
        streaming.synthesize_stream("hello")

    factory = streaming._streaming_session
    assert factory is not None
    factory._session_factory = lambda config: PersistentPiperSession(
        config, command_prefix=prefix
    )

    streaming.synthesize("流式", tmp_path / "stream.wav")
    assert factory.session_started is True
    assert adapter.has_persistent_session() is False

    streaming.close()
    assert factory.session_started is False


def test_callers_adapter_keeps_one_shot_path(fake_env, tmp_path, monkeypatch) -> None:
    from srtp_voice import tts as tts_module

    cfg, _prefix = fake_env()
    adapter = tts_module.TTSAdapter(cfg).for_streaming()
    caller = tts_module.TTSAdapter(cfg)
    captured = {}

    def fake_run(cmd, input, stdout, stderr, timeout):
        captured["cmd"] = cmd
        out = Path(cmd[cmd.index("--output_file") + 1])
        with wave.open(str(out), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(22050)
            handle.writeframes(b"\x00\x01" * 32)
        return type("R", (), {"returncode": 0, "stderr": b""})()

    monkeypatch.setattr(tts_module.subprocess, "run", fake_run)
    out_wav = tmp_path / "sync.wav"
    caller.synthesize("同步路径", out_wav)

    assert captured["cmd"][0] == str(cfg.tts_piper_exe)
    assert out_wav.exists()
    assert caller.has_persistent_session() is False
    adapter.close()


def test_runtime_owns_streaming_resource_and_closes_it(fake_env, tmp_path) -> None:
    from srtp_voice import streaming_runtime as runtime_module
    from srtp_voice import tts as tts_module

    cfg, prefix = fake_env()
    caller = tts_module.TTSAdapter(cfg)
    streaming = caller.for_streaming()
    factory = streaming._streaming_session
    assert factory is not None
    factory._session_factory = lambda config: PersistentPiperSession(
        config, command_prefix=prefix
    )
    streaming.synthesize("预热", tmp_path / "pre.wav")
    assert factory.session_started is True

    class _NoopController:
        def start_turn(self):
            raise AssertionError("no turn is started in this wiring test")

    runtime = runtime_module.StreamingResponseRuntime.__new__(
        runtime_module.StreamingResponseRuntime
    )
    runtime.tts = streaming
    runtime._caller_tts = caller
    runtime._owned_streaming_resource = None
    runtime._closed = False
    runtime._release_owned_resource()

    assert factory.session_started is False
    assert caller.has_persistent_session() is False
