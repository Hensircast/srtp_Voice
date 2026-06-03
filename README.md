# SRTP 表情机器人语音通路 V1

这是一个先和学长系统模式对齐的“语音通路闭环”骨架：

麦克风/占位音频 -> 语音情绪识别 SER -> ASR 文本 -> LLM 回复与动作策略 -> TTS -> 播放/保存 -> 表情动作 JSON

当前版本的真实模型全部用注释占位，默认可以离线跑通主流程。

## 1. 最小运行

```bash
python main.py --mode console
```

运行后手动输入一句模拟 ASR 文本，程序会生成：

- `outputs/user_input.wav`：用户输入占位音频
- `outputs/reply.wav`：TTS 占位音频
- `outputs/last_action.json`：表情头动作策略
- `outputs/memory.json`：最近几轮短期记忆

## 2. 麦克风录音运行

```bash
pip install -r requirements.txt
python main.py --mode mic --record-seconds 5
```

当前 ASR 仍是占位，所以录音后需要手动输入识别文本。后续在 `srtp_voice/asr.py` 中替换为 SenseVoice / FunASR / whisper.cpp。

## 3. 使用已有 wav 文件

```bash
python main.py --mode file --audio path/to/input.wav --text "我现在语音部分做不下去了"
```

## 4. 推荐替换顺序

1. 先替换 ASR：SenseVoiceSmall 或 FunASR Paraformer
2. 再替换 TTS：Piper 本地 TTS 或 edge-tts 先演示
3. 最后替换 SER：SenseVoiceSmall 的 emotion tag 或专门 SER 模型
4. 表情头先读取 `outputs/last_action.json`，稳定后再改串口实时发送
