from __future__ import annotations

import math
import struct
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass
class VADResult:
    is_speech: bool
    rms: float


class EnergyVAD:
    def __init__(self, threshold: float = 0.018):
        self.threshold = threshold

    def predict(self, pcm16_frame: bytes) -> VADResult:
        if not pcm16_frame:
            return VADResult(False, 0.0)

        count = len(pcm16_frame) // 2
        if count <= 0:
            return VADResult(False, 0.0)

        samples = struct.unpack("<" + "h" * count, pcm16_frame)
        rms = math.sqrt(sum(s * s for s in samples) / count) / 32768.0
        return VADResult(is_speech=rms >= self.threshold, rms=rms)


def save_pcm16_wav(path: Path, frames: List[bytes], sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        for frame in frames:
            wf.writeframes(frame)


def record_until_silence(
    output_path: Path,
    sample_rate: int = 16000,
    frame_ms: int = 32,
    threshold: float = 0.018,
    min_speech_ms: int = 400,
    silence_ms: int = 800,
    max_record_seconds: float = 8.0,
) -> Path:
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError("VAD 录音需要 sounddevice：pip install sounddevice") from exc

    frame_samples = int(sample_rate * frame_ms / 1000)
    max_frames = int(max_record_seconds * 1000 / frame_ms)
    min_speech_frames = max(1, int(min_speech_ms / frame_ms))
    silence_frames_needed = max(1, int(silence_ms / frame_ms))

    vad = EnergyVAD(threshold=threshold)

    recorded: List[bytes] = []
    speech_frames = 0
    silence_frames = 0
    started = False

    print("[VAD] 等待语音输入...")

    with sd.RawInputStream(
        samplerate=sample_rate,
        channels=1,
        dtype="int16",
        blocksize=frame_samples,
    ) as stream:
        for _ in range(max_frames):
            data, overflowed = stream.read(frame_samples)
            frame = bytes(data)
            result = vad.predict(frame)

            if result.is_speech:
                speech_frames += 1
                silence_frames = 0
                if not started and speech_frames >= min_speech_frames:
                    started = True
                    print("[VAD] 检测到语音，开始录音")
                if started:
                    recorded.append(frame)
            else:
                if started:
                    recorded.append(frame)
                    silence_frames += 1
                    if silence_frames >= silence_frames_needed:
                        print("[VAD] 检测到静音，结束录音")
                        break
                else:
                    speech_frames = 0

    if not recorded:
        raise RuntimeError("VAD 未检测到有效语音，请降低 VAD_THRESHOLD 或检查麦克风")

    save_pcm16_wav(output_path, recorded, sample_rate)
    return output_path