# SRTP 表情机器人语音交互项目

## 1. 项目定位

本项目是回合式机器人头部语音交互程序。它在单个 Python 进程中组织音频采集、语音理解、回复生成、语音合成和动作文件输出。

```text
音频或文本输入
→ mic/vad 模式下的录音与 EnergyVAD
→ SER 语音情绪识别
→ 情绪状态平滑
→ ASR 语音转文本
→ 短期记忆与 LLM
→ TTS 回复 WAV
→ 短时能量唇动参数
→ 动作策略与串口数据文件
→ 播放或跳过播放
→ Idle
```

Windows 已完成本地真实工作流验证。Ubuntu 当前完成了代码适配、跨平台路径处理和离线测试准备；原生 Ubuntu 主机上的真实麦克风、扬声器、串口和模型推理仍待验证。

## 2. 已实现后端

| 模块 | 后端 | 说明 |
| --- | --- | --- |
| VAD | `energy` | 实时录音端点检测，带环境噪声校准和迟滞阈值 |
| ASR | `mock`、`faster_whisper` | 占位文本或本地完整 WAV 识别 |
| SER | `heuristic`、`sensevoice` | RMS/ZCR 规则或本地 SenseVoiceSmall |
| LLM | `mock`、`ollama`、`lmstudio` | 占位策略或本地 LLM 运行时 |
| TTS | `mock`、`edge_tts`、`piper` | 占位 WAV、联网 TTS 或本地 Piper |

默认配置保持轻量：ASR 为 `mock`、SER 为 `heuristic`、TTS 为 `mock`；LLM 默认为本地 Ollama。

## 3. 环境准备

### 3.1 Python 版本

CI 基准使用 Python 3.11。当前 Windows 开发记录曾使用 Python 3.12，但不能据此推断所有可选模型依赖在每个 Python 小版本上均已验证；安装 FunASR、torchaudio 或 faster-whisper 前应确认其平台和 Python 版本兼容性。

升级 `pip` 不是必需步骤。需要升级时可由使用者按本机环境决定。

### 3.2 Windows PowerShell

在项目根目录创建并激活虚拟环境：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

按需安装真实 ASR 或 SER 依赖：

```powershell
python -m pip install -r requirements-asr.txt
python -m pip install -r requirements-ser.txt
```

`edge-tts` 将 MP3 转为 WAV 时需要 FFmpeg：

```powershell
winget install --id Gyan.FFmpeg --exact
```

安装后先运行只读诊断：

```powershell
python .\main.py --diagnose
```

### 3.3 原生 Ubuntu Bash

安装 Python 虚拟环境及音频系统库：

```bash
sudo apt update
sudo apt install python3-venv portaudio19-dev libsndfile1 ffmpeg
```

创建环境并安装基础依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

按需安装真实 ASR 或 SER 依赖：

```bash
python -m pip install -r requirements-asr.txt
python -m pip install -r requirements-ser.txt
```

运行只读诊断：

```bash
python main.py --diagnose
```

PortAudio、libsndfile 和 FFmpeg 由 Ubuntu 系统包提供。CI 不需要真实模型或音频设备，因此 CI workflow 不执行上述 `apt` 安装。本项目尚无 WSL 或原生 Ubuntu 真实设备验证记录。

## 4. 最小磁盘安装

依赖按用途分层：

| 需求 | 安装文件 | 内容 |
| --- | --- | --- |
| 基础运行和离线测试 | `requirements.txt` | 音频 Python 接口、配置、HTTP 客户端、edge-tts、串口库和 pytest |
| 真实 faster-whisper ASR | `requirements-asr.txt` | 基础依赖及 `faster-whisper` |
| 真实 SenseVoice SER | `requirements-ser.txt` | 基础依赖、`funasr` 和 `torchaudio` |

不使用真实 ASR 时无需安装 `requirements-asr.txt`；不使用真实 SER 时无需安装 `requirements-ser.txt`。Piper 二进制和 ONNX 模型不属于 Python requirements。

