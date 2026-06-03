from __future__ import annotations

from pathlib import Path

from .config import AppConfig


class ASRAdapter:
    """ASR 适配器。默认 mock，保证工程能跑通；真实模型后续在这里替换。"""

    def __init__(self, cfg: AppConfig):
        self.cfg = cfg

    def transcribe(self, wav_path: Path) -> str:
        if self.cfg.asr_backend == "mock":
            print("      [ASR mock] 当前未接真实 ASR 模型，请手动输入识别文本。")
            return input("请输入 ASR 识别文本：")

        if self.cfg.asr_backend == "sensevoice":
            return self._sensevoice_placeholder(wav_path)

        if self.cfg.asr_backend == "funasr":
            return self._funasr_placeholder(wav_path)

        if self.cfg.asr_backend == "whisper_cpp":
            return self._whisper_cpp_placeholder(wav_path)

        raise ValueError(f"未知 ASR_BACKEND: {self.cfg.asr_backend}")

    def _sensevoice_placeholder(self, wav_path: Path) -> str:
        # from funasr import AutoModel
        # model = AutoModel(model="iic/SenseVoiceSmall", trust_remote_code=True)
        # result = model.generate(input=str(wav_path), language="zh", use_itn=True)
        # return parse_text_from_result(result)
        raise NotImplementedError("请在 srtp_voice/asr.py 中接入 SenseVoiceSmall")

    def _funasr_placeholder(self, wav_path: Path) -> str:
        # from funasr import AutoModel
        # model = AutoModel(model="paraformer-zh", vad_model="fsmn-vad", punc_model="ct-punc")
        # result = model.generate(input=str(wav_path))
        # return result[0]["text"]
        raise NotImplementedError("请在 srtp_voice/asr.py 中接入 FunASR Paraformer")

    def _whisper_cpp_placeholder(self, wav_path: Path) -> str:
        # 推荐用 subprocess 调 whisper.cpp 可执行文件：
        # whisper-cli -m models/ggml-tiny.bin -f outputs/user_input.wav -l zh
        # 然后解析输出文本。
        raise NotImplementedError("请在 srtp_voice/asr.py 中接入 whisper.cpp")
