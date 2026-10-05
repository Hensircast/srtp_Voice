# SRTP 当前工作断点

## 最新现场（10/05 分句截止收口，优先于以下旧快照）

- 基线fe1d9a1已提交/普通推送，完整1200与精确CI37314000292双平台成功。PR24已普通合并85e4ecf、合并后精确CI37312662289双平台成功；不重复旧闭环。本轮新增自然idle桥/25项新回归的默认完整 **1225/87.04s/exit0**、编译/pip check0；实际唯一系统GUID根/测试根存在，idle-final-full.log。最新修复提交/推送、精确双平台CI和新Review在保存本节时待完成，旧CI不批准新代码。
- PR25在main上ready但未合并。新P2 comment4184486673/thread PRRT_kwDOSvoFtc6pDSBq：阻塞token迭代不触发自然分句截止。DeepSeek主执行有界桥及自有件，Codex独立原9项4/5红灯→全部绿灯，追加取消、legacy调用线程、未启动/部分启动source关闭边界；最终独立14/3.49s全绿。Codex只接管原子source claim关闭判断的小子问题，保留原错，避免跨线程关闭正在执行的源；legacy保持原同步，不改模型/分句参数/旧断言。
- 原dsh最后核实49轮654步空闲，无新派工。当前验证已退出，交付再核对进程。可靠DeepSeek余额未知；最近Codex剩50%短窗口/77%周窗口（读数可能延迟），未付费/兑换。助手多次自有夹具修正超限（本批followup报告11工具超7）如实保存，不接受为合规；时间报告不是现场实测，停止重复试错/续派。
- 最新证据索引 outputs/workbench/resume-20261005/run.md，最终在线现场读该目录final-state.json并核实Git/Review。入口 V1.8_IDLE_SENTENCE_DEADLINE.md 提供精确同配置6–10轮PowerShell设备复测，ASR预览对照另见V1.8_ASR_PREVIEW_POLICY.md。合成门控只证明deadline在backend仍停词时生效，未验收真实延迟/老人儿童听感；HTTP读阻塞只能等已有读超时后由reader关闭，不宣称可强杀/所有线程立即退出。
- 本节随本轮修复保存，提交时在线门禁未完成，不冒充已合并。保留管理副本、首启动exit101、共享cache警告和所有红灯/失败；后续使用独占cache，不改ACL。下载/安装/训练/模型替换/付费0，主目录2.728GiB为较早元数据下界，有1读取缺口且不含后续日志、外部缓存/管理副本。最终现场允许本地补记，不为状态更新不断制造新的Review提交。

## 本次恢复收口结果（10/05，优先于以下旧快照）

- DeepSeek原对话执行ASR路径投影及回归，Codex裁定保守slash身份契约并独立14项从6/8红灯验证到14/0。d7935c3已提交/普通推送：默认完整776/19.12s、编译及依赖检查0，精确CI37311205062双平台成功；回复4184092888后才解决4182409500。
- PR24最新复审5994704083明确d793无重大问题，未解决线程0；普通match-head merge85e4ecf于10/05完成，未管理员绕过/删分支。main精确运行37312662289双平台jobs均成功。PR25当前仍Draft依赖工作台，最新源代码合入810e391，最终推送/CI/Review/改main基线待完成，不能拿旧通过批准。
- 主目录第一次完整6failed/1192passed/81.38s原因实证为Python模拟Piper继承GBK而非ASR/真实Piper故障；原日志与有界实际AB保留在outputs/workbench/resume-20261005/。DeepSeek只改test_piper_session的fake UTF-8协议，保留旧断言并加gbk/cp1252、中文换行/路径和exact WAV帧数回归；自有21/11.29s通过。Codex同一实际探针修复后两模式均成功，默认完整1200/80.66s/exit0、编译/pip check0，真实系统GUID根存在（final-full-f0382f66dd4f4497bc61026db81f7082.log），未强制runner/全局UTF-8或换模型掩盖。
- 原dsh主体任务12工具/12内完成；后续10超5、fixture7超6均保留，不宣称预算全达标。后续独立件精确路径缺失造成无效查找/同名收集错误，Codex记录派工不足及助手应达限停止；后续任务给精确路径与一次验证，不无限续跑。DeepSeek可靠余额未知；阶段Codex剩84%短窗口/83%周窗口。
- 当前全部新证据、任务书、失败和报告索引在outputs/workbench/resume-20261005/run.md；管理副本保留。下载/安装/训练/模型替换/付费0，语音真实设备/老人儿童听感仍未验收，不将来源/测试替身修复当响应提速。设备6–10轮AB入口V1.8_ASR_PREVIEW_POLICY.md，模型替换仍需单独许可。

