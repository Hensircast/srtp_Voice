# SRTP 工作历史与证据索引

整理日期：2026-09-29。本文件保存已发生的事实与证据入口；当前状态以 WORK_CHECKPOINT.md 为准。旧文档中的“当前”“等待”“Draft”等词仅指记录当时，恢复时须重新核对。

## 2026-08：V1.8 实现与收口

| 阶段 | 已完成内容 | 主要证据 |
| --- | --- | --- |
| 8 月 9–10 日 | Ollama token 流、中文句子切分、Piper 增量合成、顺序播放、麦克风/VAD、ASR partial/final、有界队列、取消和时延统计；同步路径保持兼容 | V1.8_AUTOMATION_LOG.md；V1.8_REAL_DEVICE_TESTS.md |
| 真实设备反馈修复 | PowerShell 读取 UTF-8 JSON；过滤空白/标点块；静音 lip-sync 除零保护；TTS fail-fast；失败诊断及连续模式恢复；减少固定寒暄/记忆污染 | V1.8_AUTOMATION_LOG.md 的真实设备反馈章节 |
| PR #21 | V1.8 主功能 squash 合并；合并后两秒出现迟到 Codex Review，后续在独立功能分支处理 | [PR #21](https://github.com/Hensircast/srtp_Voice/pull/21)，merge `e97796225202333958c893b0e0c1d653e41340e7` |
| PR #22 | 修复旧 turn 错误误取消新 turn、事件序号观察顺序、播放器初始化取消、末尾硬标点首句延迟、跨 token 闭引号归属、stop hook 重入死锁 | [PR #22](https://github.com/Hensircast/srtp_Voice/pull/22)，merge `d57f5ba285bb3bbcac33d72a6df6fc67dde7ded1` |
| 环境审计 | 未发现本阶段持久新增的待撤销 User/Machine 候选变量；未改系统变量、PATH、凭证、服务或模型。移除临时 UTF-8 变量后，记录了 373 项完整测试、163 项定向测试通过 | V1.8_ENVIRONMENT_RESTORE.md；V1.8_AUTOMATION_LOG.md |

2026-09-29 在线复核 #21、#22 均为 MERGED。环境审计描述的是 8 月现场，不能当作 9 月系统环境的完整复查。Ubuntu CI 为离线测试，不能证明 Ubuntu 真实模型、麦克风或扬声器部署已验收。

## 2026-09：工作区恢复与首响优化

