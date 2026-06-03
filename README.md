# SRTP Voice Interaction Project

本项目是一个面向 SRTP 机器人头部系统的语音交互工程，主要用于实现“语音输入 → 语音识别 → 大模型回复 → 语音合成 → 音频播放”的基础流程，并为后续接入表情识别、串口控制、舵机动作和机器人头部表情联动提供接口。

项目当前阶段以 Windows PC 作为上位机，负责语音采集、对话生成、TTS 合成和音频播放。Arduino / STM32 等 MCU 可作为下位机，后续通过串口接收动作指令，用于控制机器人头部的眼球、嘴部、颈部或其他舵机结构。

---

## 1. Project Goals

本项目的主要目标如下：

1. 构建一个可以在 Windows 环境下运行的语音交互主流程。
2. 支持电脑麦克风输入，并能保存或处理录音文件。
3. 预留 ASR 模块接口，用于接入 Whisper、FunASR 或其他语音识别模型。
4. 预留 LLM 模块接口，用于接入 DeepSeek、Qwen、OpenAI 或本地大模型。
5. 支持 TTS 语音合成，可使用 edge-tts 等工具生成语音回复。
6. 支持电脑扬声器播放回复语音。
7. 预留串口通信接口，用于后续控制 Arduino / STM32 / 机器人头部舵机。
8. 为后续接入情绪识别、视觉识别和多模态交互提供基础框架。

---

## 2. Current Features

当前项目主要包含以下功能或预留模块：

* 电脑麦克风录音
* 语音输入文件保存
* 语音活动检测接口
* ASR 语音识别占位接口
* 大模型回复占位接口
* TTS 语音合成接口
* 音频播放接口
* 串口通信占位接口
* Windows PowerShell 运行支持
* `.env` 环境变量配置支持
* 适合 Codex 辅助修改和重构的项目结构

---

## 3. Recommended Environment

建议使用以下环境运行本项目：

| Item                | Recommended Version       |
| ------------------- | ------------------------- |
| OS                  | Windows 10 / Windows 11   |
| Python              | Python 3.10 / 3.11 / 3.12 |
| Editor              | VS Code                   |
| Terminal            | Windows PowerShell        |
| Virtual Environment | venv                      |
| Git                 | Git for Windows           |

不建议使用 Python 3.14 作为当前开发环境，因为部分音频、OpenCV、深度学习或科学计算库可能还没有稳定的预编译 wheel，容易出现安装失败问题。

---

## 4. Project Structure

项目结构可参考如下形式：

```text
srtp_Voice/
│
├── main.py
├── requirements.txt
├── README.md
├── .gitignore
├── .env.example
│
├── config/
│   └── settings.py
│
├── src/
│   ├── audio/
│   │   ├── mic_pc.py
│   │   ├── player.py
│   │   └── vad.py
│   │
│   ├── asr/
│   │   └── asr_placeholder.py
│   │
│   ├── llm/
│   │   └── llm_placeholder.py
│   │
│   ├── tts/
│   │   └── tts_edge.py
│   │
│   ├── serial_comm/
│   │   └── serial_tool.py
│   │
│   └── utils/
│       └── logger.py
│
├── recordings/
│   └── .gitkeep
│
├── outputs/
│   └── .gitkeep
│
└── tests/
    └── test_basic.py
```

实际项目结构可以根据当前代码情况调整。若部分文件暂时不存在，可以后续由 Codex 协助整理和补充。

---

## 5. Installation

### 5.1 Clone the Repository

如果项目已经上传到 GitHub，可以使用以下命令克隆：

```powershell
git clone https://github.com/your-username/srtp-voice.git
cd srtp-voice
```

如果项目已经在本地，则直接进入项目根目录：

```powershell
cd D:\code\VScode\Py\srtp_Voice
```

---

### 5.2 Create Virtual Environment

在项目根目录下创建虚拟环境：

```powershell
python -m venv venv
```

激活虚拟环境：

```powershell
.\venv\Scripts\activate
```

如果激活成功，终端前面会出现：

```text
(venv)
```

例如：

```text
(venv) PS D:\code\VScode\Py\srtp_Voice>
```

---

### 5.3 Install Dependencies

安装项目依赖：

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

如果当前还没有 `requirements.txt`，可以先根据代码中实际使用的库手动安装，例如：

```powershell
pip install python-dotenv sounddevice soundfile edge-tts pygame pyserial
```

然后生成依赖文件：

```powershell
python -m pip freeze > requirements.txt
```

---

## 6. Environment Variables

项目中不要直接写入 API Key。推荐使用 `.env` 文件保存密钥和配置。

在项目根目录新建 `.env` 文件：

```text
DEEPSEEK_API_KEY=your_deepseek_api_key_here
QWEN_API_KEY=your_qwen_api_key_here
OPENAI_API_KEY=your_openai_api_key_here
HF_TOKEN=your_huggingface_token_here
SERIAL_PORT=COM3
SERIAL_BAUDRATE=115200
```