项目不会在运行期间执行 `pip install`、`ollama pull` 或 Piper 下载命令，也不把模型权重写入 Git 跟踪目录。`faster-whisper` 以模型名首次加载且本地缓存不存在时，其依赖库可能尝试下载模型；严格离线运行应将 `ASR_MODEL` 指向已准备好的本地模型目录。

不要提交 `.venv`、`venv`、`models`、`tools/piper`、音频文件、`outputs` 或模型缓存。

## 5. `.env` 配置

程序从 `config.py` 所在仓库根目录加载 `.env`，不依赖启动命令的当前目录来查找该文件。已有 shell 环境变量优先于 `.env`，因为加载时不会覆盖现有环境变量。

建议仍从项目根目录运行程序，使 `outputs`、`models/...`、`tools/...` 等相对路径保持直观。`.env` 必须放在项目根目录：

```powershell
Copy-Item .env.example .env
```

```bash
cp .env.example .env
```

### 5.1 通用与状态文件

| 环境变量 | 示例或默认值 | 作用 |
| --- | --- | --- |
| `SAMPLE_RATE` | `16000` | 录音、mock TTS 和 FFmpeg WAV 输出采样率 |
| `OUTPUT_DIR` | `outputs` | 当前轮音频、动作、串口包和状态输出目录 |
| `MEMORY_FILE` | `outputs/memory.json` | 短期对话记忆 |
| `STATE_FILE` | `outputs/emotion_state.json` | EMA 情绪平滑状态 |
| `MAX_HISTORY_TURNS` | `3` | 传入 LLM 和保留的最近轮数；小于等于 0 时禁用记忆 |
| `EMOTION_SMOOTH_ALPHA` | `0.35` | 情绪状态 EMA 更新比例 |

### 5.2 EnergyVAD

| 环境变量 | 默认值 | 作用 |
| --- | --- | --- |
| `VAD_BACKEND` | `energy` | 当前唯一 VAD 后端 |
| `VAD_THRESHOLD` | `0.004` | 动态启动阈值的最低值 |
| `FRAME_MS` | `32` | PCM16 检测帧长度 |
| `MIN_SPEECH_MS` | `160` | 触发录音所需语音时长 |
| `SILENCE_MS` | `1000` | 已触发后结束录音所需静音时长 |
| `MAX_RECORD_SECONDS` | `15` | 校准和监听总时长上限 |
| `PRE_ROLL_MS` | `400` | 触发前保留的音频 |
| `VAD_CALIBRATION_MS` | `800` | 启动时环境噪声采样时长 |
| `VAD_NOISE_MULTIPLIER` | `3.0` | 噪声底到启动阈值的倍率 |
| `VAD_RELEASE_RATIO` | `0.60` | 触发后的释放阈值比例 |
| `VAD_DEBUG` | `0` | 设为 `1` 时周期性输出 RMS 和阈值 |

### 5.3 faster-whisper ASR

| 环境变量 | 默认值 | 作用 |
| --- | --- | --- |
| `ASR_BACKEND` | `mock` | 使用 `mock` 或 `faster_whisper` |
| `ASR_MODEL` | `small` | faster-whisper 模型名或本地模型目录 |
| `ASR_DEVICE` | `cpu` | 推理设备 |
| `ASR_COMPUTE_TYPE` | `int8` | CPU 默认计算类型 |
| `ASR_LANGUAGE` | `zh` | 转写语言 |
| `ASR_CPU_THREADS` | `4` | CPU 线程数 |
| `ASR_BEAM_SIZE` | `1` | beam size |
| `ASR_VAD_FILTER` | `1` | 录音完成后的静音过滤 |
| `ASR_MIN_SILENCE_MS` | `500` | 内置 VAD 最短静音时长 |
| `ASR_CONDITION_ON_PREVIOUS_TEXT` | `0` | 是否使用前文条件 |