- 项目列表重建后，仓库、虚拟环境、配置、模型及测试目录仍存在；应用中的 SRTP 项目已重新指向仓库根目录并被识别为 Git 仓库。默认沙箱中的 Python 启动失败经平台审核后的只读检查确认是权限限制，虚拟环境无需重建。
- 首响优化在 `codex/v1.8-first-audio-latency`，基于已合并 main；[PR #23](https://github.com/Hensircast/srtp_Voice/pull/23) 为独立 Draft，尚未合并。

| 提交 | 行为改变 | 当时自动验证 |
| --- | --- | --- |
| `31854ee` | VAD/录音上限先于新 partial 调度，避免端点额外解码；增加 endpoint-to-audio/playback、post-ASR 指标 | 375 项通过；双平台 CI 通过 |
| `3aa7ea8` | 本地 Ollama 默认 IPv4 回环地址，显式自定义服务器/endpoint 保持优先 | 376 项通过；双平台 CI 通过 |
| `c51d935` | 静音确认期不启动新 partial；只有实际提交识别时才拼接 PCM；暂停后继续说话可恢复 partial，final 仍包含完整音频 | 379 项通过；双平台 CI 通过 |
| `a729445` | Codex P2 修复：端点前先收取已经完成的 partial；新增有界、独立的逐轮时延 `turns` 导出，覆盖成功/失败/Ctrl+C | 381 项通过；双平台 [CI](https://github.com/Hensircast/srtp_Voice/actions/runs/35354538141) 通过 |

详细改动、失败前/修复后证据和 PowerShell 复测入口：V1.8_FIRST_AUDIO_LATENCY.md。2026-09-29 在线核实 #23 最新 head 为 `a7294458495d9cbad1a740326209edda598f1b01`；唯一 Review thread resolved/outdated，检查仍对应 9 月 18 日运行。

## 用户实测的解释边界

| 反馈轮次 | 完整轮数 | endpoint-to-playback p50 | LLM 首 token p50 | ASR final p50 |
| --- | --- | --- | --- | --- |
| IPv4 修复前 | 4 | 8219 ms | 4718.5 ms | 2289 ms |
| IPv4 修复后 | 4 | 3601.5 ms | 633 ms | 1680 ms |
| 静音期调度优化后 | 6 | 3398 ms | 562.5 ms | 1437 ms |

- Windows 本机 localhost 三次健康请求为 2039–2062 ms，IPv4 为 1–26 ms；`::1` 约 2027 ms 后拒绝连接。tags/chat 每轮两次请求，证据支持消除约 4 秒连接回退等待。该收益不保证出现在原本没有回退等待的新机器上。
- 三次用户测试不是相同问题/负载下的受控实验；不同阶段中位数不可直接相加，不能把全部差值归于单一改动。4–6 轮不足以证明稳定 p95。
- 最新六轮最长 endpoint-to-playback 8906 ms、LLM 首 token 6031 ms。保留事件中另有 ASR 3234 ms、LLM 547 ms 的慢轮；最早事件被淘汰。前两轮慢的原因尚未确定，不能声称冷启动已经确认或修复。
- `time_to_playback_ms` 包含等候用户和说话；`endpoint_to_playback_ms` 从 VAD 确认结束开始，未含此前静音确认时间，且是软件事件计时。

## 2026-09-29：长期协作规则与状态整理

- 未找到仓库及上级目录已有 AGENTS.md 或独立断点；Codex 按用户要求创建项目规则、DSH_TASK_TEMPLATE.md 和 WORK_CHECKPOINT.md，保留原安全边界。
- 规则创建、只读状态检查、历史整理由 Codex 直接完成，未向 DeepSeek 外发材料。dsh CLI `0.1.5-rc.3` 可运行；web/headless profiles 存在，项目会话/当前助手任务与可外发范围未核实。headless 会创建新会话，不用它冒充原会话恢复。
- 规则创建时额度读数为 5 小时剩余 89%、7 天剩余 65%，结束为 86%/65%；本次整理起点为 85%/64%。这些是时间点读数，恢复时需刷新。
- 最近目录元数据枚举约 2.67 GiB/56856 个可读文件，有 30 个读取错误，不能作为完整项目总占用。没有下载、安装、训练、付费、兑换或修改全局配置。
- 本次整理验证 Python 3.12.10 和 pip check 正常；未重跑历史测试、模型实验或麦克风验收。现有三份最近测试 metrics 均无 `turns` 字段，未发现 a729445 之后的新逐轮复测证据。

## 2026-09-29：工作台与真实 dsh 协作

- DeepSeek 经既有会话实现工具主体，Codex 规划验收、审查关键接口/差异，补独立回归并接管并发、进程安全等子问题，没有将其他模型冒充 DeepSeek。
- 初审发现错误 RPC/投影、默认配置冒充当前配置、计数类型、异常 schema、共享临时目录绕行、递归自测及 PID/命令安全问题。保留失败证据、按审查修正；不安全的本轮 conftest 未保留，未改全局权限。
- 逐步独立验证：异常 schema 修复前 4 failed/1 passed；core 61 passed；ops 接管后 56 passed；最终 **479 passed/10.70s**，统一 full 的 compileall/pytest/pip check 全部 exit 0。一项快照测试原有恒真表达式被改成实际状态/类型检查后重跑通过；历史 381 是延迟分支当时的测试。
- 新客户端隐藏认证、真实 status 和有限派工成功；约 10 秒后生成新文件，再次同 ID 本地拒绝，最后 idle/queue=0。Codex 核对 ledger、创建时间及注册命令，不仅依据 accepted 或助手短报；助手自身未执行网络认证或测试。
- 实际 dsh 协议 0.1.7-rc.2，会话使用 V4 Flash 别名/effort max，未核实 V4.1，未改默认设置。smoke 增加 3 steps；累计 token 不等于剩余额度，没有受控成本对照，不声称节省比例。
- 历史计时导入 p50/max 3398/8906 ms，逐轮/测量配置未知；当前配置与测量 provenance 分开，没有新增语音提速宣称，也未代跑真实设备测试。
- 可访问元数据 60608 文件/2879586382 bytes、1 处读取错误；本地下载/安装/训练/付费/兑换均为 0。临时目录受限采用项目唯一目录；误解析的本轮临时目录移回 outputs 保留，未删除用户数据。
- 新工具 PR/双平台 CI/Review 待在线验收后在断点记录，不把旧 #23 的成功搬到新分支。

## 按需加载

- 当前目标、授权缺口、助手状态及恢复步骤：WORK_CHECKPOINT.md。
- 长期规则：仓库根目录 AGENTS.md；派工格式：DSH_TASK_TEMPLATE.md。
- V1.8 全流程：V1.8_AUTOMATION_LOG.md；真实设备验收：V1.8_REAL_DEVICE_TESTS.md。
- 环境变量历史审计：V1.8_ENVIRONMENT_RESTORE.md；首响原因和逐轮测试命令：V1.8_FIRST_AUDIO_LATENCY.md。
- 本地输出/录音/配置/模型不在本索引中展开；任务实际需要时按授权访问，外发和 Git 提交边界另行核实。
