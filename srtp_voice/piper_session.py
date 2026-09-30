"""Long-lived Piper synthesis session.

Piper loads its ONNX voice model once before its ``getline`` loop, so keeping
one process alive across sentences removes a per-sentence process start and
model load. The CLI protocol is line-delimited JSON on stdin
(``{"text": ..., "output_file": ...}``) and one output path per line on stdout.

The C++ implementation notifies the output path on stdout *before* the WAV
stream is closed, therefore a session never trusts the acknowledgement alone:
each reply is verified by waiting for a stable, complete, readable WAV inside
the same bounded timeout.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
import wave
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from .config import AppConfig, is_windows_platform

FORBIDDEN_OUTPUT_ARGS = frozenset(
    {
        "--output_file",
        "--output-file",
        "-f",
        "--output_dir",
        "--output-dir",
        "-d",
        "--output_raw",
        "--output-raw",
    }
)

STDERR_TAIL_LINES = 40
STDERR_LINE_MAX_CHARS = 500
ACK_QUEUE_MAX = 8
WAV_POLL_INTERVAL_SECONDS = 0.02
WAV_STABLE_POLLS = 2
TEARDOWN_TIMEOUT_SECONDS = 5.0


class PiperSessionError(RuntimeError):
    """Raised when a persistent Piper session cannot serve a sentence."""


@dataclass(frozen=True)
class PiperReply:
    output_path: Path
    frames: int
    sample_rate: int


def _normalized_path(path: Path) -> str:
    """Absolute, case-normalized path used for acknowledgement matching."""

    import os

    return os.path.normcase(str(Path(path).resolve()))


def _validate_wav(path: Path) -> tuple[int, int] | None:
    """Return (frames, sample_rate) only for a complete, readable PCM WAV."""

    try:
        with wave.open(str(path), "rb") as wav_file:
            frames = wav_file.getnframes()
            sample_rate = wav_file.getframerate()
            channels = wav_file.getnchannels()
            wanted = frames * channels * wav_file.getsampwidth()
            payload = wav_file.readframes(frames)
    except (OSError, EOFError, wave.Error):
        return None
    if frames <= 0 or sample_rate <= 0:
        return None
    if len(payload) != wanted:
        return None
    return frames, sample_rate


def build_piper_command(
    cfg: AppConfig,
    *,
    json_input: bool = True,
    command_prefix: Sequence[str] | None = None,
) -> list[str]:
    """Build the Piper argv, rejecting any caller-supplied output control.

    ``command_prefix`` is a test-only launcher (for example
    ``[sys.executable, "fake_piper.py"]``) so the same code path can be
    exercised on Windows, where a ``.cmd`` shim cannot carry stdio reliably.
    """

    exe = cfg.tts_piper_exe
    model = cfg.tts_piper_model
    prefix = [str(part) for part in (command_prefix or ())]
    if prefix:
        if not Path(prefix[0]).is_file():
            raise FileNotFoundError(f"piper launcher not found: {prefix[0]}")
    elif not exe.is_file():
        raise FileNotFoundError(f"piper executable not found: {exe}")
    if not prefix and not is_windows_platform() and not _is_executable(exe):
        raise PiperSessionError(
            "piper executable is not executable on this platform. "
            f"exe={exe}. Grant execute permission with: chmod +x {exe}"
        )
    if not model.is_file():
        raise FileNotFoundError(f"piper model not found: {model}")

    cmd = [*(prefix or [str(exe)]), "--model", str(model)]
    config = cfg.tts_piper_config
    if config is None:
        auto_config = Path(str(model) + ".json")
        if auto_config.is_file():
            config = auto_config
    if config is not None:
        cmd.extend(["--config", str(config)])
    if cfg.tts_piper_espeak_data is not None:
        cmd.extend(["--espeak_data", str(cfg.tts_piper_espeak_data)])
    if json_input:
        cmd.append("--json-input")
    if cfg.tts_piper_extra_args:
        import shlex

        extra_args = shlex.split(cfg.tts_piper_extra_args)
        forbidden = sorted(set(extra_args) & FORBIDDEN_OUTPUT_ARGS)
        if forbidden:
            raise PiperSessionError(
                "TTS_PIPER_EXTRA_ARGS must not contain Piper output path controls "
                f"{forbidden}; output path is managed by the session."
            )
        cmd.extend(extra_args)
    return cmd


def _is_executable(path: Path) -> bool:
    import os

    return os.access(path, os.X_OK)


class PersistentPiperSession:
    """One reusable Piper process; all synthesis on it is serialized."""

    def __init__(
        self,
        cfg: AppConfig,
        *,
        popen: Callable[..., subprocess.Popen] | None = None,
        command_prefix: Sequence[str] | None = None,
    ) -> None:
        self.cfg = cfg
        self._command_prefix = list(command_prefix) if command_prefix else None
        self._popen_factory = popen or subprocess.Popen
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._path_queue: deque[str] = deque(maxlen=ACK_QUEUE_MAX)
        self._ready = threading.Condition()
        self._stderr_tail: deque[str] = deque(maxlen=STDERR_TAIL_LINES)
        self._stderr_thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._closed = False

    # -- lifecycle --------------------------------------------------------- #
    @property
    def started(self) -> bool:
        return self._process is not None

    def start(self) -> None:
        with self._lock:
            self._start_locked()

    def _start_locked(self) -> None:
        if self._closed:
            raise PiperSessionError("Piper session is closed")
        if self._process is not None:
            return
        # The persistent protocol is line-delimited JSON; without --json-input
        # the CLI would read plain text and never answer with output paths.
        cmd = build_piper_command(
            self.cfg, json_input=True, command_prefix=self._command_prefix
        )
        try:
            self._process = self._popen_factory(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            self._process = None
            raise PiperSessionError(f"piper session failed to start: {type(exc).__name__}") from exc
        self._thread = threading.Thread(
            target=self._read_stdout, args=(self._process,),
            name="srtp-piper-stdout", daemon=True
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr, args=(self._process,),
            name="srtp-piper-stderr", daemon=True
        )
        try:
            self._thread.start()
            self._stderr_thread.start()
        except Exception:
            self._teardown()
            raise

    def close(self, *, timeout: float = TEARDOWN_TIMEOUT_SECONDS) -> None:
        """Stop only this session's process; never touch a foreign pid."""

        with self._lock:
            self._closed = True
            self._teardown(timeout=timeout)

    def _teardown(self, *, timeout: float = TEARDOWN_TIMEOUT_SECONDS) -> None:
        """Single recycle path for close, timeout, crash and I/O failure.

        Always inside a bounded deadline: stop the child, reap it, close both
        pipes, join the readers and drop queued acknowledgements so a later
        sentence can never consume a stale reply.
        """

        process = self._process
        self._process = None
        deadline = time.monotonic() + max(0.1, timeout)

        def remaining() -> float:
            return max(0.001, deadline - time.monotonic())

        with self._ready:
            self._path_queue.clear()
            self._ready.notify_all()
        if process is not None:
            try:
                if process.stdin is not None:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass
                try:
                    process.wait(timeout=min(0.25, remaining()))
                except subprocess.TimeoutExpired:
                    try:
                        process.terminate()
                        process.wait(timeout=min(0.5, remaining()))
                    except subprocess.TimeoutExpired:
                        try:
                            process.kill()
                            process.wait(timeout=remaining())
                        except (OSError, subprocess.TimeoutExpired):
                            pass
                    except OSError:
                        pass
            except OSError:
                pass
            for stream_name in ("stdout", "stderr"):
                stream = getattr(process, stream_name, None)
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
        for thread in (self._thread, self._stderr_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=remaining())
        if self._thread is not None and not self._thread.is_alive():
            self._thread = None
        if self._stderr_thread is not None and not self._stderr_thread.is_alive():
            self._stderr_thread = None
        with self._ready:
            self._ready.notify_all()

    # -- reader threads ---------------------------------------------------- #
    def _read_stdout(self, process: subprocess.Popen | None) -> None:
        if process is None or process.stdout is None:
            return
        try:
            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                parsed = self._parse_ack(line)
                with self._ready:
                    if parsed is not None and self._process is process:
                        self._path_queue.append(parsed)
                    self._ready.notify_all()
        except (OSError, ValueError):
            pass
        finally:
            with self._ready:
                self._ready.notify_all()

    @staticmethod
    def _parse_ack(line: str) -> str | None:
        """The official CLI prints the plain output path, not JSON.

        A JSON object carrying ``output_file``/``output_path`` is also accepted
        for forward compatibility; anything else is ignored so log noise can
        never be mistaken for an acknowledgement.
        """

        text = line.strip()
        if not text:
            return None
        if text.startswith("{"):
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                return None
            if isinstance(payload, dict):
                candidate = payload.get("output_file") or payload.get("output_path")
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
            return None
        if text.startswith(("[", "(", "#")):
            return None
        return text

    def _read_stderr(self, process: subprocess.Popen | None) -> None:
        if process is None or process.stderr is None:
            return
        try:
            for raw_line in process.stderr:
                text = raw_line.rstrip("\r\n")[:STDERR_LINE_MAX_CHARS]
                if text and self._process is process:
                    self._stderr_tail.append(text)
        except (OSError, ValueError):
            pass

    # -- synthesis --------------------------------------------------------- #
    def synthesize(self, text: str, out_wav: Path) -> PiperReply:
        clean_text = text.strip()
        if not clean_text:
            raise ValueError("piper TTS text must not be empty")
        out_wav = Path(out_wav).resolve()
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._ensure_running()
            out_wav.unlink(missing_ok=True)
            request = json.dumps(
                {"text": clean_text, "output_file": str(out_wav)},
                ensure_ascii=False,
            )
            process = self._process
            assert process is not None  # _ensure_running guarantees this
            deadline = time.monotonic() + max(1.0, float(self.cfg.tts_piper_timeout_seconds))
            try:
                process.stdin.write(request + "\n")  # type: ignore[union-attr]
                process.stdin.flush()  # type: ignore[union-attr]
            except (OSError, ValueError) as exc:
                self._teardown()
                raise PiperSessionError(
                    f"piper session stdin failed: {type(exc).__name__}"
                ) from exc
            try:
                self._await_ack(out_wav, deadline)
                frames, sample_rate = self._await_complete_wav(out_wav, deadline)
            except PiperSessionError:
                self._teardown()
                raise
        return PiperReply(output_path=out_wav, frames=frames, sample_rate=sample_rate)

    def _ensure_running(self) -> None:
        if self._closed:
            raise PiperSessionError("Piper session is closed")
        if self._process is None:
            self.start()
            return
        code = self._process.poll()
        if code is not None:
            # Never drop the handle silently: recycle the whole session.
            self._teardown()
            raise PiperSessionError(
                f"piper session exited unexpectedly with code {code}"
            )

    def _await_ack(self, out_wav: Path, deadline: float) -> None:
        wanted = _normalized_path(out_wav)
        with self._ready:
            while True:
                while self._path_queue:
                    candidate = self._path_queue.popleft()
                    if _normalized_path(Path(candidate)) == wanted:
                        return
                    # A late reply for a discarded sentence is dropped, never
                    # reused as this sentence's acknowledgement.
                process = self._process
                if process is None or self._closed:
                    raise PiperSessionError("piper session is no longer running")
                if process.poll() is not None:
                    code = process.returncode
                    raise PiperSessionError(
                        f"piper session exited with code {code} before acknowledging"
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PiperSessionError("piper session timed out waiting for output path")
                self._ready.wait(timeout=min(remaining, 0.05))

    def _await_complete_wav(self, out_wav: Path, deadline: float) -> tuple[int, int]:
        """Acknowledge is not completion: wait for a stable, readable WAV."""

        last_size = -1
        stable = 0
        while True:
            result = _validate_wav(out_wav)
            if result is not None:
                try:
                    size = out_wav.stat().st_size
                except OSError:
                    size = -1
                if size == last_size and size > 0:
                    stable += 1
                    if stable >= WAV_STABLE_POLLS:
                        return result
                else:
                    stable = 0
                last_size = size
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PiperSessionError(
                    "piper session produced an incomplete or unreadable WAV"
                )
            time.sleep(min(WAV_POLL_INTERVAL_SECONDS, remaining))

    @property
    def stderr_tail(self) -> tuple[str, ...]:
        return tuple(self._stderr_tail)


class PiperSessionFactory:
    """Lazy session owner used by streaming TTS; nothing starts until needed."""

    def __init__(
        self,
        cfg: AppConfig,
        *,
        session_factory: Callable[[AppConfig], PersistentPiperSession] | None = None,
        one_shot: Callable[[str, Path], None] | None = None,
    ) -> None:
        self._cfg = cfg
        self._session_factory = session_factory or (
            lambda config: PersistentPiperSession(config)
        )
        self._one_shot = one_shot
        self._session: PersistentPiperSession | None = None
        self._disabled = not bool(getattr(cfg, "tts_piper_persistent", False))
        self._lock = threading.Lock()

    @property
    def session(self) -> PersistentPiperSession | None:
        return self._session

    @property
    def session_started(self) -> bool:
        return self._session is not None and self._session.started

    def synthesize(self, text: str, out_wav: Path) -> None:
        session = self._get_session()
        if session is None:
            if self._one_shot is None:  # pragma: no cover - wiring guarantees it
                raise PiperSessionError("no piper synthesis path is available")
            self._one_shot(text, out_wav)
            return
        # Failed synthesis is never silently retried with a different backend.
        # Operators can explicitly opt out via TTS_PIPER_PERSISTENT=0.
        session.synthesize(text, out_wav)

    def _get_session(self) -> PersistentPiperSession | None:
        if self._disabled:
            return None
        with self._lock:
            if self._session is None:
                self._session = self._session_factory(self._cfg)
            return self._session

    def _drop_session(self) -> None:
        with self._lock:
            session = self._session
            self._session = None
        if session is not None:
            try:
                session.close()
            except PiperSessionError:  # pragma: no cover - defensive
                pass

    def close(self) -> None:
        self._drop_session()
