from __future__ import annotations

import struct
import wave

from srtp_voice.lip_sync import generate_lip_sync_from_wav


def test_silent_wav_uses_safe_lip_sync_floor_without_division_by_zero(
    tmp_path,
) -> None:
    wav_path = tmp_path / "silent.wav"
    with wave.open(str(wav_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(1000)
        wav_file.writeframes(struct.pack("<" + "h" * 80, *([0] * 80)))

    result = generate_lip_sync_from_wav(wav_path, frame_ms=40)

    assert result["method"] == "short_time_energy"
    assert len(result["frames"]) == 2
    assert [frame["mouth_open"] for frame in result["frames"]] == [0.1, 0.1]
