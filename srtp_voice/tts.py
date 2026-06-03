from __future__ import annotations

import asyncio
import math
import struct
import wave
from pathlib import Path

from .config import AppConfig


class TTSAdapter:
    """TTS adapter for mock WAV, edge-tts MP3, and future local engines."""

    def __init__(self, cfg: AppConfig):
        self.cfg = cfg

    def synthesize(self, text: str, out_audio: Path) -> None:
        out_audio.parent.mkdir(parents=True, exist_ok=True)

        if self.cfg.tts_backend == "mock":
            self._mock_tts(text, out_audio)
            return

        if self.cfg.tts_backend == "edge_tts":
            self._edge_tts(text, out_audio)
            return

        if self.cfg.tts_backend == "piper":
            self._piper_placeholder(text, out_audio)
            return

        raise ValueError(f"Unknown TTS_BACKEND: {self.cfg.tts_backend}")

    def _mock_tts(self, text: str, out_wav: Path) -> None:
        """Generate a simple WAV tone for offline pipeline checks."""
        if out_wav.suffix.lower() != ".wav":
            raise ValueError(f"mock TTS output must be a .wav file: {out_wav}")

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

    def _edge_tts(self, text: str, out_mp3: Path) -> None:
        """Generate real speech with edge-tts and save the MP3 directly."""
        if out_mp3.suffix.lower() != ".mp3":
            raise ValueError(f"edge_tts output must be an .mp3 file: {out_mp3}")

        import edge_tts

        async def run() -> None:
            communicate = edge_tts.Communicate(
                text=text,
                voice=self.cfg.tts_voice,
                rate="+0%",
                volume="+0%",
            )
            await communicate.save(str(out_mp3))

        asyncio.run(run())

        if not out_mp3.exists() or out_mp3.stat().st_size == 0:
            raise RuntimeError(f"edge-tts did not create a valid MP3 file: {out_mp3}")

    def _piper_placeholder(self, text: str, out_audio: Path) -> None:
        raise NotImplementedError("Piper local TTS is not connected yet.")