同时建议提供一个 `.env.example`，用于说明需要哪些变量，但不要填写真实密钥：

```text
DEEPSEEK_API_KEY=
QWEN_API_KEY=
OPENAI_API_KEY=
HF_TOKEN=
SERIAL_PORT=COM3
SERIAL_BAUDRATE=115200
```

注意：

* `.env` 文件不能提交到 GitHub。
* `.env.example` 可以提交到 GitHub。
* API Key、Token、账号密码等敏感信息不要写入代码和 README。

---

## 7. How to Run

M1 文本到语音测试：键盘输入文本，使用 edge-tts 生成 `outputs/reply.mp3`，再用 pygame 播放 MP3。

```powershell
.\venv\Scripts\activate
python scripts\test_tts.py
```

也可以直接传入文本，便于快速验证：

```powershell
python scripts\test_tts.py --text "你好，我是 SRTP 语音交互测试。"
```

运行主流程时，默认会在 console 模式下从键盘输入文本，并把 TTS 输出保存为 `outputs/reply.mp3`：

```powershell
python main.py
```

如果只生成 MP3、不播放：

```powershell
python scripts\test_tts.py --no-play
```

麦克风录音测试：默认录制 5 秒，16000 Hz，单声道，并保存到 `recordings/input.wav`。

```powershell
python scripts\test_record.py
```

也可以显式指定录音参数：

```powershell
python scripts\test_record.py --seconds 5 --sample-rate 16000 --channels 1 --output recordings\input.wav
```

---

## 8. Basic Workflow

当前语音交互流程设计如下：

```text
User Speech
    ↓
Microphone Recording
    ↓
Speech Detection / VAD
    ↓
ASR Module
    ↓
Text Input
    ↓
LLM Response
    ↓
TTS Synthesis
    ↓
Audio Playback
    ↓
Optional Serial Command to MCU
```

后续扩展后，系统可以进一步加入视觉和情绪识别：

```text
Camera / Microphone
    ↓
Emotion Recognition
    ↓
Dialogue State / Agent Planner
    ↓
LLM Response
    ↓
TTS + Facial Expression + Servo Control
```

---

## 9. Audio Notes

本项目涉及录音、语音合成和播放，因此需要特别注意音频格式问题。

常见问题包括：

1. 把 MP3 文件当作 WAV 文件读取。
2. TTS 文件还没有完全生成就开始播放。
3. 播放时采样率和文件真实采样率不一致。
4. 声道数不匹配，例如单声道文件按双声道播放。
5. PCM 数据格式错误，例如 8-bit unsigned PCM 和 16-bit little-endian PCM 混用。
6. 串口传输音频时波特率不足，导致声音失真、变速或严重噪声。

如果播放时出现“意义不明的声音”“噪声很大”“语音变快”“人声听不清”等问题，应优先检查：

* 文件格式
* 采样率
* 声道数
* dtype
* 播放库是否支持当前格式
* TTS 输出文件是否完整
* 是否误把二进制 PCM 数据当成普通音频文件读取

---

## 10. Serial Communication Plan

后续可通过串口与 Arduino / STM32 通信，实现语音交互结果对机器人头部动作的控制。

建议的串口指令形式：

```text
EMOTION:HAPPY
EMOTION:SAD
ACTION:NOD
ACTION:SHAKE_HEAD
MOUTH:OPEN
MOUTH:CLOSE
EYE:LEFT
EYE:RIGHT
```

也可以使用更严格的数据帧格式：

```text
[0xAA][0x55][LEN][SEQ][PAYLOAD...][CHK]
```

其中：

* `0xAA 0x55`：帧头
* `LEN`：数据长度
* `SEQ`：帧序号
* `PAYLOAD`：有效数据
* `CHK`：校验位，可使用 XOR 校验

后续如果项目需要更稳定的通信，应优先使用结构化数据帧，而不是简单字符串。

---

## 11. Development with Codex

本项目适合使用 Codex 进行辅助编程、重构和调试。

建议在向 Codex 提问时遵循以下原则：

1. 先让 Codex 阅读项目结构，不要立即大改。
2. 每次只提出一个明确任务。
3. 要求 Codex 保持 Windows PowerShell 下可运行。
4. 要求 Codex 不要提交 `.env`、`venv`、音频缓存和模型权重。
5. 修改音频相关代码时，要求 Codex 检查文件格式、采样率、声道数和 dtype。
6. 修改依赖时，要求 Codex 同步更新 `requirements.txt`。
7. 避免一次性要求 Codex 重写整个项目。

示例任务：

```text
请阅读这个 SRTP 语音交互项目，先不要立即修改代码。请重点检查 TTS 播放部分为什么会发出意义不明的声音，并从文件格式、采样率、声道数、dtype、异步写入和播放时机几个方面定位问题。最后请给出最小修改方案。
```