faster-whisper 内置 VAD 只处理已经录制的文件；`vad` 模式的实时开始和结束仍由 EnergyVAD 控制。

### 5.4 SenseVoice SER

| 环境变量 | 默认值 | 作用 |
| --- | --- | --- |
| `SER_BACKEND` | `heuristic` | 使用 `heuristic` 或 `sensevoice` |
| `SER_MODEL` | 未设置 | SenseVoice 本地目录；未设置时使用 `models/ser/SenseVoiceSmall` |
| `SER_DEVICE` | `cpu` | SenseVoice 运行设备 |
| `SER_LANGUAGE` | `zh` | 传给 SenseVoice 的语言参数 |
| `SER_FALLBACK_TO_HEURISTIC` | `1` | warmup 或推理失败时回退到规则后端 |

SenseVoice 初始化使用本地模型、`disable_update=True` 和 `disable_pbar=True`。标准输出没有本项目可直接使用的校准后情绪概率，因此 `intensity=0.50`、`confidence=0.50` 是适配层默认值，不应用于模型精度评估。

### 5.5 Ollama 与 LM Studio

| 环境变量 | 默认值 | 作用 |
| --- | --- | --- |
| `LLM_BACKEND` | `ollama` | 使用 `mock`、`ollama` 或 `lmstudio` |
| `LLM_MODEL` | `qwen3:4b-instruct` | 本地运行时中的精确模型标签 |
| `LLM_OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama 服务基址 |
| `LLM_OLLAMA_CHAT_URL` | 未设置 | 可选自定义聊天 endpoint；默认从 Base URL 派生 `/api/chat` |
| `LLM_LMSTUDIO_BASE_URL` | `http://localhost:1234` | LM Studio 服务基址 |
| `LLM_LMSTUDIO_CHAT_URL` | 未设置 | 可选自定义 endpoint；默认派生 `/v1/chat/completions` |
| `LLM_FALLBACK_TO_MOCK` | `0` | 本地 LLM 失败时是否回退到 mock |
| `LLM_TEMPERATURE` | `0` | 生成温度 |
| `LLM_MAX_TOKENS` | `512` | 最大输出 token 数 |
| `LLM_CONTEXT_TOKENS` | `8192` | Ollama 上下文窗口配置 |
| `LLM_TIMEOUT_SECONDS` | `180` | HTTP 请求超时 |

标准派生 endpoint 会执行本地模型列表预检；显式 Chat URL 用于代理、网关或非标准 endpoint，并跳过对应 Base URL 的模型列表预检。项目不需要云端 API key，也不会打印 Token 或完整环境变量。

### 5.6 TTS 与 Piper

| 环境变量 | 默认值或示例 | 作用 |
| --- | --- | --- |
| `TTS_BACKEND` | `mock` | 使用 `mock`、`edge_tts` 或 `piper` |
| `TTS_VOICE` | `zh-CN-XiaoxiaoNeural` | edge-tts voice |
| `TTS_PIPER_EXE` | 平台默认 | 可选显式 Piper 可执行文件路径 |
| `TTS_PIPER_MODEL` | `.env.example` 中为占位路径 | Piper ONNX 模型，启用前必须改为真实文件 |
| `TTS_PIPER_CONFIG` | 未设置 | 可选配置；默认尝试 `<TTS_PIPER_MODEL>.json` |
| `TTS_PIPER_TIMEOUT_SECONDS` | `60` | Piper 子进程超时 |
| `TTS_PIPER_USE_JSON_INPUT` | `0` | 是否通过单行 UTF-8 JSON 输入 |
| `TTS_PIPER_ESPEAK_DATA` | 未设置 | 可选 espeak-ng-data 目录 |
| `TTS_PIPER_EXTRA_ARGS` | 未设置 | 可选 Piper 参数；不能覆盖输出路径 |

## 6. 本地模型与权重

Windows 已验证的真实工作流使用以下本地资源：

