# SRTP 表情机器人语音交互项目

这是一个 Windows + Python 的机器人头部语音回路工程。当前主线是回合式语音交互：录音/文件输入 -> SER/ASR -> 本地 LLM -> TTS -> WAV 播放/唇动同步/动作策略 JSON。V1.4 增加可配置 SER 后端，默认使用轻量 RMS/ZCR 规则，也可加载本地 SenseVoiceSmall 模型。

## 当前主线

# SRTP 表情机器人语音通路 V1

V1.1 在 V1.0 完整语音通路基础上，接入本地 Ollama / LM Studio
大模型运行时，并增加结构化回复与动作策略、上下文控制和短期记忆。

**音频采集 / VAD → Idle-Listening-Thinking-Speaking 状态机
→ SER → 情绪状态平滑 → ASR → 本地 LLM
→ TTS → 短时能量唇动同步 → 动作策略 JSON**

当前 LLM 已支持本地真实模型运行；ASR、SER 和 TTS 仍可按配置使用
mock 或相应真实后端。

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
# Optional override. Leave unset to derive from LLM_OLLAMA_BASE_URL.
# Uncomment only for proxies, gateways, or non-standard endpoints.
# Custom chat endpoints do not need to expose /api/tags.
# LLM_OLLAMA_CHAT_URL=http://localhost:11434/api/chat
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
# Optional override. Leave unset to derive from LLM_LMSTUDIO_BASE_URL.
# Uncomment only for proxies, gateways, or non-standard endpoints.
# Custom chat endpoints do not need to expose /v1/models.
# LLM_LMSTUDIO_CHAT_URL=http://localhost:1234/v1/chat/completions
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

## 8. V1.2 faster-whisper 本地 ASR

V1.2 第一阶段保留现有 EnergyVAD、SER、情绪平滑、LLM、TTS、动作策略和串口流程，只把 ASR 后端扩展为本地 `faster-whisper`。实时录音端点仍由 `srtp_voice/vad.py` 中的 EnergyVAD 负责；`faster-whisper` 的 `vad_filter` 只在录音完成后的转写阶段做静音过滤，不是流式 ASR。

安装 ASR 额外依赖：

```powershell
python -m pip install -r requirements-asr.txt
```

配置 `.env`：

```text
ASR_BACKEND=faster_whisper
ASR_MODEL=small
ASR_DEVICE=cpu
ASR_COMPUTE_TYPE=int8
ASR_LANGUAGE=zh
ASR_CPU_THREADS=4
ASR_BEAM_SIZE=1
ASR_VAD_FILTER=1
ASR_MIN_SILENCE_MS=500
ASR_CONDITION_ON_PREVIOUS_TEXT=0
```

第一次按模型名加载 `small` 时，`faster-whisper` 可能需要下载模型缓存；不要把模型权重提交到 Git 仓库。Windows CPU 默认使用 `int8`，更适合轻量调试。

使用已有 WAV 文件：

```powershell
python main.py --mode file --audio recordings\test.wav --no-play
```

固定 5 秒麦克风录音后转写：

```powershell
python main.py --mode mic --record-seconds 5 --no-play
```

EnergyVAD 自动端点录音后转写：

```powershell
python main.py --mode vad --no-play
```

当前阶段不是流式 ASR；语音必须先保存为 WAV，再交给 `faster-whisper` 转写。

EnergyVAD 启动后会先进行短暂环境噪声校准。`VAD_THRESHOLD` 是最低启动阈值；如果环境噪声较大，程序会按噪声 RMS 自动提高有效启动阈值，并使用较低的释放阈值判断说话后的静音结束。调试时可设置：

```text
VAD_DEBUG=1
```

没有检测到有效语音时，本轮会安全结束并回到 Idle，不再生成占位语音。

## 9. V1.3 Piper 本地中文 TTS

V1.3 新增 `TTS_BACKEND=piper`，用于调用本地 `piper.exe` 和中文模型生成 `outputs/reply.wav`。Piper 是本地 TTS，不依赖网络；当前仍是回合式 TTS，不实现真正流式输入或流式输出。

本地目录约定：

```text
tools/piper/
models/piper/zh_CN-huayan-medium/
outputs/
```

推荐先用 Python subprocess 做 smoke test。不要使用 PowerShell 管道，不要使用 `--input-file`：

```powershell
python -c "import subprocess; text='你好，请简短回答。'; subprocess.run(['tools/piper/piper.exe','--model','models/piper/zh_CN-huayan-medium/model.onnx','--output_file','outputs/piper-smoke.wav'], input=(text+'\n').encode('utf-8'), check=True)"
```

`.env` 示例：

```text
TTS_BACKEND=piper
TTS_PIPER_EXE=tools/piper/piper.exe
TTS_PIPER_MODEL=models/piper/zh_CN-huayan-medium/model.onnx
TTS_PIPER_CONFIG=models/piper/zh_CN-huayan-medium/model.onnx.json
TTS_PIPER_TIMEOUT_SECONDS=60
TTS_PIPER_USE_JSON_INPUT=0
# TTS_PIPER_ESPEAK_DATA=tools/piper/espeak-ng-data
# TTS_PIPER_EXTRA_ARGS=
```

运行主流程：

```powershell
python .\main.py --mode console --text "你好，请简短回答。" --no-play
```

`--no-play` 只跳过播放，仍会生成 `outputs/reply.wav`，并继续生成唇动同步和动作策略 JSON。`tools/piper/`、`models/`、`outputs/`、音频文件都不应提交到 Git。

V1.3 仅新增 `srtp_voice/streaming.py` 作为后续版本的接口边界；真实流式 ASR、LLM、TTS 计划在 V1.7 再接入。

## 10. V1.4 本地 SenseVoice SER

V1.4 将语音情绪识别整理为统一的 `SpeechEmotionRecognizer(cfg)` 入口。默认 `heuristic` 后端只使用 WAV 的 RMS/ZCR，不加载模型；`sensevoice` 后端通过 FunASR 调用本地 SenseVoiceSmall。模型必须事先放在本机，代码不会自动下载，也不要将 `models/` 提交到 Git。

安装独立 SER 依赖：

```powershell
python -m pip install -r requirements-ser.txt
```

推荐本地模型目录：

```text
models/ser/SenseVoiceSmall/
```

`.env` 示例：

```text
SER_BACKEND=sensevoice
SER_MODEL=models/ser/SenseVoiceSmall
SER_DEVICE=cpu
SER_LANGUAGE=zh
SER_FALLBACK_TO_HEURISTIC=1
SER_TIMEOUT_SECONDS=30
```

真实模型 smoke test：

```powershell
python .\main.py --mode file --audio recordings\test.wav --text "SER smoke test" --no-play
```

SenseVoice 输出中的中文、英文和 `<|HAPPY|>` 等 rich-transcription 标签会统一映射为 `neutral`、`happy`、`sad`、`angry`、`fear`、`surprise`、`disgust`、`tired`、`excited` 或 `unknown`；语言、事件和 ITN token 会被忽略，没有情绪 token 时安全返回 `unknown`。SenseVoice 标准 `generate` 输出没有校准后的情绪强度或置信度，因此当前 `intensity=0.50` 和 `confidence=0.50` 只是适配层保守默认值，不是 SenseVoice 原始概率，也不能用于模型精度或校准评估。模型加载、推理或输出解析失败时，只有 `SER_FALLBACK_TO_HEURISTIC=1` 才会输出 warning 并回退到规则后端；设为 `0` 时直接抛出带失败阶段的错误。

V1.4 仍是完整 WAV 输入、完整结果输出的回合式处理，不是流式 SER。