本次恢复（10/05）：额度刷新短窗口100%/周85%，此前保护期结束；主目录HEAD53a6b9f，仅既有本地保护补记修改，管理副本a9bc5ca干净。只读doctor正常、项目相关进程0。PR24仍仅ASR_MODEL路径投影P2未解决，PR25仍Draft依赖24。原dsh44轮613步空闲已核实，有限任务书outputs/workbench/resume-20261005/task-asr-path.md已准备，计划在原对话派发；派发/验收状态以本轮report/日志和在线现场为准。停止重复旧实验，模型/用户.env不动。

最终现场补记（保护期本地保存，未随53a6b9f推送）：Codex剩6%短窗口/85%周窗口，已停止新派工；53a6b9f已推送且精确CI37290626228双平台成功，批准的最后只读检查无本项目Python/Piper/Ollama或Git进程。PR24的ASR路径P2仍未修复、24/25不合并；最新最终复审留待恢复。完整PR长说明的发布被安全审核拒绝后未重试，改为仅非敏感工程状态评论5991848430/5991848979成功。最新机器可读恢复现场见 outputs/workbench/accessibility-20261005/protection-state.json；本补记和该ignored文件是本地保存，恢复时保留。

## 额度保护收口（2026-10-05，优先于下方旧快照）

- 已核实授权：仅本项目完全管理及普通门禁 merge；DeepSeek 主执行，Codex 审查/边界攻关。模型替换仍须用户单独许可。停止新派工与新优化，不兑换额度/付费/无限续跑；最近 Codex 短窗口剩9%、周窗口86%，读数有延迟，恢复先刷新。DeepSeek可靠余额未知，最后原对话44轮613步空闲，不把累计用量当余额。
- 主目录代码 HEAD baddd13c993d5b1aaa63a56e8cabaf69676a2756 已正常合入 a9bc5ca 工作台修复。默认 full：1172 passed/80.03s/exit0，compileall/pip check均0；证据 outputs/workbench/accessibility-20261005/full-protection-final-5a6af22ad16942398bfa51076f8be572.log。此前远程866798a的双平台CI37288484656成功，不代替下一次推送的精确门禁。当前记录提交/推送及其CI需恢复时核对。
- PR23已普通合并。PR24远程 a9bc5ca5ce8ed33d760785dc61cfa2653aa9a23c，精确CI37289108336双平台成功；超大JSON数字/跨系统任务路径两条P2已在修复推送和验证后回复4182492712/4182493127并解决。新P2 comment4182409500/thread PRRT_kwDOSvoFtc6o-Hca：path-like ASR_MODEL 未经隐私身份投影，尚未修复；不导出这类私有路径配置，不解决线程、不合并24。
- PR25仍Draft、基线codex/v1.8-workbench；现有线程无未解决项不等于新HEAD批准。含相同未修复工作台边界；24完成前不改为main/ready，不合并25。最终说明/复审请求及在线结果需核对，不拿旧review冒充最新批准。
- 验证表述更正：此前三次管理副本“项目外/系统临时根”本地命令未给PYTEST_ADDOPTS的Windows反斜杠正确加引号，被shlex去除，实际生成项目内目录。原通过数量/退出码真实，但不能当本地项目外形状证据；不是机器安装/ACL故障。三个明确归属本轮的目录已可恢复移动至管理副本 outputs/workbench/review8-20261005/recovered-temp/，无删除。修正为前斜杠且加引号后的新独占系统根真实存在：完整750/18.99s、编译/pip检查0；日志 full-actual-outside-89ff89507d90403c82e96471bb3dc22c.log。GitHub Windows/Ubuntu成功证据不受这一本地命令问题影响。
- 管理副本继续保留 C:/Users/lenovo/.codex/worktrees/v1-8-workbench-review/srtp_Voice 与全部失败/真实模型/临时证据，未归档。主目录和管理副本测试均已退出；最新项目进程检查在沙箱被拒，需批准的只读核查，不按旧PID结束进程。
- 有效体验改动是可回退 ASR 预览开关，默认1未改用户.env。唯一真实公开音频重放中分题端点到final p50省632.5/39/63ms，六对中位109.5ms且一对回退16ms；不代表全链路、麦克风/扬声器、老人儿童或自然度验收。韵律小收益提案未采纳；真实6–10轮AB复测入口 V1.8_ASR_PREVIEW_POLICY.md。
- 本轮下载/安装/训练/替换模型/付费0。最近主目录元数据73321文件/2926512368bytes（约2.725GiB）、1读取缺口，下界且后续日志增长；不含既有外部HF缓存及管理副本。恢复：读规则/本节→刷新额度、Git/进程/原dsh→定向复现ASR路径P2、明确普通模型ID与本地路径投影契约→DeepSeek有限修复→独立回归/full/精确双平台CI/复审→仅合格后按24再25顺序普通merge。不要重复已完成实验或闭环线程。