- `models/ser/SenseVoiceSmall` 中的 SenseVoiceSmall。
- faster-whisper `small` 模型或对应本地缓存。
- Ollama 中的 Qwen3 4B instruct/no-thinking 模型；代码和 `.env.example` 的参考标签为 `qwen3:4b-instruct`。
- Piper 可执行文件和中文 ONNX 模型。

`LLM_MODEL` 必须与本机 `ollama list` 显示的标签完全一致，不应只按模型系列名称猜测：

```powershell
ollama list
```

```bash
ollama list
```

模型“权重”就是上述模型目录、ONNX 文件或 Ollama 模型存储中的数值参数。真实推理已经使用这些文件时，不需要再安装一份名称不同的额外权重。仓库不提交任何模型文件。

## 7. Piper 跨平台配置

未设置 `TTS_PIPER_EXE` 时，平台默认候选为：

| 平台 | 默认候选 |
| --- | --- |
| Windows | `tools/piper/piper.exe` |
| Ubuntu 及其他非 Windows 平台 | `tools/piper/piper` |

显式 `TTS_PIPER_EXE` 始终优先。`TTS_PIPER_MODEL` 可以使用项目相对路径，也可以使用当前平台原生绝对路径；不要把 Windows 路径复制到 Ubuntu，或反向混用。

Ubuntu 上必须给二进制执行权限：

```bash
chmod +x tools/piper/piper
```

Piper 通过 Python `subprocess` 的 UTF-8 stdin 接收文本，不使用 PowerShell 管道，也不使用 `--input-file`。Piper ONNX 模型和二进制均不提交到 Git。

Windows smoke test：

```powershell
python -c "import subprocess; from pathlib import Path; out=Path('outputs/piper-smoke.wav'); out.parent.mkdir(exist_ok=True); subprocess.run(['tools/piper/piper.exe','--model','models/piper/zh_CN-huayan-medium/model.onnx','--output_file',str(out)], input='你好\n'.encode('utf-8'), check=True)"
```

Ubuntu smoke test：

```bash
python -c "import subprocess; from pathlib import Path; out=Path('outputs/piper-smoke.wav'); out.parent.mkdir(exist_ok=True); subprocess.run(['tools/piper/piper','--model','models/piper/zh_CN-huayan-medium/model.onnx','--output_file',str(out)], input='你好\n'.encode('utf-8'), check=True)"
```

## 8. 运行模式

### 8.1 Windows PowerShell

```powershell
# console：交互输入文本
python .\main.py --mode console

# console：直接提供文本
python .\main.py --mode console --text "你好，请简短回答。" --no-play

# 使用已有 WAV
python .\main.py --mode file --audio .\recordings\test.wav --no-play

# 固定录音 5 秒
python .\main.py --mode mic --record-seconds 5 --no-play

# EnergyVAD 单轮录音
python .\main.py --mode vad --no-play

# EnergyVAD 常驻多轮
python .\main.py --mode vad --continuous --no-play

# 只运行环境诊断
python .\main.py --diagnose
```

### 8.2 Ubuntu Bash

```bash
# console：交互输入文本
python main.py --mode console

# console：直接提供文本
python main.py --mode console --text "你好，请简短回答。" --no-play

# 使用已有 WAV
python main.py --mode file --audio ./recordings/test.wav --no-play

# 固定录音 5 秒
python main.py --mode mic --record-seconds 5 --no-play

# EnergyVAD 单轮录音
python main.py --mode vad --no-play

# EnergyVAD 常驻多轮
python main.py --mode vad --continuous --no-play

# 只运行环境诊断
python main.py --diagnose
```

`--continuous` 仅支持 `mic` 和 `vad`。它在同一 Python 进程中复用 SER、ASR、LLM、TTS、情绪平滑和记忆对象；SenseVoice 在启动时 warmup 一次。每轮结束后状态机回到 Idle，再进入下一轮；按 `Ctrl+C` 会保存 Idle 状态并正常退出。

