from __future__ import annotations

import math
import struct
import wave
from collections import deque
from pathlib import Path
from typing import List

from .config import AppConfig
from .vad import EnergyVAD


class NoSpeechDetectedError(RuntimeError):
    pass


def _validate_vad_timing(cfg: AppConfig) -> None:
    available_listening_ms = cfg.max_record_seconds * 1000 - cfg.vad_calibration_ms
    if available_listening_ms < cfg.min_speech_ms:
        raise ValueError(
            "Invalid VAD timing: available listening time after calibration is too short. "
            f"MAX_RECORD_SECONDS={cfg.max_record_seconds}, "
            f"VAD_CALIBRATION_MS={cfg.vad_calibration_ms}, "
            f"MIN_SPEECH_MS={cfg.min_speech_ms}. "
            "Increase MAX_RECORD_SECONDS, reduce VAD_CALIBRATION_MS, or reduce MIN_SPEECH_MS."
        )


def _rms_pcm16(frame: bytes) -> float:
    if not frame:
        return 0.0
    count = len(frame) // 2
    if count <= 0:
        return 0.0
    samples = struct.unpack("<" + "h" * count, frame[:count * 2])
    return math.sqrt(sum(sample * sample for sample in samples) / count) / 32768.0


def _quantile(values: List[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * ratio) - 1))
    return ordered[index]


def make_dummy_wav(path: Path, seconds: float = 1.0, sample_rate: int = 16000) -> None:
    """Generate a short placeholder WAV for console mode."""
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
    """Record a fixed duration from the microphone."""
    try:
        import sounddevice as sd
        import soundfile as sf
    except ImportError as exc:
        raise RuntimeError("mic mode requires sounddevice and soundfile: pip install sounddevice soundfile") from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    audio = sd.rec(int(seconds * sample_rate), samplerate=sample_rate, channels=1, dtype="float32")
    sd.wait()
    sf.write(str(path), audio, sample_rate)


def record_until_silence(path: Path, cfg: AppConfig) -> None:
    """Record until EnergyVAD sees sustained speech and then sustained silence."""
    _validate_vad_timing(cfg)

    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError("vad mode requires sounddevice: pip install sounddevice") from exc

    if cfg.vad_backend != "energy":
        raise NotImplementedError("Only energy VAD is available in the current runnable version")

    frame_samples = int(cfg.sample_rate * cfg.frame_ms / 1000)
    frame_bytes = frame_samples * 2
    max_frames = int(cfg.max_record_seconds * 1000 / cfg.frame_ms)
    silence_frames_needed = max(1, int(cfg.silence_ms / cfg.frame_ms))
    min_speech_frames = max(1, int(cfg.min_speech_ms / cfg.frame_ms))
    pre_roll_frames = max(0, int(cfg.pre_roll_ms / cfg.frame_ms))
    calibration_frames = max(0, int(cfg.vad_calibration_ms / cfg.frame_ms))

    pre_roll: deque[bytes] = deque(maxlen=pre_roll_frames)
    recorded: List[bytes] = []
    speech_frames = 0
    silence_frames = 0
    started = False

    print("      正在监听，请先短暂保持安静，然后说话。")
    with sd.RawInputStream(
        samplerate=cfg.sample_rate,
        channels=1,
        dtype="int16",
        blocksize=frame_samples,
    ) as stream:
        noise_rms_values: List[float] = []
        for _ in range(calibration_frames):
            data, overflowed = stream.read(frame_samples)
            frame = bytes(data)
            if len(frame) == frame_bytes:
                noise_rms_values.append(_rms_pcm16(frame))

        noise_floor = _quantile(noise_rms_values, 0.95)
        effective_start_threshold = max(
            cfg.vad_threshold,
            noise_floor * cfg.vad_noise_multiplier,
        )
        effective_release_threshold = effective_start_threshold * cfg.vad_release_ratio
        start_vad = EnergyVAD(threshold=effective_start_threshold)
        release_vad = EnergyVAD(threshold=effective_release_threshold)

        print(
            "      VAD calibration: "
            f"noise_floor={noise_floor:.5f}, "
            f"configured_threshold={cfg.vad_threshold:.5f}, "
            f"effective_start_threshold={effective_start_threshold:.5f}, "
            f"effective_release_threshold={effective_release_threshold:.5f}"
        )

        for frame_index in range(max(0, max_frames - calibration_frames)):
            data, overflowed = stream.read(frame_samples)
            frame = bytes(data)
            if len(frame) != frame_bytes:
                continue

            result = release_vad.predict(frame) if started else start_vad.predict(frame)
            if cfg.vad_debug and frame_index % 10 == 0:
                threshold = effective_release_threshold if started else effective_start_threshold
                print(
                    "      [VAD debug] "
                    f"rms={result.rms:.5f}, threshold={threshold:.5f}, "
                    f"started={started}, speech_frames={speech_frames}, silence_frames={silence_frames}"
                )

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
                    speech_frames = max(0, speech_frames - 1)
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
        raise NoSpeechDetectedError("未检测到有效语音，本轮结束")

    write_pcm16_wav(path, b"".join(recorded), sample_rate=cfg.sample_rate)


def play_wav(path: Path) -> None:
    """Play a WAV file when playback dependencies are installed."""
    try:
        import sounddevice as sd
        import soundfile as sf
    except ImportError:
        print(f"      playback dependencies not installed; audio file generated: {path}")
        return

    if not path.exists():
        print(f"      audio file does not exist: {path}")
        return
    data, sr = sf.read(str(path), dtype="float32")
    sd.play(data, sr)
    sd.wait()


def read_wav_pcm16_mono(path: Path) -> tuple[int, List[int]]:
    """Read a WAV file and return sample rate plus mono int16 samples when possible."""
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
