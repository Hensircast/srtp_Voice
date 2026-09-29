# SRTP 当前工作断点

核实日期：2026-09-29（Asia/Shanghai）。恢复只先读 AGENTS.md 与本文件；详细历史按需查 WORK_HISTORY.md。旧 checkpoint 完整内容保留在 Git 历史。

## 目标与授权

- 建立轻量工作台与低消耗 dsh 协作，处理 #24 合理审查；不改变语音业务、同步兼容或扩大到 V1.7/STM32。本轮无新增真实语音提速/受控额度节省结论。
- 仓库分支 `codex/v1.8-workbench`；[Draft PR #24](https://github.com/Hensircast/srtp_Voice/pull/24) base `codex/v1.8-first-audio-latency`。[#23](https://github.com/Hensircast/srtp_Voice/pull/23) 也保持 OPEN/Draft；均没有合并授权。
- 已授权本项目必要读写、代码/文档发送既有 dsh 工作区、回归、显式暂存/功能分支提交推送、Draft PR/CI/Review 闭环。禁止项见 AGENTS.md；不读/提交凭证、录音、私人对话或模型权重，不改全局模型/环境/ACL/权限。
- 下载/训练/付费/磁盘峰值预算尚未知；本轮无这些动作，首次确有需要再集中确认。

## 当前结果与审查门禁

- 第六轮修复最新本地完整 **601 passed/16.80s/exit 0**，compileall/pip check 成功；前一 594 项代码 `58c39d5bad2936efed6edd5693cb2524c11ace91` 已推送/双平台通过。当前保存提交还须核对其新 SHA/CI，见当前 evidence 与在线 HEAD。恢复起点 512 项，共增 **89 项回归**；本轮实际离线 doctor 全部 ok，无模型加载/录音/播放/网络请求。
- 新代码 [CI 36571425999](https://github.com/Hensircast/srtp_Voice/actions/runs/36571425999) 已核实 SHA=58c39d5，Windows **594 passed/7.72s**、Ubuntu **594 passed/4.44s**，编译与 pip check 均成功。附属文档保存提交也查其真实 CI，不用旧 SHA 替代。
- `61581e5` 的 [CI](https://github.com/Hensircast/srtp_Voice/actions/runs/36566751094) 曾 Windows success、Ubuntu **5 failed/550 passed**；原生 POSIX 路径误判修正于 `f15e2d0`，[CI](https://github.com/Hensircast/srtp_Voice/actions/runs/36567732865) Windows **561/8.52s**、Ubuntu **561/6.19s** 成功，失败历史保留。
- 跨 ID 会话 OS 锁、空逐轮证据、严格 boolean、离线 UNC、原生路径五条 P2/P1 均在 f15e2d0 推送/双平台验证后带证据回复/解决，不重复处理。更早六条也已解决，详细证据在历史。
- f15e2d0 第五次 Review 又返回两条 P2：`PRRT_kwDOSvoFtc6nGwTw` 无共同指标仍可比，`PRRT_kwDOSvoFtc6nGwT3` 未检查直接依赖版本。修复推送于 58c39d5，确认该双平台 CI 后已分别[回复/解决](https://github.com/Hensircast/srtp_Voice/pull/24#discussion_r4133738916)、[回复/解决](https://github.com/Hensircast/srtp_Voice/pull/24#discussion_r4133739432)。当前所有已收到意见均闭环，不把最新请求复审冒充通过。
- 58c39d5 的[第六次复审](https://github.com/Hensircast/srtp_Voice/pull/24#issuecomment-5890738349) 返回两条新 P2：`PRRT_kwDOSvoFtc6nHfj-` 混用导入/测量配置，`PRRT_kwDOSvoFtc6nHfkC` POSIX 字面外来绝对路径名外泄。保存提交在写入前被门禁拦下；独立件修复前 **4 failed/3 passed/0.84s**，原对话有限修复后 Codex full **601 passed**。此断点为发布前快照：新提交 CI/两线程闭环须在线核对，不把本地通过当远端通过。

## 执行与证据入口

- DeepSeek 在原 Harness 对话主执行；Codex 判断方案、检查关键差异/真实原始结果，补独立回归并处理小幅确定性边界。未把其他模型冒充 DeepSeek，也未仅凭助手“完成”验收。
- 第五轮修复前独立 **8 failed/7 passed**；首版后增补边界 **5 failed/23 passed**；反馈后助手实际 full 日志 **589 passed/16.81s**（短报误写 577，原报告保留，不当验收数字）。最后两类诊断边界真实 **5 failed/28 passed** 后由 Codex 修正，最终 full 594 通过。未弱化/跳过断言。
- 当前独立证据：`outputs/workbench/review6-desktop/codex-evidence.json`；五个有限任务文件、原报告/失败与通过日志分存 review3/5/6-desktop。旧验收有效但不替代后续修复；`outputs/workbench/codex-final-evidence.json` 是先前额度保护快照，不是最新状态。
- 第六轮 DeepSeek 从日志核对 related **184 passed/13.36s**、full **601 passed/18.10s**；Codex 独立 full **601/16.80s**。Windows 的外来路径夹具注入 POSIX-relative 表示，Ubuntu 创建实际字面文件，无 skip。批准的旧夹具改为真正不同的 recording config，comparable=False 与原因断言仍在。
- 工具入口 `python -m tools.workbench`；按需读 WORKBENCH.md（命令/PowerShell同轮实测）、PROJECT_MAP.md（故障→模块/测试）及 DSH_TASK_TEMPLATE.md（七字段派工）。日志/输出保持本地，不提交 Git。
- 基线只能由同次测量配置与真实逐轮证据比较；无共同实际 summary/group timing delta 不可比。依赖约束读取 requirements.txt，未知/无效版本/声明或缺解析器不能报健康；有效旧版本仍保留供诊断，未声明约束的可选项仅做存在性判断。

## DeepSeek 当前现场

- 已唤醒 `E:/dsh_app/DeepSeek Harness.exe`，版本 0.2.0-rc.2，复用原“SRTP 工作台试点读取核验”，未新建对话。最新已完成 **15 轮/218 步**，无停止生成/后台任务提示；独立 full 已退出。应用保留开启，未再派新任务。
- 界面选中 DeepSeek-V41-Flash / Max、完全权限；只是 UI 标识，不是底层版本审计。未改全局选择或权限。内部 UUID 本轮未重新认证查询；对话标题、旧末轮、cwd/HEAD 与实际产物独立核对。
- 旧 `tools.dsh_client` 对接历史 0.1.7-rc.2 网页协议；桌面新端口的无认证旧请求实际 401。本轮使用已登录的受支持桌面界面，未读认证文件、保存 token/cookie、启动第二服务或绕过认证。桌面认证兼容尚未实现，不能沿用旧3080/凭证假定接通。
- 历史 CLI 会话 `session-f321f435-d1c1-4b66-a82f-089cd167cc20`；smoke ledger `outputs/workbench/dsh/live-smoke-20260929.json` 和真实产物仅用于历史核对，不重复投递。恢复先核对闲置与唯一 ID，不按旧 PID 杀进程或清锁。
- DeepSeek 剩余额度无法读取；累计 token/缓存命中不是剩余额度或受控省费证据，仅派有限批次。原始报告计数差异必须从日志核对。

## 环境、资源与剩余工作

- Python 3.12.10；基础/可选依赖、音频默认输入输出与 Piper/SER 文件只读诊断正常；没有实际加载/模型/设备验收。Ollama 在线状态本轮未复测，不从历史不可访问推断模型缺失，不重装或换模型。
- 默认用户 pytest 临时目录曾 PermissionError；仅用 outputs/workbench 下唯一 GUID basetemp/cache，process-only PYTEST_ADDOPTS 用正斜杠/引号；未改 ACL、全局 TEMP/PATH，不清共享目录。
- 最近可访问元数据 **63773 文件/2887389757 bytes（约 2.69 GiB）/1 个读取错误**，只是下界；本轮本地下载、依赖安装、训练、付费、兑换均 0。
- 最新 Codex 窗口剩约 **40%（5 小时）/43%（7 天）**，时间点读数可能延迟；上次额度保护已自然恢复，没有兑换。保护规则仍为 10% 减批、7% 保存。

## 恢复顺序

1. 看额度、HEAD/dirty、#24 最新 CI/Review 和当前 codex-evidence；上方 pending 若已完成不要重复实现/回复/派工，不合并 #24/#23。
2. 本次前七条意见已在推送与相应双平台 CI 成功后解决；第六轮新增两条本地已修复，先看其新 CI 和线程是否已闭环，避免重复实现/回复。若有合理新审查再按原对话有限派工；复审未返回须如实 pending，不无限轮询。
3. 工具收口后按 WORKBENCH.md 同轮快照入口取得 **6–10 轮真实语音数据**，分开 first_observed/subsequent，才分析前两轮首响长尾；当前历史 p50/max 3398/8906 ms 无完整 turns/测量配置，不编造改善或冷启动已修复。
4. 模型、.env、虚拟环境、录音/数据与服务进程分别核实；本断点不是完整备份或认证凭证。