`--no-play` 只跳过回复 WAV 播放，不跳过 TTS、唇动、动作文件或记忆写入。`--text` 直接提供当前轮用户文本，可绕过 ASR，但音频模式本身仍按所选模式采集或读取音频。

`--diagnose` 在输出目录和完整工作流对象初始化之前返回。它不会录音、播放、加载 SenseVoice 或 faster-whisper、调用 Ollama/Piper/edge-tts、创建 `outputs`，也不会输出 API key、Token 或完整环境变量。

## 9. 输出文件

| 文件 | 内容 |
| --- | --- |
| `outputs/user_input.wav` | 当前轮麦克风录音或 console 占位 WAV |
| `outputs/reply.wav` | 当前轮 TTS 回复 |
| `outputs/last_action.json` | 回复、动作策略、情绪状态和唇动参数 |
| `outputs/serial_packet.json` | 待下位机消费的动作包文件 |
| `outputs/emotion_state.json` | EMA 情绪平滑状态 |
| `outputs/last_state.json` | 状态机阶段和轨迹 |
| `outputs/memory.json` | 最近若干轮对话记忆 |

continuous 模式继续覆盖这些当前轮文件，不为每轮创建新目录。

## 10. 唇动与串口现状

`lip_sync` 从 TTS 输出 WAV 计算短时能量，默认每 40 ms 生成一个 `mouth_open`。它不是摄像头嘴形测量，也不是音素或 viseme 级同步。

`servo_targets` 当前来自 LLM 动作中的 `servo_targets_placeholder`。`main.py` 只生成并保存 `outputs/serial_packet.json`，尚未自动调用真实串口发送。

预留的 `send_serial_packet()` 接口会将调用方提供的端口名称原样传给 pyserial，因此可以接收 `COM3`、`/dev/ttyACM0`、`/dev/ttyUSB0` 等名称；项目不会自动扫描端口。

Ubuntu 用户如需后续手动调用串口接口，可加入 `dialout` 组：

```bash
sudo usermod -aG dialout $USER
```

重新登录后权限才会生效。当前尚未实现 STM32 舵机闭环；舵机角度映射、ACK、CRC、限位、重连、状态反馈和急停属于 V1.7 范围。

## 11. 常见问题

### 11.1 PowerShell 无法执行 `Activate.ps1`

可只对当前 PowerShell 进程放宽执行策略：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

也可以不激活环境，直接运行 `.\.venv\Scripts\python.exe`。

### 11.2 sounddevice、PortAudio、libsndfile 或默认设备不可用

先运行：

```powershell
python .\main.py --diagnose
```

```bash
python main.py --diagnose
```

诊断会把依赖缺失、PortAudio 查询失败、没有默认输入/输出设备等情况标记为 warning，并保留简短异常类型和上下文。程序目前使用操作系统默认设备，没有麦克风或扬声器选择参数。

Ubuntu 缺少系统库时执行：

```bash
sudo apt install portaudio19-dev libsndfile1
```

### 11.3 VAD 无法触发

确认项目根目录 `.env` 已加载，并检查 shell 环境变量是否覆盖其中配置。设置 `VAD_DEBUG=1`，检查默认麦克风，并在校准阶段保持安静。动态阈值会随环境噪声提高；未检测到语音时不会生成伪造用户音频。

### 11.4 edge-tts 已生成 MP3，但无法转换 WAV

这是 FFmpeg 缺失。分别使用对应平台命令：

```powershell
winget install --id Gyan.FFmpeg --exact
```

```bash
sudo apt install ffmpeg
```

### 11.5 Piper 文件、权限或模型路径错误

- `piper executable not found`：检查平台默认路径或 `TTS_PIPER_EXE`。
- Linux 上 `piper executable is not executable`：执行 `chmod +x tools/piper/piper`。
- `piper model not found`：把 `TTS_PIPER_MODEL` 改为真实 ONNX 文件；`.env.example` 中是占位文件名。
- `TTS_PIPER_CONFIG` 通常留空；模型配置不在 `<TTS_PIPER_MODEL>.json` 时才显式填写。

