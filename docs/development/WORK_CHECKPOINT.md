# SRTP 当前工作断点

核实日期：2026-09-29（Asia/Shanghai）。恢复先读 AGENTS.md 与本文件，历史只按需查 WORK_HISTORY.md；不重复已接收派工。

## 目标与验收

- 本轮：建立轻量长期工作台与低消耗 dsh 协作。P0 真实接入/只读试点/环境自检；P1 统一验证/计时基线/任务状态恢复；P2 项目故障导航。必须独立测试、真实派工文件证据、Windows/Ubuntu CI；模拟与真实设备验收分开。
- 后续：取得 V1.8 前两轮首响的新逐轮实测，定位 ASR/LLM/TTS 长尾。本轮没有新增语音提速结果，不确定冷启动原因。

## 仓库与授权

- 分支 `codex/v1.8-workbench`，基于延迟分支 `a7294458495d9cbad1a740326209edda598f1b01`；[独立 Draft PR #24](https://github.com/Hensircast/srtp_Voice/pull/24)，base `codex/v1.8-first-audio-latency`，已附到任务，不混入 #23 的验收。
- [PR #23](https://github.com/Hensircast/srtp_Voice/pull/23) OPEN/Draft，base main，唯一 Review thread resolved/outdated；旧 [CI](https://github.com/Hensircast/srtp_Voice/actions/runs/35354538141) 成功，时间 9 月 18 日，不代表新工具 CI。
- 已授权：本项目必要读写、代码/文档外发给既有 dsh 工作区、自动回归、显式暂存/功能分支提交推送、Draft PR/CI/Codex Review 闭环。禁止项见 AGENTS.md；不合并 #23 或本轮 PR，不改全局模型/环境/权限，不提交凭证、个人录音/对话或模型权重。
- 下载/训练/付费/磁盘峰值预算未知；当前没有需要这些预算的动作，首次需要时才集中确认。

## 当前产物与真实证据

- DeepSeek 主执行 core/ops，Codex 审查关键差异并补独立回归。涉及解释器参数绕过、进程终止、并发重试等安全子问题由 Codex 接管；不把助手的完成字样当验收。
- 工具：`python -m tools.workbench` 的 doctor/validate/snapshot/baseline/task；`python -m tools.dsh_client` 的 status/dispatch/console。用法与 PowerShell 同轮复测入口：WORKBENCH.md；故障到模块/测试：PROJECT_MAP.md。
- 最新本地完整验证：`validate --profile full`，**488 passed / 13.72s / exit 0**；compileall 与 pip check 均 exit 0。任务/dsh/独立审查定向测试先前 56 项通过。新增跨平台路径、命令原值保留及错误比较类型回归。
- 首次提交 `55e3b40` 的 [CI](https://github.com/Hensircast/srtp_Voice/actions/runs/36543096587) Windows 成功，Ubuntu 1 failed/478 passed：Windows 路径在 Linux 被误解成相对路径，脱敏失效。已修正并保留断言；当前修复仍待推送后的新双平台 CI，不得称旧运行全通过。
- Codex Review 对 `55e3b40` 返回 P1 Windows 路径脱敏及 P2 latencies_ms 必须为 mapping；均已定位和修复。P2 精确复现为 list/string 的 AttributeError（修复前 2 failed/2 passed）；回归现在完整通过。修复推送、CI 核实后回复/解决线程，不隐藏审查。
- 已验证拒绝拼接 `-c` 等解释器参数、未知/敏感参数、路径越界、覆盖旧任务、并发派工、错误 schema；中断只操作本次 Popen，不按旧 PID 杀进程。无法证明孙进程结束时 unknown，禁止自动 resume；遗留任务锁不自动清除。
- 真实 CLI 短任务 `handoff-a0527dc67b19`：exit 0、completed，后续只读 probe wrapper/child 均 dead；证据 `outputs/workbench/tasks/handoff-a0527dc67b19/`。它仅执行 manual 入口，不算语音设备测试。
- 历史真实指标导入 `outputs/workbench/historical-20260918-verified.json`，原文件 SHA-256 已保留。endpoint p50/max 3398/8906 ms；无 turns、recording_context=null，不编造可比配置或性能提升。快照是当前配置，不是测量本身，必须配对同一轮数据。
- 本地审查证据 `outputs/workbench/codex-final-evidence.json`；core/ops 原短报及失败证据保留在 outputs/workbench，不能引用旧助手数字冒充当前通过。

## dsh 协作现场

- 复用本项目已有会话 `session-f321f435-d1c1-4b66-a82f-089cd167cc20`；cwd 匹配，权限 projection 为 danger-full-access。实际模型 `deepseek-official/deepseek-flash`，最新 effort max；未核实 V4.1，未改变模型/effort 的全局设置。
- 协议按实际安装 `@deepseek-ai/dsh 0.1.7-rc.2` 核对；token 只经隐藏输入，cookie 仅内存。工具仅允许 loopback 白名单 RPC；不保存认证 URL、token 或 cookie。
- 已接收 core/ops 的 `srtp-workbench-core-20260929-01`、`srtp-workbench-ops-20260929-01` 及审查补充请求，不重复派发。ops 到有限预算后已取消剩余执行并保存，Codex 独立完成安全验收。
- 新客户端真实派工 ID `live-smoke-20260929`，requestId `3a3eabcf608b417ebf116ed852487464`；ledger `outputs/workbench/dsh/live-smoke-20260929.json`。首次 accepted、第二次 already_accepted 拒绝；独立确认产物创建于投递之后。产物 `outputs/workbench/dsh-live-smoke-result.json` 的 partial/未自行认证说明只适用于助手，不否定 Codex 的真实投递证据。
- 最后确认 running=false、queue_count=0；真实 smoke 增加 3 steps，未做测试或性能实验。所有本轮认证控制进程已退出，cookie 已随进程释放；恢复需隐藏输入认证，不能假定旧连接仍可用。
- 最新累计 tokenUsage：uncachedInput 402937、cacheRead 24488448、output 207247；不是本轮净消耗、剩余额度或金额。尚无受控成本对照，不声称节省百分比。DeepSeek 剩余额度不可读；后续只派有限短批次。

## 环境、资源与限制

- Python 3.12.10；实际只读 doctor 全部 ok，基础/可选依赖、音频输入输出、Piper/SER 文件存在。没有加载模型、录音或播放。此前 online 检查 Ollama 不可访问、未看到服务进程；不证明模型缺失，先确认本机 Ollama 服务，不重装/换模型。
- 默认用户 pytest 临时目录 PermissionError；使用 outputs/workbench 下新 GUID basetemp/cache 验证，未改 ACL/全局环境。PYTEST_ADDOPTS 用正斜杠及引号，防止 shlex 吃掉反斜杠。一次误解析产生的本轮专用临时目录已移入 outputs/workbench 保留；未删除用户数据。不安全的助手 conftest 已移除且未恢复。
- 最新可访问元数据统计 60608 文件、2879586382 bytes（约 2.68 GiB）、1 个读取错误，为下界。本轮本地下载/安装/训练/付费/兑换均为 0。
- 最新 Codex 额度约剩 47%（5 小时）、59%（7 天），仅当时读数；保护规则 10%/7% 见 AGENTS.md，不使用重置券。

## 下一步与恢复

1. 精确暂存已验收的 Review/CI 修复、提交推送到 #24，核验新 SHA 的 Windows/Ubuntu CI。继续检查审查新增问题，相关回归通过并推送后才回复/解决线程。
2. 交付前把 PR/CI/Review 的实际链接及状态更新到本断点与历史；不得将 pending 标成完成。
3. 工具完成后按 WORKBENCH.md 的同轮快照流程收集 6–10 轮，保留 first_observed 与 subsequent，不把第一轮直接当冷启动；语音性能仍待真机证据。
4. 恢复核对 HEAD/dirty/进程/队列与唯一 ID，再开展下一短批次；未知存活或危险操作先停，不删除锁/强推/扩大任务。

模型、.env、虚拟环境、录音/数据和服务进程需分别核实；本断点不替代备份，也不包含认证凭证。
