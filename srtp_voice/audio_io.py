from __future__ import annotations

import math
import struct
import time
import wave
from collections import deque
from pathlib import Path
from typing import List

from .config import AppConfig
from .vad import EnergyVAD


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


def write_pcm16_wav(path: Path, pcm: bytes, sample_rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)


def record_from_mic(path: Path, seconds: float, sample_rate: int = 16000) -> None:
    """固定时长录音。适合调试麦克风是否可用。"""
    try:
        import sounddevice as sd
        import soundfile as sf
    except ImportError as exc:
        raise RuntimeError("mic 模式需要安装 sounddevice 和 soundfile：pip install sounddevice soundfile") from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    audio = sd.rec(int(seconds * sample_rate), samplerate=sample_rate, channels=1, dtype="float32")
    sd.wait()
    sf.write(str(path), audio, sample_rate)


def record_until_silence(path: Path, cfg: AppConfig) -> None:
    """VAD 端点检测录音。

    对齐学长方案中的“音频流持续采集 + VAD 触发 + Listening 缓存”。
    这里默认用 EnergyVAD 占位；后续可把 vad.predict() 换成 Silero VAD。
    """
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError("vad 模式需要安装 sounddevice：pip install sounddevice") from exc

    if cfg.vad_backend != "energy":
        raise NotImplementedError("当前可跑版本仅内置 energy VAD；Silero VAD 请在 vad.py 接入")

    vad = EnergyVAD(threshold=cfg.vad_threshold)
    frame_samples = int(cfg.sample_rate * cfg.frame_ms / 1000)
    frame_bytes = frame_samples * 2
    max_frames = int(cfg.max_record_seconds * 1000 / cfg.frame_ms)
    silence_frames_needed = max(1, int(cfg.silence_ms / cfg.frame_ms))
    min_speech_frames = max(1, int(cfg.min_speech_ms / cfg.frame_ms))
    pre_roll_frames = max(0, int(cfg.pre_roll_ms / cfg.frame_ms))

    pre_roll: deque[bytes] = deque(maxlen=pre_roll_frames)
    recorded: List[bytes] = []
    speech_frames = 0
    silence_frames = 0
    started = False

    print("      正在监听，说话后自动开始录音，停顿后自动结束。")
    with sd.RawInputStream(
        samplerate=cfg.sample_rate,
        channels=1,
        dtype="int16",
        blocksize=frame_samples,
    ) as stream:
        for _ in range(max_frames):
            data, overflowed = stream.read(frame_samples)
            frame = bytes(data)
            if len(frame) != frame_bytes:
                continue
            result = vad.predict(frame)

            if not started:
                pre_roll.append(frame)

                if result.is_speech:
                    speech_frames += 1
                    silence_frames = 0

                    if speech_frames >= min_speech_frames:
                        started = True
                        recorded.extend(list(pre_roll))
                        silence_frames = 0
                        print(
                            "      VAD 触发，开始缓存语音："
                            f"rms={result.rms:.3f}, speech_frames={speech_frames}"
                        )
                else:
                    speech_frames = 0
                    silence_frames = 0

                continue

            recorded.append(frame)
            if result.is_speech:
                silence_frames = 0
            else:
                silence_frames += 1

            if silence_frames >= silence_frames_needed:
                print("      检测到静音，结束录音。")
                break

    if not recorded:
        print("      未检测到有效语音，生成占位音频。")
        make_dummy_wav(path, seconds=1.0, sample_rate=cfg.sample_rate)
        return

    write_pcm16_wav(path, b"".join(recorded), sample_rate=cfg.sample_rate)


def play_wav(path: Path) -> None:
    """优先用 sounddevice 播放；没有依赖时只打印路径。"""
    try:
        import sounddevice as sd
        import soundfile as sf
    except ImportError:
        print(f"      未安装播放依赖，音频文件已生成：{path}")
        return

    if not path.exists():
        print(f"      音频文件不存在：{path}")
        return
    data, sr = sf.read(str(path), dtype="float32")
    sd.play(data, sr)
    sd.wait()


def read_wav_pcm16_mono(path: Path) -> tuple[int, List[int]]:
    """读取 wav 并尽量转成单声道 int16 列表。当前只处理 16-bit PCM。"""
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        sample_rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())

    if sampwidth != 2:
        return sample_rate, []

    values = list(struct.unpack("<" + "h" * (len(raw) // 2), raw))
    if channels == 1:
        return sample_rate, values

    mono = []
    for i in range(0, len(values), channels):
        mono.append(int(sum(values[i:i + channels]) / channels))
    return sample_rate, mono