### 11.6 SenseVoice 模型路径错误或首次启动较慢

`sensevoice` 只读取本地 `SER_MODEL`，默认候选为 `models/ser/SenseVoiceSmall`。模型不存在、FunASR 缺失或 warmup 失败时，`SER_FALLBACK_TO_HEURISTIC=1` 会发出 warning 并切换到 heuristic；设为 `0` 时会保留异常链并终止。

首次等待主要来自 FunASR 导入和模型加载。continuous 模式只消除后续轮次的重复加载，不能消除首次冷启动。FunASR 日志中的 `rtf_avg` 主要反映推理速度，不包含全部初始化时间；`trust_remote_code: False` 表示未启用远程自定义代码，不是错误。

### 11.7 faster-whisper 不可用

缺少依赖时安装 `requirements-asr.txt`。模型初始化失败时检查 `ASR_MODEL`、`ASR_DEVICE` 和 `ASR_COMPUTE_TYPE`；严格离线环境应使用已经准备好的本地模型目录，避免按模型名查找远程缓存。

### 11.8 Ollama 未启动或模型标签不匹配

启动服务并检查精确标签：

```powershell
ollama serve
ollama list
```

```bash
ollama serve
ollama list
```

模型缺失时由用户显式下载，例如当前参考标签：

```powershell
ollama pull qwen3:4b-instruct
```

```bash
ollama pull qwen3:4b-instruct
```

真实验收建议保持 `LLM_FALLBACK_TO_MOCK=0`，避免本地 LLM 错误被 mock 回复掩盖。

### 11.9 Ubuntu 串口权限不足

执行 `sudo usermod -aG dialout $USER` 并重新登录。请注意：主流程当前不会自动发送串口，权限只影响后续显式调用发送接口的测试。

### 11.10 `--continuous` 用于 console 或 file

当前实现会抛出：`--continuous 仅支持 --mode vad 或 --mode mic`。console 和 file 请使用单轮模式。

### 11.11 `.env` 未生效

`.env` 只从项目根目录加载。确认文件位于 README 和 `main.py` 同级目录，并检查当前 PowerShell/Bash 环境变量，因为已有环境变量优先。相对模型和输出路径建议从项目根目录运行。

## 12. 测试与 Windows/Ubuntu CI

本地基础验证命令在 PowerShell 和 Bash 中相同：

```text
python -m compileall main.py srtp_voice
python -m pytest -q
git diff --check
python -m pip check
```

`.github/workflows/ci.yml` 使用 `windows-latest` 和 `ubuntu-latest`、Python 3.11，只安装 `requirements.txt` 并运行上述 Python 编译、pytest 和依赖检查。测试通过 fake 后端、monkeypatch 和临时目录隔离外部资源。

CI 不运行真实麦克风、音频播放、模型下载或推理、Ollama、Piper 和串口访问，也不安装 `requirements-asr.txt` 或 `requirements-ser.txt`。因此 CI 结果不能代替真实模型和硬件验收。

当前首次 CI 尝试遇到 GitHub Actions 平台 Startup failure，job 未实际启动，不能记为 Windows/Ubuntu 已通过。后续结果以 GitHub Actions 当前运行记录为准；README 暂不添加 CI badge。

## 13. 当前限制

- ASR 接收完整 WAV，不是流式 ASR。
- LLM 和 TTS 均为完整回复；TTS 不是流式输出。
- continuous 是同一 Python 进程内的同步循环，不是 FastAPI、HTTP 服务或后台进程。
- 唇动是短时能量近似，不是音素、viseme 或视觉嘴形追踪。
- 尚未实现 STM32 舵机闭环，也没有视觉输入。
- 原生 Ubuntu 的真实音频设备、串口和真实模型运行待验证。
- Windows/Ubuntu CI workflow 已配置，但成功结果仍需在 GitHub Actions 服务正常后重新验证。
