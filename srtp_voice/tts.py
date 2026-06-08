from __future__ import annotations

import asyncio
import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path

from .config import AppConfig


class TTSAdapter:
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

        raise ValueError(f"Unknown TTS_BACKEND: {self.cfg.tts_backend}")

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
                "edge_tts 需要 ffmpeg 将 mp3 转为 wav。请安装：winget install Gyan.FFmpeg"
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
