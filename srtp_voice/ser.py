from __future__ import annotations

from pathlib import Path

from .audio_io import read_wav_pcm16_mono
from .types import EmotionResult


class SpeechEmotionRecognizer:
    """语音情绪识别占位模块。

    当前版本用非常粗糙的声学统计量模拟 SER 输出，只为了打通数据流。
    后续建议替换为 SenseVoiceSmall 的 emotion tag，或专门的 SER 模型。
    """

    def predict(self, wav_path: Path) -> EmotionResult:
        sr, samples = read_wav_pcm16_mono(wav_path)
        if not samples:
            return EmotionResult(
                label="neutral",
                intensity=0.30,
                confidence=0.30,
                features={"rms": 0.0, "zcr": 0.0, "duration": 0.0},
            )

        duration = len(samples) / max(sr, 1)
        rms = (sum(s * s for s in samples) / len(samples)) ** 0.5 / 32768.0
        zc = sum(1 for a, b in zip(samples, samples[1:]) if (a >= 0) != (b >= 0))
        zcr = zc / max(len(samples) - 1, 1)

        # 占位规则：能量高+过零率高，倾向 excited/angry；能量低，倾向 tired/sad。
        if rms > 0.12 and zcr > 0.08:
            label = "angry_or_excited"
            intensity = min(1.0, 0.55 + rms * 2.5)
            confidence = 0.45
        elif rms < 0.025:
            label = "tired_or_sad"
            intensity = 0.45
            confidence = 0.40
        else:
            label = "neutral"
            intensity = 0.35
            confidence = 0.50

        return EmotionResult(
            label=label,
            intensity=float(intensity),
            confidence=float(confidence),
            features={"rms": float(rms), "zcr": float(zcr), "duration": float(duration)},
        )


# ===== 真实模型接入占位示例 =====
# class SenseVoiceSER:
#     def __init__(self):
#         from funasr import AutoModel
#         self.model = AutoModel(model="iic/SenseVoiceSmall", trust_remote_code=True)
#
#     def predict(self, wav_path: Path) -> EmotionResult:
#         result = self.model.generate(input=str(wav_path), language="zh", use_itn=True)
#         # SenseVoice 通常会输出文本、语言、情绪、事件等 rich transcribe 信息。
#         # 这里把模型输出解析成 EmotionResult(label, intensity, confidence, features) 即可。
#         raise NotImplementedError