核实：2026-10-05（Asia/Shanghai）。先读 AGENTS.md 与本文件；详细历史按需读 WORK_HISTORY.md、V1.8_NATURAL_VOICE.md 和本轮 run.md，不重新粘贴整个对话。本文件不是模型、虚拟环境、个人数据或服务的完整备份。

## 最新收口状态（10/05，优先于下方进行时/历史）

- 主目录 codex/v1.8-natural-voice 本地代码5de3602已正常合入工作台修复、CI夹具修正和最后JSON路径字符串修正；最后远程核实fd50a7d/CI37284674448成功，本轮记录尚未推送。ASR实现f5aa091的CI37281435624双平台成功、复审5990571139无重大问题，不当新集成批准。
- PR23普通merge40d80ea及main CI37274469160成功。PR24在main上ready，第一轮五P2修复1bf211f/完整691/CI37282999618通过后已回复/解决。后续五P2修复8b3e5de：CLI文件重验完整配置与指纹、捕获来源脱敏、路径/可执行/端点身份和有限JSON，独立13失败后定向228通过；五线程待新CI/最终审查后才能闭环，不能合并。
- 首个8b的CI37285614664双平台6失败/728通过，原因是两个新夹具模拟系统临时输出但漏设假项目根。本地项目内临时根掩盖了错误，不称环境问题。只补夹具根、保留无差值/不可比断言，9f87af9推送后CI37286779611双平台成功，五线程已回复/解决；独占系统临时根完整734/19.34s、编译/pip check成功。
- 随后新P2线程PRRT_kwDOSvoFtc6o9tuw/comment4182248718指出JSON路径字段的字符串泄漏，Codex小幅按键统一投影并补8项回归。修复5f8a304已推送、完整742/18.13s、精确CI37287703762双平台成功；带证据回复4182368646并resolve，最终复审5991467915待返回，未合并24。
- 主目录最终来源/语音集成在项目外独占GUID根默认full1164/79.62s成功，compileall/pip check均0；此前1156两次完整同样成功。当前记录文档待提交/推送及精确新CI/Review。PR25仍Draft/依赖24，未合并。Git仅单次空代理+HTTP/1.1重试解决临时连接故障，未改全局代理、凭证或系统配置。
- 原DeepSeek桌面对话44轮613步已空闲，无新派工。其round8主体补丁/自有测试已完成并保留1/30、2/214失败日志；多次工具预算超限（本批约20/21对10）已说明，Codex只接管类型/投影等判断子问题。原项目进程最后检查0，恢复需刷新，不按旧PID杀进程。
- 证据仍在主目录 outputs/workbench/accessibility-20261005/，附着副本 outputs/workbench/review7-20261005/、review8-20261005/；旧展开记录不删除。整体精简断点被安全审核拒绝后改为追加本节，不绕过、不覆盖旧记录。
- 最新Codex读数剩29%短窗口/89%周窗口，DeepSeek可靠余额仍未知。下载/安装/训练/替换模型/付费0；主目录73321文件/2926512368bytes（约2.725GiB）、1读取缺口，属下界，不含外部既有HF缓存/附着副本，后续日志会增加。
- 下一步仅完成现有门禁：最终主目录full、提交/推送/精确双平台CI与复审；5f对应路径P2通过后回复/resolve、最终审查无有效遗留才普通merge24；之后25改main基线/ready并验收合并。不以旧通过、未解决0或未返回审查称完成；保留ASR默认1，设备/老人儿童试听/LLM首轮原因仍待真实复测。

## 本轮恢复（10/05，优先于以下10/01历史快照）

