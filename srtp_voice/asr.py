from __future__ import annotations

from pathlib import Path

from .config import AppConfig


class ASRAdapter:
    """ASR 适配器。

    V2 对齐学长方案：优先预留 SenseVoiceSmall ONNX INT8 接口；默认 mock 仍可跑。
    """

    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        # 真实工程中建议在这里加载并预热模型，避免首次交互卡顿。
        # 例如：self.session = onnxruntime.InferenceSession(...)

    def transcribe(self, wav_path: Path) -> str:
        if self.cfg.asr_backend == "mock":
            print("      [ASR mock] 当前未接真实 ASR，返回固定占位文本。")
            return "这是语音识别占位结果"

        if self.cfg.asr_backend == "sensevoice_onnx":
            return self._sensevoice_onnx_placeholder(wav_path)

        if self.cfg.asr_backend == "sensevoice":
            return self._sensevoice_pytorch_placeholder(wav_path)

        if self.cfg.asr_backend == "funasr":
            return self._funasr_placeholder(wav_path)

        if self.cfg.asr_backend == "whisper_cpp":
            return self._whisper_cpp_placeholder(wav_path)

        raise ValueError(f"未知 ASR_BACKEND: {self.cfg.asr_backend}")

    def _sensevoice_onnx_placeholder(self, wav_path: Path) -> str:
        # 学长定稿对应方案：SenseVoiceSmall INT8 ONNX + ONNX Runtime。
        # 伪代码：
        # import onnxruntime as ort
        # session = ort.InferenceSession("models/sensevoice_small_int8.onnx", providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
        # audio = load_16k_pcm(wav_path)
        # inputs = preprocess_for_sensevoice(audio)
        # outputs = session.run(None, inputs)
        # return decode_sensevoice_text(outputs)
        raise NotImplementedError("请在 srtp_voice/asr.py 中接入 SenseVoiceSmall INT8 ONNX")

    def _sensevoice_pytorch_placeholder(self, wav_path: Path) -> str:
        # from funasr import AutoModel
        # model = AutoModel(model="iic/SenseVoiceSmall", trust_remote_code=True)
        # result = model.generate(input=str(wav_path), language="zh", use_itn=True)
        # return parse_text_from_result(result)
        raise NotImplementedError("请在 srtp_voice/asr.py 中接入 SenseVoiceSmall PyTorch/FunASR")

    def _funasr_placeholder(self, wav_path: Path) -> str:
        # from funasr import AutoModel
        # model = AutoModel(model="paraformer-zh", vad_model="fsmn-vad", punc_model="ct-punc")
        # result = model.generate(input=str(wav_path))
        # return result[0]["text"]
        raise NotImplementedError("请在 srtp_voice/asr.py 中接入 FunASR Paraformer")

    def _whisper_cpp_placeholder(self, wav_path: Path) -> str:
        # whisper-cli -m models/ggml-tiny.bin -f outputs/user_input.wav -l zh
        raise NotImplementedError("请在 srtp_voice/asr.py 中接入 whisper.cpp")