另一个示例任务：

```text
请将当前项目整理成更清晰的模块结构，包括 audio、asr、llm、tts、serial_comm 和 utils。要求保持原有功能可运行，不要引入复杂框架，不要修改 API Key 的读取方式，并给出 Windows PowerShell 下的运行命令。
```

---

## 12. Git Usage

初始化 Git 仓库：

```powershell
git init
```

查看当前状态：

```powershell
git status
```

添加文件：

```powershell
git add .
```

提交代码：

```powershell
git commit -m "Initial SRTP voice project"
```

连接 GitHub 远程仓库：

```powershell
git remote add origin https://github.com/your-username/srtp-voice.git
```

推送到 GitHub：

```powershell
git branch -M main
git push -u origin main
```

---

## 13. Files That Should Not Be Committed

以下内容不应该提交到 GitHub：

```text
venv/
.venv/
.env
.env.*
__pycache__/
*.pyc
*.wav
*.mp3
*.pcm
recordings/
outputs/
temp/
tmp/
logs/
*.log
models/
weights/
*.pt
*.pth
*.onnx
```

推荐在 `.gitignore` 中加入这些内容。

---

## 14. Recommended `.gitignore`

```gitignore
# Python
__pycache__/
*.pyc
*.pyo
*.pyd

# Virtual environments
venv/
.venv/
env/

# Secrets
.env
.env.*

# Logs
*.log
logs/

# Audio files
*.wav
*.mp3
*.pcm
recordings/
outputs/
temp/
tmp/

# Model files
models/
weights/
*.pt
*.pth
*.onnx
*.ckpt

# IDE
.vscode/
.idea/

# OS
.DS_Store
Thumbs.db
```

---

## 15. Roadmap

后续开发计划可以分为以下阶段：

### Stage 1: Basic Voice Pipeline

* 完成电脑麦克风录音
* 完成语音文件保存
* 完成 TTS 合成
* 完成电脑扬声器播放
* 完成最小可运行主程序

### Stage 2: ASR and LLM Integration

* 接入 ASR 模型
* 接入 DeepSeek / Qwen / OpenAI / 本地 LLM
* 支持连续对话
* 支持简单上下文记忆

### Stage 3: Emotion-Aware Dialogue

* 提取语音情绪特征
* 接入视觉表情识别
* 将情绪标签输入 LLM
* 根据用户情绪调整回复语气

### Stage 4: Robot Head Control

* 接入 Arduino / STM32 串口通信
* 建立表情动作指令表
* 控制眼球、嘴部、头部舵机
* 实现语音回复与表情动作同步

### Stage 5: Local Deployment

* 尝试本地 ASR / TTS / LLM
* 尝试 RK3588 / Raspberry Pi 5 / Intel 小主机部署
* 优化响应速度
* 减少云端 API 依赖

---

## 16. Troubleshooting

### 16.1 `git` is not recognized

如果 PowerShell 中出现：

```text
git : 无法将“git”项识别为 cmdlet、函数、脚本文件或可运行程序的名称
```

说明 Git 没有安装，或者没有加入 PATH。

可以使用以下命令安装 Git：

```powershell
winget install --id Git.Git -e --source winget
```

安装完成后，需要关闭并重新打开 PowerShell 或 VS Code 终端。

检查 Git 是否安装成功：

```powershell
git --version
```

---

### 16.2 Cannot Activate venv

如果执行：

```powershell
.\venv\Scripts\activate
```

出现权限错误，可以尝试：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

然后重新激活：

```powershell
.\venv\Scripts\activate
```

---

### 16.3 TTS Output Sounds Like Noise

如果 TTS 播放出来是噪声或意义不明的声音，应检查：

1. TTS 输出文件格式是否正确。
2. 是否将 MP3 当作 WAV 读取。
3. 播放库是否支持当前音频格式。
4. 采样率是否匹配。
5. 声道数是否匹配。
6. 文件是否写入完成后才开始播放。
7. 是否误读了 PCM 原始数据。

建议先将 TTS 输出保存为标准 WAV 文件，再用播放器单独打开测试。如果播放器能正常播放，而 Python 播放异常，问题通常在播放代码；如果播放器也无法正常播放，问题通常在 TTS 输出文件本身。

---

## 17. License

This project is currently for SRTP research and course project development. The license can be added later according to the actual requirements of the research group or project team.

---

## 18. Notes

本项目仍处于 SRTP 原型开发阶段，代码结构和功能接口可能会随着机器人头部硬件、语音模块、情绪识别模块和串口控制方案的调整而变化。

开发过程中应优先保证：

1. 主流程可以运行。
2. 每个模块边界清晰。
3. 密钥和隐私信息不进入 GitHub。
4. 音频输入输出格式明确。
5. 串口通信协议可调试、可复现。
6. 后续接入机器人头部硬件时便于扩展。
