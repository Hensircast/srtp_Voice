# SRTP 表情机器人语音通路 V1.0

V1.0 按完整语音通路拓展：

**音频采集 / VAD → Idle-Listening-Thinking-Speaking 状态机 → SER → 情绪状态平滑 → ASR → LLM → TTS → 短时能量唇动同步 → 动作策略 JSON**

当前真实模型仍以注释占位，默认离线可跑。

## 1. 最小运行

```powershell
.\venv\Scripts\activate
python main.py --mode console --text "测试 V1.0 主线" --no-play
```

## 2. 固定时长麦克风录音

```powershell
pip install -r requirements.txt
python main.py --mode mic --record-seconds 5
```

## 3. VAD 自动端点录音

```powershell
python main.py --mode vad
```

默认用短时能量 EnergyVAD 占位。后续在 `srtp_voice/vad.py` 中替换为 Silero VAD 或 Silero VAD ONNX。

## 4. 临时接入 edge-tts 真实语音

```powershell
$env:TTS_BACKEND="edge_tts"
$env:TTS_VOICE="zh-CN-XiaoxiaoNeural"
python main.py --mode console
```

edge-tts 会先生成临时 MP3，再通过 ffmpeg 转为 `outputs/reply.wav`。如未安装 ffmpeg，程序会给出明确错误；可用以下命令安装：

```powershell
winget install Gyan.FFmpeg
```

## 5. 对齐学长的模型替换点

- VAD：`srtp_voice/vad.py` → Silero VAD / Silero VAD ONNX
- ASR：`srtp_voice/asr.py` → SenseVoiceSmall INT8 ONNX
- TTS：`srtp_voice/tts.py` → MOSS-TTS-Nano ONNX
- 情绪平滑：`srtp_voice/emotion_state.py` → 对角卡尔曼滤波器
- 唇动同步：`srtp_voice/lip_sync.py` → 目前已实现短时能量 mouth_open 序列

## 6. 输出文件

- `outputs/user_input.wav`：用户语音
- `outputs/reply.wav`：回复语音
- `outputs/last_action.json`：表情动作、情绪状态、唇动同步参数
- `outputs/serial_packet.json`：唇动串口同步占位包
- `outputs/emotion_state.json`：平滑后的连续情绪状态
- `outputs/last_state.json`：对话状态机轨迹
- `outputs/memory.json`：短期对话记忆
