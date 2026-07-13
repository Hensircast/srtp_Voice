# SRTP 表情机器人语音交互项目

这是一个运行于 Windows + Python 的机器人头部语音交互工程。项目当前采用回合式处理，可从麦克风、VAD 自动录音、已有 WAV 文件或终端文本获取输入，并生成回复音频、唇动参数和动作策略。

## 1. 当前工作流

```text
输入音频或文本
→ Idle / Listening / Thinking / Speaking 状态机
→ SER 语音情绪识别
→ 情绪状态平滑
→ ASR 语音转文本
→ 短期记忆
→ 本地 LLM 回复与动作策略
→ TTS 合成 WAV
→ 短时能量唇动同步
→ 动作与串口占位 JSON
→ 播放或跳过播放
→ Idle
```

当前可配置的真实后端包括：

- ASR：本地 `faster-whisper`
- SER：本地 SenseVoiceSmall + FunASR
- LLM：本地 Ollama 或 LM Studio
- TTS：本地 Piper，或联网演示用 `edge-tts`

各模块保留 mock 或规则后端，便于离线调试。项目尚未实现流式 ASR、流式 TTS、独立服务或并行推理。

## 2. 环境准备

推荐环境：

- Windows 10/11
- PowerShell
- Python venv
- 16 kHz、单声道麦克风输入

创建并激活虚拟环境：

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

## 3. 安装依赖

基础依赖：

```powershell
python -m pip install -r requirements.txt
```

使用 `faster-whisper` ASR 时：

```powershell
python -m pip install -r requirements-asr.txt
```

使用 SenseVoice SER 时：

```powershell
python -m pip install -r requirements-ser.txt
```

Piper 使用本地 `piper.exe`，不需要安装 Piper Python 包。模型、Piper 工具和运行输出均不应提交到 Git。

## 4. `.env` 配置

程序启动时通过 `python-dotenv` 加载项目根目录的 `.env`：

```powershell
Copy-Item .env.example .env
```

当前 PowerShell 进程中已设置的环境变量优先于 `.env`。排查配置时，应同时检查两处。

常用配置如下：

| 环境变量 | 默认值 | 作用 |
| --- | --- | --- |
| `VAD_BACKEND` | `energy` | 录音端点检测后端 |
| `VAD_THRESHOLD` | `0.004` | VAD 最低启动阈值 |
| `VAD_DEBUG` | `0` | 输出 VAD RMS 和阈值诊断信息 |
| `ASR_BACKEND` | `mock` | `mock` 或 `faster_whisper` |
| `ASR_MODEL` | `small` | faster-whisper 模型名或路径 |
| `SER_BACKEND` | `heuristic` | `heuristic`、`sensevoice` 或预留的 `custom` |
| `SER_MODEL` | 未设置 | SenseVoice 本地模型路径；未设置时使用 `models/ser/SenseVoiceSmall` |
| `SER_DEVICE` | `cpu` | SenseVoice 运行设备 |
| `SER_FALLBACK_TO_HEURISTIC` | `1` | SenseVoice 失败时是否回退到规则 SER |
| `LLM_BACKEND` | `ollama` | `mock`、`ollama` 或 `lmstudio` |
| `LLM_MODEL` | `qwen3:4b-instruct` | 本地 LLM 模型名 |
| `LLM_FALLBACK_TO_MOCK` | `0` | 本地 LLM 失败时是否回退到 mock |
| `TTS_BACKEND` | `mock` | `mock`、`piper` 或 `edge_tts` |

VAD 还支持环境噪声校准、动态启动阈值和释放阈值。常用补充项包括 `VAD_CALIBRATION_MS`、`VAD_NOISE_MULTIPLIER`、`VAD_RELEASE_RATIO`、`MIN_SPEECH_MS`、`SILENCE_MS` 和 `PRE_ROLL_MS`，具体默认值见 `.env.example`。

## 5. 运行方式

### 5.1 console 模式

不使用麦克风，直接输入文本。`--text` 可避免交互式输入：

```powershell
python .\main.py --mode console --text "你好，请简短回答。" --no-play
```

console 模式会生成占位用户 WAV，以便继续执行 SER、LLM、TTS 和唇动流程。

### 5.2 mic 模式

按固定时长录音，默认 5 秒：

```powershell
python .\main.py --mode mic --record-seconds 5 --no-play
```

mic 模式支持 `--continuous`，每轮都会重新执行固定时长录音：

```powershell
python .\main.py --mode mic --record-seconds 5 --continuous --no-play
```

### 5.3 VAD 单轮模式

EnergyVAD 会先进行环境噪声校准，然后自动判断说话开始和静音结束：

```powershell
python .\main.py --mode vad --no-play
```

单轮模式在完成一次处理或未检测到有效语音后退出。

### 5.4 VAD continuous 模式

推荐的常驻多轮命令：

```powershell
python .\main.py --mode vad --continuous --no-play
```

