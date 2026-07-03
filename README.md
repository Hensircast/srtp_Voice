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

## 7. V1.1 本地 LLM 运行时

V1.1 默认使用 Ollama 作为本地 LLM 运行时，不连接 DeepSeek、OpenAI 等云端模型服务。模型权重应下载到本机，不要提交到 Git 仓库。

### Ollama 默认方式

安装并启动 Ollama 后，下载本地模型：

```powershell
ollama serve
ollama pull qwen3:4b-instruct
```

配置 `.env`：

```text
LLM_BACKEND=ollama
LLM_MODEL=qwen3:4b-instruct
LLM_OLLAMA_BASE_URL=http://localhost:11434
LLM_OLLAMA_CHAT_URL=http://localhost:11434/api/chat
LLM_FALLBACK_TO_MOCK=0
LLM_TEMPERATURE=0
LLM_MAX_TOKENS=512
LLM_CONTEXT_TOKENS=8192
LLM_TIMEOUT_SECONDS=180
```

Ollama 使用原生 `/api/chat`，请求中会设置 `stream=false`、完整 JSON Schema `format`、`temperature=0`、`num_predict=512` 和 `num_ctx=8192`，并且不会发送 `think` 字段，用于约束 qwen3 instruct 模型输出结构化 JSON。历史记录只会传入最近 `MAX_HISTORY_TURNS=3` 轮的 `user_text` 和 `reply_text`，不会把 action、唇动、串口包或状态机详情放进 LLM prompt。若 Ollama 返回 `done_reason=length`，表示模型输出被截断，程序会明确报错，不会尝试修补半截 JSON；可增大 `LLM_MAX_TOKENS` 后重试。若报错 `exceed_context_size_error`，请增大 `LLM_CONTEXT_TOKENS` 或减少历史。

运行：

```powershell
python main.py --mode console --text "你好，请给一个简短回复" --no-play
```

如果 Ollama 没有运行，程序会提示：

```powershell
ollama serve
```

如果模型没有下载，程序会提示：

```powershell
ollama pull <LLM_MODEL>
```

### LM Studio 兼容方式

在 LM Studio 中下载模型，进入 Local Server，加载模型并启动服务。配置：

```text
LLM_BACKEND=lmstudio
LLM_MODEL=<LM Studio 中显示的模型 id>
LLM_LMSTUDIO_BASE_URL=http://localhost:1234
LLM_LMSTUDIO_CHAT_URL=http://localhost:1234/v1/chat/completions
```

运行：

```powershell
python main.py --mode console --text "你好，请给一个简短回复" --no-play
```

### Mock 回退

本地 LLM 调用失败时，默认直接报错。演示时可开启 mock 回退：

```text
LLM_FALLBACK_TO_MOCK=1
```

### LLM 解析测试

不联网的结构化 JSON 解析测试：

```powershell
python tests\test_llm_parsing.py
```
