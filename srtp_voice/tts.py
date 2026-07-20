from __future__ import annotations

import asyncio
import json
import math
import os
import shutil
import shlex
import struct
import subprocess
import wave
from pathlib import Path

from .config import AppConfig, is_windows_platform


class TTSAdapter:
    _PIPER_FORBIDDEN_OUTPUT_ARGS = {
        "--output_file",
        "--output-file",
        "-f",
        "--output_dir",
        "--output-dir",
        "-d",
        "--output_raw",
        "--output-raw",
    }

    def __init__(self, cfg: AppConfig):
        self.cfg = cfg

    def synthesize(self, text: str, out_wav: Path) -> None:
        out_wav.parent.mkdir(parents=True, exist_ok=True)

        if self.cfg.tts_backend == "mock":
            self._mock_tts(text, out_wav)
            return

        if self.cfg.tts_backend == "edge_tts":
            self._edge_tts_to_wav(text, out_wav)
            return

        if self.cfg.tts_backend == "piper":
            self._piper_tts(text, out_wav)
            return

        raise ValueError(f"Unknown TTS_BACKEND: {self.cfg.tts_backend}")

    def supports_streaming(self) -> bool:
        return False

    def synthesize_stream(self, text: str):
        raise NotImplementedError(f"TTS backend '{self.cfg.tts_backend}' does not support streaming TTS yet.")

    def _mock_tts(self, text: str, out_wav: Path) -> None:
        if out_wav.suffix.lower() != ".wav":
            raise ValueError(f"mock TTS output must be .wav: {out_wav}")

        sample_rate = self.cfg.sample_rate
        seconds = min(4.0, max(0.8, len(text) * 0.045))
        n = int(seconds * sample_rate)

        with wave.open(str(out_wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)

            for i in range(n):
                freq = 420 + 40 * math.sin(2 * math.pi * i / sample_rate / 0.35)
                env = min(1.0, i / (0.05 * sample_rate), (n - i) / (0.08 * sample_rate))
                sample = int(2600 * env * math.sin(2 * math.pi * freq * i / sample_rate))
                wf.writeframes(struct.pack("<h", sample))

    def _edge_tts_to_wav(self, text: str, out_wav: Path) -> None:
        import edge_tts

        temp_mp3 = out_wav.with_suffix(".mp3")

        async def run() -> None:
            communicate = edge_tts.Communicate(
                text=text,
                voice=self.cfg.tts_voice,
                rate="+0%",
                volume="+0%",
            )
            await communicate.save(str(temp_mp3))

        asyncio.run(run())

        if not temp_mp3.exists() or temp_mp3.stat().st_size == 0:
            raise RuntimeError(f"edge-tts did not create MP3: {temp_mp3}")

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError(
                "edge-tts 已经生成 MP3，但未找到 FFmpeg，无法继续转换为 WAV。\n"
                "Windows PowerShell:\n"
                "  winget install --id Gyan.FFmpeg --exact\n"
                "Ubuntu Bash:\n"
                "  sudo apt install ffmpeg"
            )

        cmd = [
            ffmpeg,
            "-y",
            "-i", str(temp_mp3),
            "-ar", str(self.cfg.sample_rate),
            "-ac", "1",
            str(out_wav),
        ]
        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        if not out_wav.exists() or out_wav.stat().st_size == 0:
            raise RuntimeError(f"ffmpeg did not create WAV: {out_wav}")

    def _piper_tts(self, text: str, out_wav: Path) -> None:
        clean_text = text.strip()
        if not clean_text:
            raise ValueError("piper TTS text must not be empty")

        exe = self.cfg.tts_piper_exe
        model = self.cfg.tts_piper_model
        if not exe.is_file():
            raise FileNotFoundError(f"piper executable not found: {exe}")
        if not is_windows_platform() and not os.access(exe, os.X_OK):
            raise RuntimeError(
                "piper executable is not executable on this platform. "
                f"exe={exe}. Grant execute permission with: chmod +x {exe}"
            )
        if not model.is_file():
            raise FileNotFoundError(f"piper model not found: {model}")

        out_wav.parent.mkdir(parents=True, exist_ok=True)
        if out_wav.exists():
            try:
                out_wav.unlink()
            except OSError as exc:
                raise RuntimeError(f"piper TTS cannot remove stale output WAV before synthesis: {out_wav}") from exc

        cmd = [
            str(exe),
            "--model", str(model),
            "--output_file", str(out_wav),
        ]

        config = self.cfg.tts_piper_config
        if config is None:
            auto_config = Path(str(model) + ".json")
            if auto_config.is_file():
                config = auto_config
        if config is not None:
            cmd.extend(["--config", str(config)])

        if self.cfg.tts_piper_espeak_data is not None:
            cmd.extend(["--espeak_data", str(self.cfg.tts_piper_espeak_data)])

        if self.cfg.tts_piper_use_json_input:
            cmd.append("--json-input")
            stdin_data = (json.dumps({"text": clean_text}, ensure_ascii=False) + "\n").encode("utf-8")
        else:
            stdin_data = (clean_text + "\n").encode("utf-8")

        if self.cfg.tts_piper_extra_args:
            extra_args = shlex.split(self.cfg.tts_piper_extra_args)
            forbidden = [arg for arg in extra_args if arg in self._PIPER_FORBIDDEN_OUTPUT_ARGS]
            if forbidden:
                raise ValueError(
                    "TTS_PIPER_EXTRA_ARGS must not contain Piper output path controls "
                    f"{forbidden}; output path is managed by TTSAdapter."
                )
            cmd.extend(extra_args)

        try:
            result = subprocess.run(
                cmd,
                input=stdin_data,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.cfg.tts_piper_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "piper TTS timed out. "
                f"exe={exe}, model={model}, timeout={self.cfg.tts_piper_timeout_seconds}s"
            ) from exc

        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")[:1000]
            raise RuntimeError(
                "piper TTS failed. "
                f"exe={exe}, model={model}, returncode={result.returncode}, stderr={stderr}"
            )

        if not out_wav.exists() or out_wav.stat().st_size == 0:
            raise RuntimeError(f"piper TTS did not create a non-empty WAV: {out_wav}")
