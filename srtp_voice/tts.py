from __future__ import annotations

import asyncio
import math
import struct
import subprocess
import wave
from pathlib import Path

from .config import AppConfig


class TTSAdapter:
    """TTS 适配器。mock 用于占位；edge_tts 用于真实语音合成；piper 后续可接本地小模型。"""

    def __init__(self, cfg: AppConfig):
        self.cfg = cfg

    def synthesize(self, text: str, out_wav: Path) -> None:
        out_wav.parent.mkdir(parents=True, exist_ok=True)

        if self.cfg.tts_backend == "mock":
            self._mock_tts(text, out_wav)
            return

        if self.cfg.tts_backend == "edge_tts":
            self._edge_tts(text, out_wav)
            return

        if self.cfg.tts_backend == "piper":
            self._piper_placeholder(text, out_wav)
            return

        raise ValueError(f"未知 TTS_BACKEND: {self.cfg.tts_backend}")

    def _mock_tts(self, text: str, out_wav: Path) -> None:
        """生成一段简单音调，只用于打通主流程，不是真实 TTS。"""
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

    def _edge_tts(self, text: str, out_wav: Path) -> None:
        """
        使用 edge-tts 生成真实语音。
        注意：edge-tts 默认保存 mp3，这里优先保存 mp3，再尝试用 ffmpeg 转 wav。
        如果电脑没有 ffmpeg，就直接把 mp3 路径打印出来。
        """
        import edge_tts

        mp3_path = out_wav.with_suffix(".mp3")

        async def run() -> None:
            communicate = edge_tts.Communicate(
                text=text,
                voice=self.cfg.tts_voice,
                rate="+0%",
                volume="+0%",
            )
            await communicate.save(str(mp3_path))

        asyncio.run(run())

        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(mp3_path),
                    "-ar",
                    str(self.cfg.sample_rate),
                    "-ac",
                    "1",
                    str(out_wav),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            print(f"      edge-tts 已生成 mp3：{mp3_path}")
            print("      未检测到 ffmpeg，暂时没有转换为 wav。")
            print("      你可以安装 ffmpeg，或者后续把播放函数改成播放 mp3。")

    def _piper_placeholder(self, text: str, out_wav: Path) -> None:
        """
        Piper 是本地小模型 TTS，后续可替换这里。
        典型命令：
        echo 你好 | piper --model zh_CN-huayan-medium.onnx --output_file outputs/reply.wav
        """
        raise NotImplementedError("请后续在这里接入 Piper 本地 TTS。")