- 用户授权本项目仓库完全管理，包括安全merge；仍遵守AGENTS禁止项及分支保护/CI/Review门禁，不直接写main、不管理员绕过。允许项目内可回退优化，面向老人和儿童，DeepSeek主执行；模型替换须先提供证据/空间/回退方案并取得用户许可。无新下载、安装、训练或付费授权。
- 当前HEAD1659aeb，起点工作树干净；PR23→24→25三层Draft依赖均OPEN，门禁现场检查中。PR25精确HEAD双平台CI36746339880成功；最终复审5915774072无重大问题，既有五线程已解决，不再记为待返回。
- Codex额度起点剩99%短窗口/100%周窗口，旧10/01保护已结束。本轮仍执行约10%收缩/7%保护，不为凑阈值无效耗用。DeepSeek可靠剩余额度尚不可读。
- 原桌面“SRTP 工作台试点读取核验”已恢复，36轮516步且空闲；只复用原对话。离线doctor健康，项目无正在执行的Python/Piper/Ollama进程；在线doctor返回URLError警告，不等于模型丢失，不重装/换模型/自行重复服务。
- 有限审计任务已在原对话真实投递并完成，37轮526步闲置；audit.md/audit-state.json存在，仅源码推断无设备测量。Codex未采纳重复SILENCE_MS开关及未证实目标人群停顿断言；端点上限已有相关测试，空ASR已有文字提示，原报告保留不伪改。
- 韵律批已完成但未接受：真实合成输入paired暖态省约28ms，冷import116.311ms；自有targeted101/6.37s，首版1failed/6passed留存。Codex核对后因收益偏小与全局缓存/宽异常/阈值及非相邻并列保护不足，补丁恢复prosody.py到1659aeb，提案diff与新增测试归档保留；decision-prosody.md明确未接受，原助手报告不伪改。未动用户修改/旧断言，非全链路/真人测量。原dsh38轮535步已闲置。
- 本轮起点本地full992/80.64s/exit0，compileall/pip check0。沙箱基础解释器无法启动，改用批准的原项目venv执行，不改ACL/安装/全局设置。PR23已在精确CI与最终复审5989501229通过后，普通merge40d80ea入main，未删分支/重写历史；main CI37274469160成功。PR24已安全改main基线并ready，最终复审5989499057再次服务错误，未合并；PR25保留原分支，下一ASR批另验收。
- ASR开关已实现但尚未提交：默认预览兼容，0取消仅显示的partial解码，保持VAD/完整final；Codex独立首轮2failed/8passed（快照缺键、非法值泄漏），DeepSeek已修复来源隐私/快照/.env。自有夹具仍丢帧挂起，Codex10分钟停止原dsh批并局部修正，定向57/2.35s/exit0，完整1021/76.33s/exit0（编译/pip check通过）。原Indentation/BOM/9failed与挂起日志保留，不称预览已默认提速。
- PR24 ready后的两次真正Review已返回5条新P2（不是仅issue comment），4181444058/63/71及4181480072/79：doctor嵌套路径、全空turn、metadata字符串/配置完整性和关键设置指纹。Codex独立27failed/1passed复现；已创建附着的本项目v1-8-workbench-review隔离树HEAD49bbb6d9，原对话task-review24.md有限派工，主目录不切分支。当前不合并24/25、不resolve未修复线程。
- 模型首版接线错误/有限源等待已停止，副本留存；Codex接管接口、有限输入、0.8秒间隔/12次AB/BA，经合成门禁后单次有界真实公开Piper+缓存small重放exit0。独立JSON核实12轮完整PCM/final一致、off预览0/final1；分题VAD-stop→ASR-final p50差632.5/39/63ms，paired中位109.5ms，一对回退16ms，非全链路/硬件/人群测量。load5688ms/warmup1578ms单列；RSS null，未证明2GiB目标。decision-asr-model.md与真实日志/失败均留存，默认1/.env不改。
- DeepSeek主执行PR24隔离修复已有168/4.66s，Codex进一步发现版本正则过宽、只补12字段/未知完整配置与直接mapping导出旁路，原对话review24-followup.md有限7工具/10分钟继续补齐。独立/旧断言不变，未full/CI/提交；旧snapshot缺扩展键应未知而非补假来源。上一批助手承认约22工具超14与覆盖首失败日志（已转录不补造），本批明示缩限。15:43额度剩60%短窗口/94%周窗口，DeepSeek可靠余额仍不可读。

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
