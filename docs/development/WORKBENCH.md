# SRTP 轻量工作台（workbench）

统一入口提供环境自检、验证、配置快照、延迟基线和任务状态恢复；`tools.dsh_client` 单独负责有限派工。
相对路径按项目根目录解释。默认离线，只有显式在线自检和 dsh 连接访问网络；不能把模拟测试当真实设备验收。

## 快速开始

```bash
python -m tools.workbench doctor                 # 只读自检，不写文件、不联网
python -m tools.workbench validate --profile offline
python -m tools.workbench validate --profile targeted tests/test_workbench.py
python -m tools.workbench baseline --metrics outputs/latency-<stamp>/streaming_metrics.json \
    --output outputs/workbench/baseline-<stamp>.json --measurement real --label <stamp>
```

Python 至少 3.11；3.14 及更新版本自检提示未验证，而非直接判定不可用。CI 使用 3.11。

## doctor：环境自检

检查项：Python 版本、基础依赖（requirements.txt）、可选依赖、现有音频诊断（soundfile /
sounddevice / PortAudio / 默认输入输出设备）、Piper 与 SER 模型文件的存在性与字节数、磁盘剩余空间。

- 默认**只读**：不写任何文件、不联网、不启动录音或模型。
- 输出已脱敏：不出现用户绝对路径（项目外路径记为 `<outside-project>`）、不出现音频设备名、
  不出现配置 URL；异常只记录类型与安全类别（如 `ModuleNotFoundError:dependency_missing`），
  不回显异常消息。JSON 模式（`--json`）与文本模式同样脱敏。
- `--online`：仅当配置的 Ollama endpoint 是 loopback http(s)、不含用户名/口令、无 query/fragment
  时，才发起超时 ≤ 3 秒的 `GET /api/tags`；禁用代理，只比对所需模型是否在列表中，
  绝不调用 pull/generate，也不加载模型。非 loopback 或带凭证的 endpoint 直接拒绝（warning）。
- 危险等级：缺基础依赖或 Python 版本不受支持为 `error`；缺可选依赖、模型文件或音频设备为
  `warning`，不视为致命。

退出码：`0` = ok，`1` = warning，`2` = error。

## validate：统一验证

| profile | 实际执行 |
| --- | --- |
| `offline` | `python -m compileall -q main.py srtp_voice tools tests`；`python -m pytest -q tests` |
| `full` | `offline` + `python -m pip check`（CI 使用该 profile） |
| `targeted` | 只对显式给出的 `tests/` 内文件执行 `python -m pytest -q <文件...>` |
| `manual` | 只打印真实设备测试入口 `docs/development/V1.8_FIRST_AUDIO_LATENCY.md`，不运行任何命令 |

- 所有子进程使用 argv 列表、当前解释器（`sys.executable`）、固定项目根工作目录、`shell=False`。
- 不安装依赖、不访问网络；`targeted` 拒绝 pytest 额外开关（`-x`、`--cov` 等）、绝对路径、
  目录以及 `tests/` 之外的任何目标（含 `tests/../...`）。
- 失败即返回子进程的真实退出码；`targeted` 参数非法时返回 `2`。
- 退出码：`0` = 全部通过，其他 = 子进程退出码，`manual` 恒为 `0`（未运行任何测试）。
- CI 只调用该统一入口，不再重复散落 compileall / pytest / pip check。

## baseline：延迟基线

```bash
python -m tools.workbench baseline --metrics <源 metrics> --output outputs/workbench/<名称>.json \
    [--compare <旧基线>] [--measurement real|simulated|unknown] [--label <名称>]
```

- 只读取 metrics 白名单键（`summary`、`turns`、`last_turn`、`late_events`、
  `dropped_event_history`、`tts_backpressure_events`、`cancelled`、`failed`），
  不访问 `streaming_events.json`、录音或模型。metrics 中非计时字段忽略，只报告数量，不回显字段名或自由文本。
