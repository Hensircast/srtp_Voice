# SRTP 当前工作断点

核实：2026-10-01（Asia/Shanghai）。先读 AGENTS.md 与本文件；详细历史按需读 WORK_HISTORY.md、V1.8_NATURAL_VOICE.md 和本轮 run.md，不重新粘贴整个对话。本文件不是模型、虚拟环境、个人数据或服务的完整备份。

## 当前目标与边界

- 用户授权“继续优化直到额度临界”，DeepSeek 主执行、Codex 设计/审查/复杂问题与小确定操作。当前进入收口，停止扩展新优化，只完成已有验收、提交、CI/Review和安全保存；任一窗口约10%收缩、约7%保护，读数可能延迟，不主动付费/兑换/无限等重置。
- 仓库 D:/code/VScode/Py/srtp_Voice，已有 venv/Scripts/python.exe。分支 codex/v1.8-natural-voice，Draft PR25（https://github.com/Hensircast/srtp_Voice/pull/25），base codex/v1.8-workbench，仍OPEN/Draft，不合并。
- 已核实授权限该项目代码/文档/测试、原dsh工作区允许材料、显式暂存/提交推送、Draft PR和CI/Review。无新增模型下载/安装/训练/费用/磁盘峰值授权；录音、个人对话、.env、权重和凭证不外发、不提交。其余禁止项见AGENTS，main/global/ACL/管理员/force/reset/stash/clean不动。
- 最新代码单元cf5dfce9a510b76b1abf5d1757a17447c833c6dd；Review短答修复6046cf1与工作台限额cf5dfce已分别提交、一次推送。最后本地默认完整 **992 passed/70.72s/exit0**、compileall/pip check0；精确CI36745093363已成功，Win **992/55.33s**、Ubuntu **992/46.49s**，编译依赖通过。不沿用旧SHA成功；附属文档提交HEAD与CI现场另核实。

## 已完成与验证证据

| 独立代码单元 | 本地完整 / 精确双平台CI | 状态 |
| --- | --- | --- |
| CJK lookahead纠错 45056f6 | 815/35.05s；36730511188 Win815/21.81s、Ubuntu815/17.29s | 原P2推送/CI后回复4145902198并解决 |
| HTTP reader/response清理 80c295c | 839/41.65s；36732829801 Win839/25.23s、Ubuntu839/17.76s | 复审5914005071无重大问题，仅该SHA |
| 自有惰性HTTP连接复用 e0a3472 | 868/66.88s；36737021424 Win868/53.70s、Ubuntu868/45.31s | 复审5914617158无重大问题，仅该SHA |
| 安全服务计时 aef3497 | 956/69.17s；36741916141 Win956/53.14s、Ubuntu956/45.43s | 新复审P2见下，不视为批准 |

- 上述full/CI均compileall/pip check通过。HTTP真实回环两轮、每轮tags+chat两个请求：关闭4条TCP，复用Content-Length1、chunked2、gated EOF2；done不等EOF。仅真实传输计数，不是Ollama/设备提速实测。
- STREAM_LLM_REUSE_HTTP=1默认仅main流式Ollama显式启用，0可回退逐次模块请求；直接StrategyGenerator(cfg)不自动Session。独占lease/并发独立请求、忙close延迟、主程序资源所有权与清理异常保护已覆盖。
- 7白名单服务数字只从成功done发布，原始ns转ms；每请求隔离、最终marker绑定、清洗事件、深复制快照与脱敏baseline已接通。backend_diagnostics与latencies_ms/summary分开，缺失/取消/空/截断不造0，不新增LLM请求、不据第一轮认定冷启动。复测入口 V1.8_HTTP_DIAGNOSTICS.md。
- 更早常驻Piper、可选预热、16kPCM内存ASR与自然边界/模板措辞已经实现，不重复实验。原真实公开Piper/ASR小样本和失败限制在自然语音文档；本持续循环没有新真实模型、录音、扬声器或听感验收。

## 当前Review与剩余验收