启用回复音频播放：

```powershell
python .\main.py --mode vad --continuous
```

continuous 模式只支持 `vad` 和 `mic`。`console` 或 `file` 配合 `--continuous` 会得到明确的参数错误。

### 5.5 file 模式

使用已有 WAV 文件测试 SER、ASR 和后续流程：

```powershell
python .\main.py --mode file --audio .\path\to\audio.wav --no-play
```

`file` 模式要求提供 `--audio`，并且文件必须存在。

## 6. continuous 模式说明

continuous 模式保持 `main.py` 在同一个 Python 进程中运行：

```text
程序启动
→ 加载配置并创建 SER
→ 预加载 SenseVoice SER 模型（启用 sensevoice 时）
→ 初始化其余运行时对象
→ VAD 或固定时长麦克风录音
→ 完成 SER、情绪平滑、ASR、LLM、TTS、唇动和输出
→ 状态机回到 Idle
→ 自动开始下一轮监听
→ Ctrl+C 正常退出
```

SenseVoice 模型只在启动时加载一次，后续各轮复用同一个 `SpeechEmotionRecognizer` 和已加载模型，从而避免每轮交互重复冷启动。第一次启动仍需导入 FunASR 并把模型权重加载到内存，continuous 模式不能消除这部分首次等待时间。

ASR、LLM、TTS 适配器、情绪平滑器和短期记忆对象同样在循环外创建并复用。Piper 每轮仍通过本地可执行文件完成一次 WAV 合成；这不等同于流式 TTS。

按 `Ctrl+C` 后，程序捕获中断，将状态机保存为 Idle，并正常退出，不输出冗长 traceback。

continuous 模式不是独立服务，不使用 FastAPI、HTTP、额外后台进程、多线程或异步并行。

### `--no-play`

`--no-play` 只跳过 `outputs/reply.wav` 的播放。TTS 合成、唇动参数、动作策略、状态文件和短期记忆仍按当前工作流执行，适合调试和验证。

## 7. 本地模型配置

### 7.1 faster-whisper ASR

CPU 调试配置：

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

按模型名首次加载时，`faster-whisper` 可能下载模型缓存。其内置 VAD 只在录音完成后过滤静音；实时录音端点仍由 EnergyVAD 负责。当前 ASR 是完整 WAV 输入，不是流式 ASR。

### 7.2 SenseVoice SER

推荐目录：

```text
models/ser/SenseVoiceSmall/
```

配置示例：

```text
SER_BACKEND=sensevoice
SER_MODEL=models/ser/SenseVoiceSmall
SER_DEVICE=cpu
SER_LANGUAGE=zh
SER_FALLBACK_TO_HEURISTIC=1
```

SenseVoice 使用本地模型路径，并设置 `disable_update=True`，不会由项目代码自动下载或更新模型。启动时会打印：

```text
[INIT] 正在预加载 SenseVoice SER 模型
[INIT] SenseVoice SER 模型加载完成
```

适配层从 `<|NEUTRAL|>`、`<|HAPPY|>` 等 token 提取情绪，并统一为 `neutral`、`happy`、`sad`、`angry`、`fear`、`surprise`、`disgust`、`tired`、`excited` 或 `unknown`。语言、事件和 ITN token 不会被当作情绪；没有情绪 token 时返回 `unknown`。

`intensity=0.50` 和 `confidence=0.50` 是当前适配层默认值，不是 SenseVoice 原始概率，也不应用于模型准确率评估。

### 7.3 Ollama LLM

启动 Ollama 并准备模型：

```powershell
ollama serve
ollama pull qwen3:4b-instruct
```

配置示例：

```text
LLM_BACKEND=ollama
LLM_MODEL=qwen3:4b-instruct
LLM_OLLAMA_BASE_URL=http://localhost:11434
LLM_FALLBACK_TO_MOCK=0
LLM_TEMPERATURE=0
LLM_MAX_TOKENS=512
LLM_CONTEXT_TOKENS=8192
LLM_TIMEOUT_SECONDS=180
```

Ollama 使用原生 `/api/chat`、非流式响应和 JSON Schema。未显式配置 `LLM_OLLAMA_CHAT_URL` 时，聊天地址从 Base URL 自动派生；自定义代理或网关可显式设置该变量。

历史只传入最近 `MAX_HISTORY_TURNS` 轮的 `user_text` 和 `reply_text`。若返回 `done_reason=length`，可提高 `LLM_MAX_TOKENS`；若出现上下文超限，可提高 `LLM_CONTEXT_TOKENS` 或减少历史轮数。

### 7.4 LM Studio LLM

在 LM Studio 中加载模型并启动 Local Server，然后配置：

```text
LLM_BACKEND=lmstudio
LLM_MODEL=<LM Studio 中显示的模型 id>
LLM_LMSTUDIO_BASE_URL=http://localhost:1234
```