- 逐轮只保留 `turn_id` / `marks` / `latencies_ms` 数值，并按记录次序标注
  `first_observed` / `subsequent`；这**不代表**已证实冷启动或热启动差异。
  源文件没有 `turns`（或为空）时明确写 `per_turn_available: false` 与
  `per_turn_unavailable`，绝不把最后一轮当作完整明细。
- 输出 JSON 含 `schema_version`、`measurement`、`label`、来源文件的 SHA-256 与项目相对位置、
  导入时的 `capture_context`（Git HEAD、Python/OS、非敏感配置）以及 `summary`、计数器、逐轮数据。
  不含绝对路径、URL、密钥或原始自由文本。
- 导入时配置不等于测量时配置。旧结果默认 `recording_context: null`，不能补填当前配置后声称可比。
- 防覆盖：目标文件已存在时自动改用 `-1`、`-2` 后缀；输出必须位于 `outputs/` 下，
  否则报错并以 `2` 退出。
- `--compare` 仅在真实/模拟分类明确一致、测量配置及 Python/OS 一致、schema 兼容且双方都有逐轮证据时，
  才报告 p50/p95 差值；否则标记 `comparable: false` 与原因（`incomparable`），
  不凭历史版本宣称性能提升。
- 拒绝 NaN/Infinity/负数时延与非法结构（退出码 `2`）。

### 同轮配置快照与复测

以下在项目根目录的同一个 PowerShell 中运行；新目录不覆盖既有测试。先自检并处理 error，确认 Ollama 在运行、所需模型已准备好。使用耳机，自然问答 6–10 轮后 Ctrl+C；不更换配置，不预先用最终测试结果选择方案。

```powershell
.\venv\Scripts\python.exe -m tools.workbench doctor --online
$runName = 'latency-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + ([Guid]::NewGuid().ToString('N').Substring(0,8))
$env:OUTPUT_DIR = "outputs/$runName"
$env:MEMORY_FILE = "$env:OUTPUT_DIR/memory.json"
.\venv\Scripts\python.exe -m tools.workbench snapshot --output "$env:OUTPUT_DIR/recording-context.json" --label $runName
.\venv\Scripts\python.exe main.py --mode vad --streaming --continuous
# 结束后，仍在同一终端：
.\venv\Scripts\python.exe -m tools.workbench baseline --metrics "$env:OUTPUT_DIR/streaming_metrics.json" --recording-metadata "$env:OUTPUT_DIR/recording-context.json" --output "$env:OUTPUT_DIR/timing-baseline.json" --measurement real --label $runName
$m = Get-Content -Raw -Encoding UTF8 "$env:OUTPUT_DIR/streaming_metrics.json" | ConvertFrom-Json
$m.summary | ConvertTo-Json -Depth 6
$round = 0
$m.turns | ForEach-Object {
    $round++
    [pscustomobject]@{
        Round = $round
        ASR_ms = $_.latencies_ms.asr_final_ms
        Setup_ms = $_.latencies_ms.post_asr_setup_ms
        LLM_ms = $_.latencies_ms.llm_first_token_ms
        TTS_ms = $_.latencies_ms.tts_first_chunk_ms
        Playback_ms = $_.latencies_ms.endpoint_to_playback_ms
    }
} | Format-Table -AutoSize
```

`snapshot` 只记录实际配置，标注 `not_measured`；它必须配对同一次运行的 metrics。对外分享计时基线即可，录音、事件文字和 memory 保持本地。第一条记录未必成功，不自动称为冷启动；取消轮可能缺少部分指标。软件 playback 事件不等于声卡实际出声。

## 新机器说明

- 安装 `requirements.txt` 的基础依赖后可运行工具。Linux 音频通常还需要系统 PortAudio。
  `faster_whisper`、`funasr`、`torch` 属可选模型依赖；`serial` 属基础依赖，不是可选项。Piper 使用可执行文件，不要求 Piper Python SDK。