- aef3497新P2：thread PRRT_kwDOSvoFtc6nnYHG，comment4146825471（https://github.com/Hensircast/srtp_Voice/pull/25#discussion_r4146825471）。无标点短答未flush前同步诊断sink挡住首句。
- Codex独立6 failed/7.49s复现，已最小修正为先收集清洗统计、最终文本TTS入队后发布一次；三个短答×两模式、阻塞日志仍实际假播放。相关65 passed/3.04s。修复6046cf1已推送并被最终head cf5dfce的CI覆盖；CI成功后带证据回复4146984782并解决，新复审未返回不称批准。
- DeepSeek最后小批实现validate --max-failures正int可选，默认完整旧行为/runner/退出码保持，只pytest限额；不是walltimeout、不终止挂住线程、不当产品提速。自有真实117 passed/16.57s；Codex独立70 passed/14.65s含真实两个故意失败探针1vs2失败控制（预期子实验，不是仓库门禁失败）。
- 最后默认完整full992及精确代码CI已通过，不用失败限额跳过必要验证。两个代码单元均已推送；附属文档CI/新Review在提交本断点时仍待核实，交付后的现场结果另写outputs/workbench/quota-loop-20260930/final-state.json，恢复同时核查Git/在线状态。未返回Review不冒充批准；必要时7%保护线只安全保存，不虚假交付。

## 助手、失败与资源

- 只复用原桌面DeepSeek Harness“SRTP 工作台试点读取核验”（E:/dsh_app/DeepSeek Harness.exe），最后截图核实36轮516步、空闲无新任务。读取一度错配其它窗口/无关文本，仅只读重新选取/激活，未对其它应用输入；恢复后原助手截图正确，错误文本不当其余额证据。不新开会话或服务。
- DeepSeek主执行HTTP/配置/计时/工作台与自有门禁；Codex审查原差异、独立测试，接管lease方法绑定/closed/兼容/隐私与诊断发布顺序子问题。每批工具/时间有限，不因报告“完成”验收。
- 本轮完整任务/报告/原日志/失败记录：outputs/workbench/quota-loop-20260930/run.md。HTTP首报告所称transport-targeted.log缺失；pool两份失败43/90与34/95；diag首26/103、次10/44；明确修正98通过。助手只报告的中间失败不补造日志。
- diag-2 pytest总结后6分钟未退出，Codex只读核对精确本批命令/父PID后，仅停止pytest子27560，日志保留，关联Python随后全退出；不按历史PID再杀进程。最后验证进程状态交付前重新核查。
- Codex00:42读数剩83%短窗口（自然重置后）/11%周窗口，已接近约10%收缩区且停止新派工；最终读数在final-state.json。DeepSeek余额未可靠核验，原对话累计196M/99%缓存与24%上下文不是余额或节省证据。不兑换/付费，也不为凑到精确阈值耗无意义额度。
- 本轮下载/安装/训练/付费均0。2026-10-01正常只读元数据：69871文件、2911729066 bytes（约2.712GiB）、1读取缺口，是项目占用下界，不含外部已有模型缓存。首次沙箱125缺口不当完整总量，未改ACL/清理；后续少量日志/提交会增加占用。
- 起点离线doctor健康，Python3.12、既有Piper/model/SER元数据正常；本循环未新在线确认Ollama监听。历史服务不可用不证明模型缺失，不重装/换模型/自行开第二实例。

## 恢复与下一步

1. 核对最新额度、HEAD/dirty、项目Python/Piper进程、原dsh是否闲置及本轮run.md，禁止重复派工/同任务并发。
2. 本地full、代码单元推送及P2闭环已完成，不重做/重复回复。核对最后附属文档HEAD/CI与新复审；有合理新意见再有限复现处理，不拿旧CI/未解决0当新审查批准。
3. 将本文件中的待验收状态更新为真实结果，更新Draft PR说明并保存文档；原展开断点在Git e0a3472及更早25c663e，所有本轮新增经过已写run.md/历史，不删除失败证据。
4. 恢复有额度后按两个语音文档的新GUID、同配置/题目做6–10轮真实复测；分享脱敏baseline逐轮LLM加载/输入处理/首token/TTS/endpoint_to_playback，别发录音或事件文字。已有服务未启动时由用户开启，保留实际模型和地址。
5. 优先用真实数据判断前两轮慢、播放空档与同声线自然度，再决定新方案；不先换声线/下载大模型/盲目缩VAD窗口。
