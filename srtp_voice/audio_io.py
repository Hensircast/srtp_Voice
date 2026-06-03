from __future__ import annotations

import math
import struct
import wave
from pathlib import Path
from typing import List


def make_dummy_wav(path: Path, seconds: float = 1.0, sample_rate: int = 16000) -> None:
    """生成一段很短的占位音频，保证 console 模式没有麦克风也能跑通。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(seconds * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        for i in range(n):
            sample = int(1200 * math.sin(2 * math.pi * 440 * i / sample_rate))
            wf.writeframes(struct.pack("<h", sample))


def record_from_mic(path: Path, seconds: float, sample_rate: int = 16000) -> None:
    """固定时长录音。第一版先稳定跑通；后续可替换成 VAD 端点检测。"""
    try:
        import sounddevice as sd
        import soundfile as sf
    except ImportError as exc:
        raise RuntimeError("mic 模式需要安装 sounddevice 和 soundfile：pip install sounddevice soundfile") from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    audio = sd.rec(int(seconds * sample_rate), samplerate=sample_rate, channels=1, dtype="float32")
    sd.wait()
    sf.write(str(path), audio, sample_rate)


def play_wav(path: Path) -> None:
    """优先用 sounddevice 播放；没有依赖时只打印路径。"""
    try:
        import sounddevice as sd
        import soundfile as sf
    except ImportError:
        print(f"      未安装播放依赖，音频文件已生成：{path}")
        return

    data, sr = sf.read(str(path), dtype="float32")
    sd.play(data, sr)
    sd.wait()


def play_mp3(path: Path) -> None:
    """Play an MP3 file with pygame without decoding it through soundfile."""
    if path.suffix.lower() != ".mp3":
        raise ValueError(f"play_mp3 only accepts .mp3 files: {path}")
    if not path.exists() or path.stat().st_size == 0:
        raise FileNotFoundError(f"MP3 file is missing or empty: {path}")

    try:
        import pygame
    except ImportError as exc:
        raise RuntimeError("MP3 playback requires pygame: pip install pygame") from exc

    pygame.mixer.init()
    try:
        pygame.mixer.music.load(str(path))
        pygame.mixer.music.play()
        clock = pygame.time.Clock()
        while pygame.mixer.music.get_busy():
            clock.tick(20)
    finally:
        pygame.mixer.music.unload()
        pygame.mixer.quit()


def read_wav_pcm16_mono(path: Path) -> tuple[int, List[int]]:
    """读取 wav 并尽量转成单声道 int16 列表。当前只处理 16-bit PCM，足够用于占位特征。"""
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        sample_rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())

    if sampwidth != 2:
        # 对 float wav 或其他格式，真实项目建议用 librosa/soundfile 读取。
        return sample_rate, []

    values = list(struct.unpack("<" + "h" * (len(raw) // 2), raw))
    if channels == 1:
        return sample_rate, values

    mono = []
    for i in range(0, len(values), channels):
        mono.append(int(sum(values[i:i + channels]) / channels))
    return sample_rate, mono