- Piper 可执行文件与模型、SER 模型（SenseVoice 默认缓存）通常不在仓库内；
  `doctor` 会报告 `exists=false` 与相对位置，按需自行准备。
- 真实设备验证（麦克风、扬声器、Ollama 模型、串口）不进入 CI。
  手动实测入口见 `docs/development/V1.8_FIRST_AUDIO_LATENCY.md`，
  `validate --profile manual` 只负责打印该入口，不运行、不代跑、也不假称成功。
- CI 覆盖范围：Windows/Ubuntu + Python 3.11 的离线测试与 `pip check`。
  跨平台结论以真实 CI 运行为准，本地单平台运行不能替代。

## 日志与结果位置

- 工作台相关日志与基线建议写入 `outputs/workbench/`（已存在的结果不会被覆盖）。
- 本文档不记录任何本机绝对路径、账号或私人信息。

## 任务运行器（tools.workbench task）

```bash
python -m tools.workbench task run --id T1 -- python -m tools.workbench validate --profile offline
python -m tools.workbench task status --id T1 [--json]
python -m tools.workbench task resume --id T1
```

- 只允许 `doctor/validate/baseline/snapshot`，禁 shell、`python -c`、安装与任务嵌套；`python` 代称解析为当前解释器，日志不写绝对解释器路径。
- 状态在 `outputs/workbench/tasks/<ID>/`（manifest.json、state.json、attempts/*.log），独占锁保证同 ID 不并发；重复 ID 直接拒绝，不覆盖历史。
- `resume` 仅在失败/中断且确认旧任务已结束时允许；成功、活跃或未知拒绝。锁遗留时不自动删除锁，也不依据旧 PID 终止进程。Windows 存活查询为只读；Linux 使用 `os.kill(pid, 0)`。
- 中断只尝试终止本次持有的子进程对象；若无法证明孙进程结束，状态为 `unknown`，不自动恢复。runner 不是通用训练调度器，不运行任意命令，不替代程序自身检查点。

## dsh 连接与有限派工（tools.dsh_client）

```bash
python -m tools.dsh_client status --session SESSION --json
python -m tools.dsh_client dispatch --id ID --task-file docs/xxx.md --session SESSION
python -m tools.dsh_client --console
# console 内：status SESSION / dispatch ID docs/xxx.md SESSION / quit
```

- 协议依据已安装 `@deepseek-ai/dsh` **0.1.7-rc.2**：POST `/api/<method>`，`client-request` 信封，`server-response.result` 为 `{ok,value}`/`{ok,error}`；版本变化可能不兼容。
- 仅 `session/list`、`session/projections`、`session/prompt`（catalog 只读可读）；不创建会话、不改全局模型/权限。
- token 只经 `getpass` 隐藏输入，仅存内存；不落盘、不进 argv/环境/日志。base URL 必须 loopback、无 userinfo/query/fragment；禁代理与自动 redirect，token 交换只接受同 origin `/` 或 `./` 的 303。
- `status` 只输出精选字段（session_id、running、权限、实际模型、usage、队列数）；`tokenUsage` 不是剩余额度或金额。`--console` 可保持连接避免反复认证。
- `dispatch` 仅接收 docs/ 或 outputs/ 下的 markdown，拒绝链接路径；先落盘 `outputs/workbench/dsh/<ID>.json` ledger 再发 RPC，同 ID 文件/会话变更拒绝。每-ID 锁和同项目/会话 OS 锁覆盖初次、重试、队列核验与 prompt；不同 ID 也不能并发进入同一会话。重复 accepted 不再派发；ambiguous 必须显式 `--retry` 并复用同一 requestId，不强清锁。
- running、已有队列或无法识别队列时拒绝新增派工；不用模型轮询状态。accepted 仅表示进入 inbox，验收必须查看实际文件和测试。旧 CLI 曾核实 V4 Flash 别名；本轮桌面原会话界面显示 DeepSeek-V41-Flash / Max，此为选中模型标识，不冒充底层服务版本审计；不改变全局模型、effort 或权限。
- 认证/状态/派工真实联调由 Codex 执行。协议测试采用离线 mock，task 有一个真实但不递归的短子进程测试；均不代替真实语音实验。

## 低消耗工作循环

### 已安装 Harness 桌面版的恢复

本项目已实际核实桌面版 0.2.0-rc.2：启动后可打开既有 srtp_Voice 工作区及“SRTP 工作台试点读取核验”原对话。先核对末轮任务、项目目录、助手是否闲置，再发一个有限任务文件；验证实际差异、失败日志与有效测试，不以发送成功/完成短报验收。

桌面版的服务端口可随启动变化；本轮旧 HTTP 客户端未认证请求返回 401。不要沿用旧 3080/旧 cookie、读取内部凭证、绕过认证或另启重复服务；当前用已登录的桌面界面继续工作，未宣称旧 CLI 已兼容桌面认证。源码/离线协议测试与真实桌面执行分开记证据。需要 UI 操作时用受支持桌面控制，先观察、操作后刷新，不盲目重复发送。

doctor 永不探测配置中的 UNC/网络命名空间（包括混合分隔符）；网络模型显示未知并警告，`--online` 仍只允许已约定 loopback tags，不允许 SMB 探测。外部基线的空/错误逐轮分组、未知指标及非 boolean outcome 会被拒绝；未知条件不能形成性能比较结论。

### 日常循环

1. 恢复只读 AGENTS.md 与 WORK_CHECKPOINT.md；按 PROJECT_MAP.md 定向找文件，不重读整段历史。
2. 按 DSH_TASK_TEMPLATE.md 一次合并派发一个可验收批次，明确工具调用数、时长和停止点；详细证据落盘，短报即可。
3. Codex 在产物、异常或交付时审查关键差异，失败先定向修正；调用次数/时长到界即停止继续派发并保存。不让模型反复询问进度。
4. 小修先 targeted，交付跑 full；Git 只暂存显式文件，Draft PR 与 CI/Review 结果写断点。

这些措施减少重复读取、认证和派工；尚无等价任务的受控 token 对照，不能宣称节省百分比。累计 tokenUsage 不等于剩余额度；预算不可读时按有限批次执行，不擅自购买。

## 环境提示

- 本机默认 pytest 临时根可能不可用（权限错误）；请用独立唯一目录，例如
  `python -m pytest -q -p no:cacheprovider --basetemp=outputs/workbench/pytest-tmp-<唯一后缀>`。
  部分工具测试在 `outputs/workbench/pytest-local/<UUID>/` 创建自己专属目录；其他测试仍使用 pytest 的 tmp_path。
  如本机默认临时目录权限异常，创建新 GUID 目录并仅在本次终端设置 `PYTEST_ADDOPTS` 的 basetemp/cache；不清理共享目录、不改 ACL 或系统配置。新机器未必需要此绕行。

```powershell
# 在单独测试终端中运行；环境变量只对该终端有效。
$testBase = Join-Path (Get-Location) ('outputs/workbench/pytest-' + [Guid]::NewGuid().ToString('N'))
if (Test-Path -LiteralPath $testBase) { throw '拒绝复用已有临时目录' }
New-Item -ItemType Directory -Path $testBase -ErrorAction Stop | Out-Null
$pytestPath = ([System.IO.Path]::GetFullPath($testBase)).Replace('\','/')
# PYTEST_ADDOPTS 会经过 shlex：用正斜杠和引号，避免反斜杠被当作转义。
$env:PYTEST_ADDOPTS = "--basetemp='$pytestPath' -o cache_dir='$pytestPath/cache'"
.\venv\Scripts\python.exe -m tools.workbench validate --profile full
```