未显式配置 `LLM_LMSTUDIO_CHAT_URL` 时，聊天地址从 Base URL 自动派生。自定义 endpoint 不要求实现 `/v1/models` 预检接口。

### 7.5 Piper TTS

目录约定：

```text
tools/piper/
models/piper/zh_CN-huayan-medium/
```

配置示例：

```text
TTS_BACKEND=piper
TTS_PIPER_EXE=tools/piper/piper.exe
TTS_PIPER_MODEL=models/piper/zh_CN-huayan-medium/model.onnx
# TTS_PIPER_CONFIG=models/piper/zh_CN-huayan-medium/model.onnx.json
TTS_PIPER_TIMEOUT_SECONDS=60
TTS_PIPER_USE_JSON_INPUT=0
```

`TTS_PIPER_CONFIG` 可省略；默认会尝试 `<TTS_PIPER_MODEL>.json`。Piper 通过 Python subprocess 的 UTF-8 stdin 接收文本，不使用 PowerShell 管道，也不使用 `--input-file`。

本地 smoke test：

```powershell
python -c "import subprocess; text='你好，请简短回答。'; subprocess.run(['tools/piper/piper.exe','--model','models/piper/zh_CN-huayan-medium/model.onnx','--output_file','outputs/piper-smoke.wav'], input=(text+'\n').encode('utf-8'), check=True)"
```

### 7.6 edge-tts

联网演示配置：

```powershell
$env:TTS_BACKEND="edge_tts"
$env:TTS_VOICE="zh-CN-XiaoxiaoNeural"
python .\main.py --mode console --text "你好" --no-play
```

`edge-tts` 先生成 MP3，再调用 FFmpeg 转为 `outputs/reply.wav`。未安装 FFmpeg 时程序会给出明确错误。

## 8. 输出文件

| 文件 | 内容 |
| --- | --- |
| `outputs/user_input.wav` | 当前轮用户录音或 console 占位音频 |
| `outputs/reply.wav` | 当前轮 TTS 回复音频 |
| `outputs/last_action.json` | 回复文本、表情动作、情绪状态和唇动参数 |
| `outputs/serial_packet.json` | 下位机串口动作包占位输出 |
| `outputs/emotion_state.json` | 平滑后的连续情绪状态 |
| `outputs/last_state.json` | 状态机阶段和轨迹 |
| `outputs/memory.json` | 最近若干轮短期记忆 |

continuous 模式会覆盖当前轮音频和状态输出，不会为每轮自动创建新文件。

## 9. 常见问题

### VAD 无法检测到有效语音

1. 确认项目根目录的 `.env` 已加载。
2. 检查当前 PowerShell 环境变量是否覆盖 `.env`。
3. 设置 `VAD_DEBUG=1`，观察 RMS、启动阈值和释放阈值。
4. 检查 Windows 默认录音设备是否正确。
5. VAD 启动后的校准阶段保持安静。

环境噪声较大时，EnergyVAD 会自动提高有效启动阈值。continuous 模式下未检测到有效语音会结束当前轮并继续监听；单轮模式则回到 Idle 后退出。

### SenseVoice 首次启动较慢

主要耗时通常来自 FunASR 导入和模型权重加载。continuous 模式后续轮次复用已加载模型，避免重复冷启动。FunASR 日志中的 `rtf_avg` 主要反映推理速度，不包含全部初始化时间。

### 日志出现 `trust_remote_code: False`

这是 FunASR 日志，表示未启用远程自定义代码，不是运行错误。当前项目没有向 `AutoModel` 传入 `trust_remote_code=True` 或 `remote_code`。

### 为什么情绪强度和置信度总是 `0.50`

SenseVoice 标准输出没有提供可直接用于本项目的校准后情绪强度和置信度。当前两个值是适配层默认值，不代表模型概率。

### Ollama 无法连接或模型不存在

检查服务和模型：

```powershell
ollama serve
ollama list
```

需要时下载配置中的模型：

```powershell
ollama pull qwen3:4b-instruct
```

## 10. 测试与验证

运行全部离线测试：

```powershell
python -m pytest -q
```

基础验证：

```powershell
python -m compileall .\main.py .\srtp_voice
git diff --check
python -m pip check
```

测试使用 fake 后端和临时目录，不需要启动真实 Ollama、Piper、SenseVoice、faster-whisper 模型或麦克风。

## 11. 当前限制

- 当前仍是完整录音、完整识别、完整回复的回合式处理。
- continuous 模式是同一进程内的同步循环，不是服务，也不表示可以无限期无故障运行。
- 未实现流式 ASR、流式 LLM、流式 TTS、多线程并行或异步流水线。
- SER 未转换为 ONNX；SenseVoice 当前通过 FunASR 本地运行。
- 唇动同步基于短时能量，不是音素或 viseme 级口型同步。
- 串口部分当前以动作包 JSON 为主，真实下位机联调仍需结合硬件配置。
