# SRTP 当前工作断点

核实日期：2026-09-30（Asia/Shanghai）。先读 AGENTS.md 与本文件；历史按需查 WORK_HISTORY.md 和 Git。旧断点完整内容保存在基线 `49bbb6d`，本文件不是模型、虚拟环境、服务或个人数据的完整备份。

## 当前目标、授权与位置

- 用户要求全项目代码审查、迭代优化响应速度与体验，重点减少语音机械感。基线 `49bbb6d9f18630624b67fb14014df3153eee18ff`，开始时工作树干净。
- 当前分支 `codex/v1.8-natural-voice`，计划发布独立 Draft PR，base 为 `codex/v1.8-workbench`；此断点是发布前快照，远端 SHA/CI/Review 尚须核实，不能称完成。
- 已授权本仓库必要代码/文档读写、发送到既有 dsh 工作区、回归、显式路径暂存、功能分支提交推送、Draft PR/CI/Review。禁止项沿用 AGENTS.md；不合并、不改 main/global/ACL、不外发录音/私人对话/权重/凭证。
- 下载、训练、安装、付费与峰值磁盘预算没有新增授权，本轮均未执行；必要时再集中确认，不能从界面“完全权限”继承。

## 当前已验证结果

- DeepSeek 主执行 Piper 常驻、双阶段播放、自然分句、口语提示及测试；Codex 审查跨模块设计/关键差异，独立补测并接管资源所有权、reader 绑定、取消/关闭/启动失败竞态及边界裁定。
- 流式 Piper 独立会话复用进程；同步路径仍单次 CLI。合成/播放有界重叠，取消旧轮不播放预取内容；队列真实完成计数，无固定 join 等待。关闭超时如实报错并保留可回收状态。
- 自然模式不在普通逗号/顿号/列表冒号处早切；保护跨 token 小数、版本、常见英文缩写，超时/长度 fallback 优先完整子句。同步与流式提示首句直接回答，保留用户长答/重复要求。
- LLM 小块读取已由真实本地 HTTP 测试验证首 token 不等后续数据；这不是对所有 Ollama 响应传输的普遍提速证明。多句播放时长记录最后完成。
- 最终本地 `python -m tools.workbench validate --profile full`：**666 passed / 39.07s / exit 0**；compileall/pip check 通过，比基线 601 项增加 65 项。唯一 GUID 临时目录，未改全局 TEMP/权限，无 skip 掩盖失败。
- 真实已有 Piper、同一模型、固定公开六句、one-shot→persistent→persistent→one-shot：后续中位数 **499.080→228.149 ms、518.718→232.634 ms（下降 54.29%/55.15%）**。首次常驻反而慢约 61–101 ms，未实现/宣称冷启动提速。
- 源码内 `python -m tools.benchmark_piper` 已真实运行：六个有效 WAV，进程回收，后续中位数 215.859 ms；正式比较仍用上方配对结果，不挑最好数字。
- 未真人试听、未录麦克风、未验证新提示的真实 Ollama 内容质量或完整 endpoint_to_playback；不把 TTS 小样本当自然度评分/端到端改善。`tts_style` 仍是元数据，不宣称实际 pitch/speed 情感控制。

## 证据、失败与旧 Review

- 详细范围、配对结果、复现与 6–10 轮 PowerShell 指令：`docs/development/V1.8_NATURAL_VOICE.md`。本地证据目录 `outputs/workbench/voice-ux-20260930/`，原始 WAV/报告/日志均不提交。
- `codex-evidence.json` 记录独立验证、失败历史及限制；audit/piper/delivery/enumeration 各有限任务和短报均在同目录。首版 Piper 19 failed/46 passed、播放首版 7 failed/72 passed、批末 2 failed/75 passed、生命周期独立 4 failed/线程警告均真实发生。首版播放日志被助手后续覆盖，仅其计数/症状可核对，不声称完整原日志仍在。
- Codex 独立失败捕获后修正，不以助手“完成”验收。新枚举测试错误禁止硬上限的完整逗号子句边界，按批准设计改成明确子句/无损/长度断言；原兼容断言保留。
- [#24](https://github.com/Hensircast/srtp_Voice/pull/24) 与 [#23](https://github.com/Hensircast/srtp_Voice/pull/23) 保持 OPEN/Draft，不合并。#24 当前未解决线程 **0**；最新 `49bbb6d` 复审请求返回“git ref does not exist”错误，不是批准或待回结论。新 PR 实际推送后重新请求 Review。
- 基线 [CI 36573909027](https://github.com/Hensircast/srtp_Voice/actions/runs/36573909027) 对应 `49bbb6d`，Windows/Ubuntu 各 601 项成功；不得用它替代本轮新 SHA 的 CI。

## DeepSeek、环境与资源现场

- 复用已登录桌面 `E:/dsh_app/DeepSeek Harness.exe` 原“SRTP 工作台试点读取核验”，没有新对话/第二服务/认证绕过。最新已完成 **20 轮/328 步**，界面空闲、无停止生成提示；本轮相关 Python/Piper 进程检查为 0，未按历史 PID 杀进程。应用保留开启。
- UI 标识 DeepSeek-V41-Flash/Max/完全权限，不冒充底层审计。DeepSeek 剩余额度不可读，累计 112M token/99% 缓存不是剩余额度或受控节省证据；仅有限批派工，当前无新任务。
- Codex 最近剩余约 **69%（5h）/37%（7d）**，读数可能延迟；沿用 10% 减批/7% 保存，不兑换或无限续跑。
- Python 3.12.10，现有依赖/音频 metadata/Piper/SER 只读自检正常。默认 `127.0.0.1:11434` TCP 超时且监听条目 0：当时默认 Ollama 服务不可用，不证明模型缺失，不重装/换模型/自行启动第二实例。
- 本轮下载/安装/训练/付费均 **0**，公开文本测试 WAV 本地生成且保持忽略。最近可访问元数据 **65178 文件 / 2,895,193,310 bytes（2.696 GiB）/1 个读取错误**，只是占用下界；没有改 ACL 来消除读取缺口。

## 尚未完成与恢复顺序

1. 核对额度、HEAD/dirty、原 dsh 闲置和本轮证据；不重复实现、测试并发或投递同一任务。
2. 显式路径提交/推送 `codex/v1.8-natural-voice`，创建以 workbench 为 base 的 Draft PR 并附到当前聊天；核实远端 SHA、请求 Review、检查对应 Windows/Ubuntu CI。合理新意见验证修复、通过并推送后再回复解决；无结论/错误如实记录。
3. 按自然语音文档的新 GUID 目录和同轮配置快照做真实 6–10 轮复测，分开首次/后续，评估碎句、空档、措辞、取消与 endpoint_to_playback；若 Ollama 未运行，用户先开启已有服务，保持原模型/实际配置地址。
4. 不因 Piper 复用数字好看就更换声线或下载权重；声线自然度下一步须有真人反馈和对应授权。
