# SRTP 当前工作断点

核实日期：2026-09-29（Asia/Shanghai）。恢复先读 AGENTS.md 与本文件，历史只按需查 WORK_HISTORY.md；不重复已接收派工。

## 目标与验收

- 本轮：建立轻量长期工作台与低消耗 dsh 协作。P0 真实接入/只读试点/环境自检；P1 统一验证/计时基线/任务状态恢复；P2 项目故障导航。必须独立测试、真实派工文件证据、Windows/Ubuntu CI；模拟与真实设备验收分开。
- 9 月 29 日晚恢复：Harness 桌面版已唤醒，在原“SRTP 工作台试点读取核验”对话中完成两批有限执行；Codex 已独立复现、审查并完成本地验收。**四条修复本地通过，但此断点保存时新提交 CI/线程回复尚待验证**；最终状态查 PR HEAD 和本地 codex-evidence.json，不把派工或助手短报当完成。
- 后续：取得 V1.8 前两轮首响的新逐轮实测，定位 ASR/LLM/TTS 长尾。本轮没有新增语音提速结果，不确定冷启动原因。

## 仓库与授权

- 分支 `codex/v1.8-workbench`，基于延迟分支 `a7294458495d9cbad1a740326209edda598f1b01`；[独立 Draft PR #24](https://github.com/Hensircast/srtp_Voice/pull/24)，base `codex/v1.8-first-audio-latency`，已附到任务，不混入 #23 的验收。
- [PR #23](https://github.com/Hensircast/srtp_Voice/pull/23) OPEN/Draft，base main，唯一 Review thread resolved/outdated；旧 [CI](https://github.com/Hensircast/srtp_Voice/actions/runs/35354538141) 成功，时间 9 月 18 日，不代表新工具 CI。
- 已授权：本项目必要读写、代码/文档外发给既有 dsh 工作区、自动回归、显式暂存/功能分支提交推送、Draft PR/CI/Codex Review 闭环。禁止项见 AGENTS.md；不合并 #23 或本轮 PR，不改全局模型/环境/权限，不提交凭证、个人录音/对话或模型权重。
- 下载/训练/付费/磁盘峰值预算未知；当前没有需要这些预算的动作，首次需要时才集中确认。

## 当前产物与真实证据

- DeepSeek 主执行 core/ops，Codex 审查关键差异并补独立回归。涉及解释器参数绕过、进程终止、并发重试等安全子问题由 Codex 接管；不把助手的完成字样当验收。
- 工具：`python -m tools.workbench` 的 doctor/validate/snapshot/baseline/task；`python -m tools.dsh_client` 的 status/dispatch/console。用法与 PowerShell 同轮复测入口：WORKBENCH.md；故障到模块/测试：PROJECT_MAP.md。
- 最新本地完整验证：`validate --profile full`，**561 passed / 15.63s / exit 0**；compileall 与 pip check 均 exit 0。较本轮开始增加 49 项回归，原隐私断言保留；新增独立验收初次真实 4 failed/22 passed，诊断补强修复前 2 failed。背压缺失仍为 null，不冒充实测零。
- 首次提交 `55e3b40` 的 [CI](https://github.com/Hensircast/srtp_Voice/actions/runs/36543096587) Windows 成功、Ubuntu 1 failed/478 passed。修复提交 `058c5d156dca48198737a587b3ab47425852060e` 的 [双平台 CI](https://github.com/Hensircast/srtp_Voice/actions/runs/36544108954) 均成功：Windows 488 passed/7.68s，Ubuntu 488 passed/2.87s，编译和 pip check 均成功。不要混淆两个 SHA 或用旧失败运行冒充通过；后续文档提交的最新 CI 恢复时在线核对。
- 第一轮两条 Review 已在 `058c5d1` 修复并在验证后回复/解决。复审 `058c5d1` 又返回四条：基础清单不应把未显式声明的 NumPy 当直接依赖、严格校验 summary、保存 TTS 背压、正确数未知 latency 字段。修复提交 `66f2b4dc0a97454fd1beccc1207b8ff4d01f1573` 的 [双平台 CI](https://github.com/Hensircast/srtp_Voice/actions/runs/36545870096) 成功：Windows 511 passed/9.37s，Ubuntu 511 passed/3.24s，编译与 pip check 成功；六条线程均已带证据回复/解决。已核实 NumPy 是 soundfile 传递依赖，移除直接基础要求，不新增安装。
- `66f2b4d` 的第三次 Review 四条 P2 已完成本地修复与回归（保存时线程尚未回复/解决）：
  1. `PRRT_kwDOSvoFtc6nCIJ3`：同一规范项目/会话共享 OS 锁，覆盖 status 到 prompt；侧锁名不能与派工 ID 冲突，保留原每-ID 锁；以实际竞争验证，而非自报标志。
  2. `PRRT_kwDOSvoFtc6nCIKB`：拒绝空逐轮证据、错误分组/顺序/计数及未知 timing 字段；合法缺逐轮数据仍不可比。
  3. `PRRT_kwDOSvoFtc6nCIKH`：cancelled/failed 若存在须实际 JSON boolean，拒绝其他类型且不回显值；缺失保持兼容默认。
  4. `PRRT_kwDOSvoFtc6nCIKO`：诊断只收路径元数据，UNC（含混合分隔符）在解析/探测前分类跳过；报告未知且警告。同步诊断默认探测保持兼容，SER 默认路径未丢失。
- Codex 对保存的 a8021a2 源码独立复现：模拟同会话接受 2 次请求、空逐轮可比=true、字符串 false 转真、8 次 UNC 探测被拦截。没有真实 RPC 重复派工或网络共享访问。脚本/独立证据在 `outputs/workbench/review3-desktop/`；助手首轮 report 仅为自评，后续以 Codex 验收和在线 CI 为准。
- 首次本轮修复提交 `61581e59f6d2d5ec311233456f658a3e45c8041b` 的 [CI 36566751094](https://github.com/Hensircast/srtp_Voice/actions/runs/36566751094) Windows success、Ubuntu **5 failed/550 passed**；不能称双平台通过。第四次 Review 新 P1 `PRRT_kwDOSvoFtc6nGiRa` 指向同一原生 POSIX 路径误判。Codex 已修复分类并补 6 项纯 Windows/POSIX 语法回归；两处新增 probed 期待值按原生路径语法区分平台，所有脱敏/存在/大小断言保留。当前本地 561 项通过，最新补充提交 CI 仍待发布验证，五个线程尚未解决。
- 缺失背压 null 修正保存于 `a8021a28aca65622618233830b0a990dfbd437b1`，恢复在线核实 [CI 36547275343](https://github.com/Hensircast/srtp_Voice/actions/runs/36547275343) Windows/Ubuntu 均 success。512 项通过不表示覆盖上述四条新问题；未经独立复现、修复、测试与 CI 不解决线程。
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
- 本次恢复发现 Harness 0.2.0-rc.2 位于 `E:/dsh_app`，启动前未运行，已通过受支持的桌面控制启动。已选择原 SRTP 对话，末轮 smoke 文本/产物与记录相符；界面显示完全权限、DeepSeek-V41-Flash / Max，未修改选择或全局设置。桌面服务在回环新端口，旧 HTTP 客户端无认证请求返回 401，未绕过认证或读取任何凭证；本批改走已登录桌面界面，仍禁止并发 dispatch。
- 桌面两批真实执行已结束，界面最后 12 轮/176 步、无停止生成或后台任务提示；只读进程检查未见遗留本项目 Python/pytest。Harness 保持开启供用户使用，不关闭用户应用。本轮 UUID 未经认证 API 再核实，但对话标题/末轮文本/项目 cwd 与真实产物均已核对。
- 主执行 DeepSeek；Codex 给出会话锁与 UNC 方案、补 28 项独立回归，接管两个确定性诊断边界修正。有限任务、修正反馈、两个助手报告、失败/通过日志、原源码复现及 Codex 验收 JSON 构成恢复证据，不仅一个结果文件。没有新增模型/权限/认证配置。
- 最新累计 tokenUsage：uncachedInput 402937、cacheRead 24488448、output 207247；不是本轮净消耗、剩余额度或金额。尚无受控成本对照，不声称节省百分比。DeepSeek 剩余额度不可读；后续只派有限短批次。

## 环境、资源与限制

- Python 3.12.10；实际只读 doctor 全部 ok，基础/可选依赖、音频输入输出、Piper/SER 文件存在。没有加载模型、录音或播放。此前 online 检查 Ollama 不可访问、未看到服务进程；不证明模型缺失，先确认本机 Ollama 服务，不重装/换模型。
- 默认用户 pytest 临时目录 PermissionError；使用 outputs/workbench 下新 GUID basetemp/cache 验证，未改 ACL/全局环境。PYTEST_ADDOPTS 用正斜杠及引号，防止 shlex 吃掉反斜杠。一次误解析产生的本轮专用临时目录已移入 outputs/workbench 保留；未删除用户数据。不安全的助手 conftest 已移除且未恢复。
- 最近可访问元数据统计 63036 文件、2885681457 bytes（约 2.69 GiB）、1 个读取错误，为当时下界。本轮本地下载/安装/训练/付费/兑换均为 0。
- 上次 7%/52% 时已安全保存，窗口自然恢复；本次阶段最新约剩 **86%（5 小时）、50%（7 天）**，未付费/兑换。DeepSeek 账户剩余额度仍不可读，界面累计 token/缓存命中不能当剩余额度或节省证明。读数可能延迟，保护规则见 AGENTS.md。

## 下一步与恢复

1. 恢复先核对额度、HEAD/dirty、#24 最新 Review/CI 与本地证据；最终保存提交 CI 若仍 pending 必须核实，不使用旧提交通过冒充。已有六条已处理，不重复回复，不合并 #24/#23。
2. 四条 P2 和后续一条 P1 已有本地修复，不重复实现；检查最新补充提交双平台 CI 与五线程回复/解决是否完成。失败的 61581e5 CI 不替代新提交；新提交通过后再处理线程，并按有限批次检查新增 Review。没有审查结论时不声称复审通过。
3. 下一批仅在原 Harness 对话闲置后派发；桌面 0.2.0 与旧 HTTP 客户端认证接口未联通，不沿用旧端口/凭证，不启动第二套服务或绕过认证。必要时用受支持桌面界面及独立产物核验，见 WORKBENCH.md。
4. 工具完成后按 WORKBENCH.md 的同轮快照流程收集 6–10 轮，保留 first_observed 与 subsequent，不把第一轮直接当冷启动；语音性能仍待真机证据。
5. 恢复核对相关进程/队列与唯一 ID，再开展下一短批次；未知存活或危险操作先停，不删除锁/强推/扩大任务。所有本轮认证进程已退出，恢复需隐藏输入认证，不把凭证写入交接。

模型、.env、虚拟环境、录音/数据和服务进程需分别核实；本断点不替代备份，也不包含认证凭证。
