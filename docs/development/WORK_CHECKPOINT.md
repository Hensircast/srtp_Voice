# SRTP 当前工作断点

核实日期：2026-09-30（Asia/Shanghai）。先读 AGENTS.md 与本文件；历史按需查 WORK_HISTORY.md 和 Git。旧断点完整内容保存在基线 `49bbb6d`，本文件不是模型、虚拟环境、服务或个人数据的完整备份。

## 当前目标、授权与位置

- 新批次（2026-09-30 20:53）：用户批准可回退自主优化、DeepSeek主执行；从干净`3f09485`恢复，离线doctor正常/Python与Piper进程0，起点额度80%短窗口/23%周。新复审针对`f38716e`返回缩写/单字母句号永久误保护P2（thread `PRRT_kwDOSvoFtc6nhATi`，comment4144236099），未解决1，优先于新连接等优化。本批仅上下文分句最小修复，Codex独立门禁已新增；原dsh闲置24轮394步，有限派工尚未发送，任务与后续证据`outputs/workbench/abbreviation-review-20260930/`。不将上一ASR“待审/未解决0”旧快照视为现场状态。
- 本批20:57已在原dsh发送一次14工具/8分钟有限任务，界面25轮396步进行中；独立基线实际 **22 failed/6 passed/0.35s**。另补Codex独立真实线程/假LLM与TTS门禁，验证下一句生成尚阻塞时首句已经交给TTS，不将假桩计时当真实提速。
- 21:01核查dsh已停止于25轮397步，仅“已读取文件”、无代码/报告/错误原因；Python/Piper无新任务。桌面首次读动作报coordinate input geometry unavailable，按技能重新选取窗口后正常，仅尝试一次有界恢复，不把停止当完成、也不静默接管执行。
- 后续核实原dsh已闲置26轮409步，首版源码/自有测试落盘，两份日志 **26 failed/68 passed**、**26 failed/30 passed**，报告承认超14工具；不验收。Codex线程件实际 **4 failed/9.44s**，运行时源码可能处于修改中，不记为精确HEAD基线。额度阶段72%短/22%周。一次自动审批超时未执行补丁，重新只读确认未落盘，不将超时当拒绝或绕过。
- 21:18 Codex接管需判断的子问题，裁定以原句号index+统一三态predicate替代重复布尔；最后一份有限执行要求 `outputs/workbench/abbreviation-review-20260930/feedback-state.md`（8工具/5分钟/一次验证）交回dsh。不盲目续跑，仍失败保存现场，不新增其它优化。
- 原dsh最终27轮420步已空闲，report-final承认逐字发射与shell重写编码破坏，编译通过不是行为通过，未运行最终合并验证；不验收/不再派发。失败源码本地保存`deepseek-failed-streaming.py`。Codex只接管该复杂模块：从实际HEAD UTF-8原文核对恢复全部标点，以apply_patch完成最小统一三态与原索引修复，定向 **123 passed/2.77s**。自有新测试里不应强制旧模式逗号连续，删除无关逗号输入但保留强断言，并独立增加原旧模式/自然模式逗号契约，既有测试未改，失败记录不删除。新增分区一致性门禁后完整验证待运行；不声称设备时延/自然度提升。
- 本批交付前完整验证 **803 passed/38.52s/exit0**，compileall/pip check通过；比727新增76项。全量token分区、慢续接、纯空白、闭合引号、旧模式兼容、普通文本与真实线程/假LLM交付均通过。提交前在线复核新线程仍未解决1，只在实际修复推送及相关新SHA CI成功后闭环；源码/test独立代码提交，详细文档/断点另存，支持正常revert且不丢原失败证据。DS任务模板补丁编辑/最小健全性/报告预算约束由Codex直接完善，本批停止新派工。
- 当前批次（2026-09-30 19:16）：用户要求继续优化，从干净`5a2c1a5`恢复，doctor离线健康、Python/Piper进程0；额度起点短窗口100%/周27%。上一预热`8201713`复审无重大问题（comment5904481921）、旧线程未解决0。原dsh有限主执行16k PCM16内存直传并完成有限修正，Codex审查/独立回归/解码器与真实离线模型核查；本地 **727 passed/38.62s**、编译和依赖检查通过。任务/报告/失败记录在`outputs/workbench/asr-memory-20260930/`；新提交/CI/Review以实际Git/在线PR和该目录codex-evidence.json为准，不拿旧SHA通过替代。
- 追加批次（2026-09-30 12:37 启动）：用户要求在额度允许内继续优化；从干净 HEAD `32cf036` 恢复，离线 doctor 健康、项目进程0。只实现默认关闭的 Piper 预热；原 dsh 12 工具/8 分钟有限主执行，2分40秒后停止，界面空闲22轮373步。Codex 审查/独立回归/边界修复，本地 **704 passed/37.38s**、编译及依赖检查通过；真实公开六句串行配对已完成，详见自然语音文档。本批提交与 CI 按实际 Git/在线 PR 和 `outputs/workbench/voice-warmup-20260930/codex-evidence.json` 核实，不沿用旧 SHA 通过。
- 用户要求全项目代码审查、迭代优化响应速度与体验，重点减少语音机械感。基线 `49bbb6d9f18630624b67fb14014df3153eee18ff`，开始时工作树干净。
- 当前分支 `codex/v1.8-natural-voice`，[Draft PR #25](https://github.com/Hensircast/srtp_Voice/pull/25)，base 为 `codex/v1.8-workbench`。前一批代码 `023979e` 和文档 `32cf036` 双平台各680项 CI 成功，已收到的两条 Review 均解决；`32cf036` 最新复审返回“未发现重大问题”（comment5904055029），不当新预热提交的批准。PR 未合并；不再扩展优化批次，先完成本批提交/CI/Review 检查。
- 已授权本仓库必要代码/文档读写、发送到既有 dsh 工作区、回归、显式路径暂存、功能分支提交推送、Draft PR/CI/Review。禁止项沿用 AGENTS.md；不合并、不改 main/global/ACL、不外发录音/私人对话/权重/凭证。
- 下载、训练、安装、付费与峰值磁盘预算没有新增授权，本轮均未执行；必要时再集中确认，不能从界面“完全权限”继承。

## 当前已验证结果

- 当前缩写修复代码`b2e06e0e5c80cd59deb75e24d8a180d9565289a1`已推送；[CI36722862975](https://github.com/Hensircast/srtp_Voice/actions/runs/36722862975)实际SHA双平台成功，Windows **803 passed/31.52s**、Ubuntu **803 passed/17.29s**、编译/pip check通过；已[回复并解决](https://github.com/Hensircast/srtp_Voice/pull/25#discussion_r4145214846)原缩写P2，已收到未解决线程0，不当新复审批准。本批完整证据`outputs/workbench/abbreviation-review-20260930/codex-evidence.json`，可正常revert该独立代码提交，不重写历史；附属文档HEAD/CI与新复审另按实际Git/在线核实。
- 上一ASR代码`f38716e8e12901e19cb1ee7e0fdd7d4a7744e849`已推送；[CI36709843039](https://github.com/Hensircast/srtp_Voice/actions/runs/36709843039)实际SHA，Windows **727 passed/24.31s**、Ubuntu **727 passed/16.64s**，编译/pip check成功；[复审请求](https://github.com/Hensircast/srtp_Voice/pull/25#issuecomment-5910423969)后来返回本批缩写P2，不能沿用历史“待审/0线程”快照。
- 流式16k PCM16不再经临时WAV中转，其他速率/后端/自定义adapter缺hook或开关0仍走原路径；同步文件API、正式输入WAV、VAD与final-only流程不变。65,536个PCM16值与真实安装解码器完全相等；两个纯中转中位记录109.1635→0.0865ms、79.957→0.059ms（不含模型推理）。同一公开合成句真实离线模型4次文本非空/hash一致，WAV→内存→内存→WAV为1982.396/1634.752/1615.348/1706.436ms，不承诺普遍/端到端改善。
- ASR失败历史：独立先10 failed/1.12s；助手targeted-1/3为1 failed/60 passed，targeted-2为1 failed/9 passed（均独立保留）。Codex独立1 failed/48 passed/1.11s确认自有测试非法flag期望错误；新增采样率2 failed/1 passed/0.52s捕获int截断。交回dsh按具体要求修正，targeted-4 **64 passed/2.50s**。独立测试未修改，助手报告承认默认配置误判；full727全部通过，无skip掩盖失败。
- 初次真实模型探测整仓库快照IncompleteSnapshotError；按库相同必需文件清单local_files_only重新核实后，已有缓存模型正常加载。未下载、未重装，不将过严探测当真实模型缺失。原始两份报告均保留。
- 此前预热代码`8201713`及文档`5a2c1a5`双平台704项CI已验证；预热[复审已返回无重大问题](https://github.com/Hensircast/srtp_Voice/pull/25#issuecomment-5904481921)，不是随后ASR代码的批准。没有未解决的旧线程，PR保持Draft未合并。
- 预热配对：未预热首句 **560.000/580.781 ms**，预热后 **247.738/238.604 ms**；监听前准备 **476.709/470.573 ms**，准备加首句 **724.447/709.177 ms**，总量反而增加128–164ms。只声称首句 TTS 等待转移，不声称整体冷启动/endpoint 或自然度已改善。没有换模型/声线、没有录音/播放。
- 失败历史保留：独立先8 failed/1.00s；助手三轮11 failed/65 passed、3 failed/73 passed、2 failed/74 passed，分别targeted-{1,2,3}.log；独立再5 failed/3 passed/0.84s。助手将属性调用错误误判成测试桩；报告称.env.example已更新但当时无差异，均由Codex核对修正。自动审查拒绝改独立断言的补丁，最终独立测试未修改，在代码侧实现严格新开关及异常契约；未绕过拒绝。

- DeepSeek 主执行 Piper 常驻、双阶段播放、自然分句、口语提示及测试；Codex 审查跨模块设计/关键差异，独立补测并接管资源所有权、reader 绑定、取消/关闭/启动失败竞态及边界裁定。
- 流式 Piper 独立会话复用进程；同步路径仍单次 CLI。合成/播放有界重叠，取消旧轮不播放预取内容；队列真实完成计数，无固定 join 等待。关闭超时如实报错并保留可回收状态。
- 自然模式不在普通逗号/顿号/列表冒号处早切；保护跨 token 小数、版本、常见英文缩写，超时/长度 fallback 优先完整子句。同步与流式提示首句直接回答，保留用户长答/重复要求。
- LLM 小块读取已由真实本地 HTTP 测试验证首 token 不等后续数据；这不是对所有 Ollama 响应传输的普遍提速证明。多句播放时长记录最后完成。
- 第二条 Review 修复后最终本地 `python -m tools.workbench validate --profile full`：**680 passed / 41.46s / exit 0**；compileall/pip check 通过，比基线 601 项增加 79 项。唯一 GUID 临时目录，未改全局 TEMP/权限，无 skip 掩盖失败；先前 666/676 项仍是历史结果，不能替代本轮新 SHA CI。
- 代码提交 `7761a71` 的 [CI 36665524047](https://github.com/Hensircast/srtp_Voice/actions/runs/36665524047) 已核实双平台 success：Windows Python 3.11 **666 passed/23.29s**、Ubuntu **666 passed/15.94s**，编译/pip check 成功。
- 真实已有 Piper、同一模型、固定公开六句、one-shot→persistent→persistent→one-shot：后续中位数 **499.080→228.149 ms、518.718→232.634 ms（下降 54.29%/55.15%）**。首次常驻反而慢约 61–101 ms，未实现/宣称冷启动提速。
- 源码内 `python -m tools.benchmark_piper` 已真实运行：六个有效 WAV，进程回收，后续中位数 215.859 ms；正式比较仍用上方配对结果，不挑最好数字。
- 未真人试听、未录麦克风、未验证新提示的真实 Ollama 内容质量或完整 endpoint_to_playback；不把 TTS 小样本当自然度评分/端到端改善。`tts_style` 仍是元数据，不宣称实际 pitch/speed 情感控制。

## 证据、失败与旧 Review

- 详细范围、配对结果、复现与 6–10 轮 PowerShell 指令：`docs/development/V1.8_NATURAL_VOICE.md`。本地证据目录 `outputs/workbench/voice-ux-20260930/`，原始 WAV/报告/日志均不提交。
- `codex-evidence.json` 记录独立验证、失败历史及限制；audit/piper/delivery/enumeration 各有限任务和短报均在同目录。首版 Piper 19 failed/46 passed、播放首版 7 failed/72 passed、批末 2 failed/75 passed、生命周期独立 4 failed/线程警告均真实发生。首版播放日志被助手后续覆盖，仅其计数/症状可核对，不声称完整原日志仍在。
- Codex 独立失败捕获后修正，不以助手“完成”验收。新枚举测试错误禁止硬上限的完整逗号子句边界，按批准设计改成明确子句/无损/长度断言；原兼容断言保留。
- [#24](https://github.com/Hensircast/srtp_Voice/pull/24) 与 [#23](https://github.com/Hensircast/srtp_Voice/pull/23) 保持 OPEN/Draft，不合并。#24 当前未解决线程 **0**；最新 `49bbb6d` 复审请求返回“git ref does not exist”错误，不是批准或待回结论。新 PR 实际推送后重新请求 Review。
- 基线 [CI 36573909027](https://github.com/Hensircast/srtp_Voice/actions/runs/36573909027) 对应 `49bbb6d`，Windows/Ubuntu 各 601 项成功；不得用它替代本轮新 SHA 的 CI。
- #25 已在实际 SHA 存在后[请求 Codex Review](https://github.com/Hensircast/srtp_Voice/pull/25#issuecomment-5903599378)，返回 P2 数字后句号永久误保护（thread `PRRT_kwDOSvoFtc6nYL3n`）；不是审批通过。
- 文档 HEAD `8b99f7e` 的 CI 36665793150 双平台各 666 项成功。Codex 独立复现 Review **4 failed/2 passed/0.26s**，原 dsh 于 11:52 有限修复，日志 **47 passed/1.75s**；助手报告 source_head/自然件子计数沿用旧值，不当现场事实，且其单字符工具调用超过批准 8 次（已承认，停止），不静默扩大预算。
- Codex 加测普通空白与慢 token 续接，再出现 **2 failed/8 passed/0.25s**；直接补最小超时保护、统一 lookahead 与候选清理，定向 **54 passed/2.85s**、完整 **676 passed/39.61s**。
- 修复 `1d62616` 的 [CI 36667012047](https://github.com/Hensircast/srtp_Voice/actions/runs/36667012047) 已核实 Windows **676 passed/22.44s**、Ubuntu **676 passed/14.46s**，编译与 pip check 均成功。随后带证据[回复并解决该线程](https://github.com/Hensircast/srtp_Voice/pull/25#discussion_r4140694450)，再请求复审；无新结论时保持待审，不冒充批准。
- 第二次 Review 返回 [P2 关闭超时仍释放活动资源](https://github.com/Hensircast/srtp_Voice/pull/25#discussion_r4140712496)，thread `PRRT_kwDOSvoFtc6nYahg`。Codex 接管跨模块所有权，独立 **3 failed/1 passed/1.81s**，修正关闭状态/串行关闭/延迟资源释放；受控阻塞、裸 session 重试（含已自行退出线程）及线程已停的异常回收四项门禁，定向 **45 passed/4.67s**、完整 **680 passed/41.46s**。
- 修复 `023979e` 的 [CI 36668128009](https://github.com/Hensircast/srtp_Voice/actions/runs/36668128009) 核实 Windows **680 passed/23.12s**、Ubuntu **680 passed/16.25s**，编译/pip check 成功，随后带证据[回复解决](https://github.com/Hensircast/srtp_Voice/pull/25#discussion_r4140770472)。已收到线程均闭环；最终文档保存后请求新复审，未返回时如实待审，不无限轮询。DeepSeek 无新任务。

## DeepSeek、环境与资源现场

- 复用已登录桌面 `E:/dsh_app/DeepSeek Harness.exe` 原“SRTP 工作台试点读取核验”，没有新对话/第二服务/认证绕过。最新已完成 **27轮/420步**，界面空闲、无新任务；full退出，代码CI核实后Python/Piper进程数 **0**，未按历史PID杀进程。应用保留开启。
- UI标识DeepSeek-V41-Flash/Max/完全权限，不冒充底层审计。DeepSeek剩余额度不可读，累计171M token/99%缓存、上下文66%不是余额或受控节省证据；失败有限批已结束，无新任务，不声称已经节省百分比。
- 本批代码CI核查后Codex剩余约 **66%（5h）/21%（7d）**，读数可能延迟；只完成本批交接/最终CI/Review检查，不为额度重置擅自开更多批次，沿用10%减批/7%保存，不兑换/无限续跑。
- Python 3.12.10，现有依赖/音频 metadata/Piper/SER 只读自检正常。默认 `127.0.0.1:11434` TCP 超时且监听条目 0：当时默认 Ollama 服务不可用，不证明模型缺失，不重装/换模型/自行启动第二实例。
- 本批下载/安装/训练/付费均 **0**，只用公开文本与假模型/假音频验证分句，不运行真实模型或设备，本地产物忽略不提交。最新同口径可访问元数据 **67335文件 / 2,905,728,257 bytes（约2.706GiB）/1读取错误**，只是项目占用下界，不含仓库外既有模型缓存，不据文件计数变化声称清理/缩容（本批未删除历史产物）。没有改ACL消除缺口。

## 尚未完成与恢复顺序

1. 核对额度、HEAD/dirty、原 dsh 闲置和本轮证据；不重复实现、测试并发或投递同一任务。
2. #25 已创建并附到当前聊天；三条 Review 均在修复推送/相应 CI 成功后闭环，不重复实现/提交/回复。恢复核实实际 HEAD/CI 与新复审；合理新意见验证修复、通过并推送后再回复解决；无结论/错误如实记录。
3. 按自然语音文档的新 GUID 目录和同轮配置快照做真实 6–10 轮复测，分开首次/后续，评估碎句、空档、措辞、取消与 endpoint_to_playback；若 Ollama 未运行，用户先开启已有服务，保持原模型/实际配置地址。
4. 不因 Piper 复用数字好看就更换声线或下载权重；声线自然度下一步须有真人反馈和对应授权。
