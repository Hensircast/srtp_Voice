from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

import srtp_voice.tts as tts_module
from srtp_voice.config import AppConfig
from srtp_voice.tts import TTSAdapter


def test_edge_tts_missing_ffmpeg_reports_windows_and_ubuntu_commands(
    monkeypatch,
    tmp_path,
) -> None:
    calls = []

    class FakeCommunicate:
        def __init__(self, *, text, voice, rate, volume):
            calls.append(
                {
                    "text": text,
                    "voice": voice,
                    "rate": rate,
                    "volume": volume,
                }
            )

        async def save(self, path: str) -> None:
            Path(path).write_bytes(b"fake edge-tts mp3")

    fake_edge_tts = types.ModuleType("edge_tts")
    fake_edge_tts.Communicate = FakeCommunicate
    monkeypatch.setitem(sys.modules, "edge_tts", fake_edge_tts)
    monkeypatch.setattr(tts_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        tts_module.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("FFmpeg must not run when it is missing"),
    )

    out_wav = tmp_path / "reply.wav"
    cfg = AppConfig(
        tts_backend="edge_tts",
        tts_voice="zh-CN-XiaoxiaoNeural",
    )

    with pytest.raises(RuntimeError) as exc_info:
        TTSAdapter(cfg).synthesize("你好", out_wav)

    message = str(exc_info.value)
    assert "edge-tts 已经生成 MP3" in message
    assert "无法继续转换为 WAV" in message
    assert "Windows PowerShell:\n  winget install --id Gyan.FFmpeg --exact" in message
    assert "Ubuntu Bash:\n  sudo apt install ffmpeg" in message
    assert out_wav.with_suffix(".mp3").read_bytes() == b"fake edge-tts mp3"
    assert calls == [
        {
            "text": "你好",
            "voice": "zh-CN-XiaoxiaoNeural",
            "rate": "+0%",
            "volume": "+0%",
        }
    ]
