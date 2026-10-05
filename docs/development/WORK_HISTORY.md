# SRTP 工作历史与证据索引

## 2026-10-05 恢复后的审查收口

DeepSeek主体修复ASR path-like来源隐私并补26项回归，Codex独立14项红灯→绿灯及完整776/19.12s；d7935c3精确CI37311205062双平台和复审5994704083通过，线程闭环后PR24普通merge85e4ecf，main精确CI37312662289双平台通过。语音集成首完整6/1192红灯实证为Python假Piper继承GBK：DeepSeek只纠正fake协议编码、补2项legacy编码强回归，旧断言/生产Piper不变；独立子进程AB和默认完整1200/80.66s通过。失败、预算偏差与限制保留在outputs/workbench/resume-20261005/run.md，PR25后续在线门禁以实际结果为准。无下载/替换模型或真实设备/听感提速结论。

## 2026-10-05 额度保护补记

本轮主目录集成 baddd13c 默认完整1172/80.03s通过；管理副本a9bc5ca完整750，精确双平台CI37289108336成功后才回复/解决超大数字与跨系统任务路径两条P2。新ASR模型路径P2未修复，PR24/25不合并。详见WORK_CHECKPOINT.md顶部保护收口，不将历史通过当当前批准。

更正保留：此前三次所谓本地“项目外临时根”被PYTEST_ADDOPTS的反斜杠解析成项目内目录，不能作为项目外形状证据；测试数/退出码与GitHub双平台CI真实。原目录可恢复移到管理副本outputs/workbench/review8-20261005/recovered-temp/，没有删除。带引号前斜杠的新系统根真实存在，独立完整750/18.99s通过；full-actual-outside-89ff89507d90403c82e96471bb3dc22c.log保留修正证据。未掩盖失败或改全局权限。

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
- 新工具提交 `55e3b40`，独立 [Draft PR #24](https://github.com/Hensircast/srtp_Voice/pull/24)。首次 CI Windows 成功、Ubuntu 1 failed/478 passed，未冒充全通过；Codex P1 指出跨系统路径脱敏、P2 指出 latency map 类型。修复 `058c5d1` 本地 **488 passed/13.72s**，[双平台 CI](https://github.com/Hensircast/srtp_Voice/actions/runs/36544108954) Windows **488/7.68s**、Ubuntu **488/2.87s** 均成功。P2 修复前精确复现 2 failed/2 passed 的 AttributeError。两线程在推送/验证后回复并解决，已请求修复复审，未把 bot 的接收反应当通过。

- `058c5d1` 的第二次 Review 返回四条统计/依赖问题。NumPy 实际为 soundfile 传递依赖，基础清单改为显式 requirements；比较 summary 重用严格数值/顺序校验；背压保留并验证；忽略字段与 ID 重命名分别计数。独立修复前 **22 failed**，修复后完整 **511 passed/34.01s**。既有比较夹具改用合法整体分布平移，保留 +100 ms 断言；未知字段三项和重命名一项分别精确断言，不减弱隐私检查。提交 `66f2b4d` 的 [CI](https://github.com/Hensircast/srtp_Voice/actions/runs/36545870096) Windows **511/9.37s**、Ubuntu **511/3.24s** 均成功，四条线程带证据回复/解决；至此已处理全部六条意见。
- 最后新增缺失背压计数为 null 而非实测零的回归，完整本地 **512 passed/17.60s**，编译与 pip check 成功。本记录保存时，最后修正的提交与 CI 尚待核实；最终结果保存到本地 `outputs/workbench/codex-final-evidence.json`，恢复仍需匹配 PR HEAD 在线复查。
- 最后提交前第三次 Review 返回四条新 P2：跨派工 ID 的会话互斥、空 per-turn 证据、outcome 严格 boolean、离线 UNC 网络访问。四条尚未修复/解决，不能称复审通过或任务全部完成。最新额度剩约 **7%/52%**，按用户准则启动断点保护，不再新开发/派工、不兑换；保存修复清单及安全停止点。认证控制进程均已退出，dsh 最后 idle/queue=0。

## 2026-09-29 晚：Harness 桌面恢复与第三轮审查修复

- 唤醒已安装的 Harness 0.2.0-rc.2，在原“SRTP 工作台试点读取核验”中串行执行两批有限任务；未新建对话，未修改全局模型/权限/配置。界面显示 DeepSeek-V41-Flash / Max；新回环端口的未认证旧客户端收到 401，没有绕过或读取凭证，改用已登录桌面界面。
- 原提交 a8021a2 的双平台 CI 在线核实 success。Codex 用该提交源代码独立复现四个真实缺陷：模拟接受两次派工、空逐轮可比、字符串 false 转真、8 次被拦截 UNC 探测；未真实联网或重复派工。证据脚本在 outputs/workbench/review3-desktop/。
- DeepSeek 实现主体。首版助手回归 12 failed/3 passed → 15 passed；Codex 独立验收仍 4 failed/22 passed，发现侧锁名冲突、未知逐轮字段及混合 UNC。具体反馈后修正，完整独立 553 passed/14.62s。再补诊断解析顺序/警告语义，修复前 2 failed，最终 **555 passed/14.92s**，compileall/pytest/pip check 全部 exit 0，比恢复起点增加 43 项回归。
- 原 report.json 中的 fixed/环境判断不是 Codex 验收；保留它和失败日志，后续独立验收写 codex-evidence.json，不覆盖失败历史。未见遗留项目测试进程；桌面助手已停止本批，应用按用户意图保留开启。
- 此记录为发布前证据快照：新提交 CI 与线程闭环须匹配在线 HEAD，不能用 a8021a2 的通过代替新代码。无新增语音速度或受控额度节省实测。本轮本地下载/安装/训练/付费/兑换均为 0；可读元数据约 2.69 GiB/63036 文件，1 处读取缺口。
- 发布 `61581e5` 后实际 [CI](https://github.com/Hensircast/srtp_Voice/actions/runs/36566751094) Windows success、Ubuntu 5 failed/550 passed。第四次 Review P1 与该失败根因相同：把所有 `/` 开头的 POSIX 原生绝对路径误当外来路径。Codex 修复纯语法分类、增加 6 项跨平台回归并纠正两项新增 probed 期待值（原隐私断言保留），本地完整 **561 passed/15.63s**，编译与依赖一致性通过。发布新补充提交并核实其 CI 后才回复/解决五线程；新复审结论须另查。
- 后续发布 `f15e2d08daf1b8e742a30759579ccae7027fd9a8`，[精确 SHA CI 36567732865](https://github.com/Hensircast/srtp_Voice/actions/runs/36567732865) 实际 Windows **561 passed/8.52s**、Ubuntu **561 passed/6.19s**，编译和 pip check 均成功；较恢复起点共增 49 项回归。确认后五个 P2/P1 线程逐条带证据回复/解决，未隐藏或 dismiss 审查。一次回复参数格式错误没有产生写入，修正后核对全部五个成功结果。已请求最新复审，保存时没有结论；不合并 #24/#23。原 Harness 对话保持 12 轮/176 步、闲置，没有为了状态同步再启动新任务。

## 2026-09-29 晚：第五轮复审与最终工作台验收

第五轮复审在 f15e2d0 又返回无共同指标/基础版本约束两条 P2。Codex 独立修复前 **8 failed/7 passed**，交回原 Harness 对话有限执行；首版完整 577 通过后，增补实际边界 **5 failed/23 passed**，具体反馈由 DeepSeek 修正。助手 refine-full.log 实际 **589 passed/16.81s**，refine-report.json 误写 577，原报告保留并在 Codex 证据中校正，不盲信短报。最后两类元数据/声明未知防护，修复前 **5 failed/28 passed**，由 Codex 小幅修正并去掉重复读取；最终独立统一 full **594 passed/16.01s**、编译/pip check 和真实离线 doctor 全部成功。源码两模块与独立件已显式提交推送于 `58c39d5bad2936efed6edd5693cb2524c11ace91`；本记录保存时 [CI 36571425999](https://github.com/Hensircast/srtp_Voice/actions/runs/36571425999) in_progress，两线程待该 CI 成功后处理，已请求复审，不合并。原对话现 14 轮/205 步、闲置且无项目测试进程。本轮占用下界 63773 文件/2887389757 bytes/1 处读取缺口，无下载/安装/训练/付费/兑换，也无新增语音/省费实测。

后续在线确认 `58c39d5` 的 CI Windows **594 passed/7.72s**、Ubuntu **594 passed/4.44s**，编译/pip check 全部成功；两线程随后带证据回复/解决。至此本次七条审查意见均已验证闭环；最新复审仍待结论。恢复断点已精简，前版全文和历史失败保留于 Git/日志，不靠重复粘贴全部上下文恢复。

## 2026-09-29 晚：第六轮测量来源与隐私修复

第六次复审在 58c39d5 又返回测量/导入配置混淆和外来路径位置外泄两条 P2。保存提交在写入前被门禁停止，没有继续盲推。独立件真实 **4 failed/3 passed/0.84s**，原 Harness 有限任务仅改基线模块和一处预先批准的测量配置夹具，不弱化原 False/原因断言。助手日志 related **184/13.36s**、full **601/18.10s**，Codex 独立 full **601/16.80s**、编译/pip check 全部通过。Windows 以 POSIX-relative 边界注入，Ubuntu CI 创建真实字面 C:/UNC 文件名，没有 skip。原对话 **15 轮/218 步**已停止；此为发布前快照，新增提交 CI/两线程状态须在线与本地 review6 独立证据核对，不能冒称全部完成。

## 2026-09-30晚至10-01：有界持续优化与额度收口

从干净25c663e、离线doctor健康恢复。CJK新P2由Codex一行ASCII条件+12项门禁修复45056f6，完整815及精确CI36730511188绿后回复解决。原DeepSeek继续主执行HTTP生命周期80c295c（839，CI36732829801）与自有Session复用e0a3472（868，CI36737021424）；两SHA各自复审无重大问题，不当后续批准。借用模块不关闭，惰性独占租约、忙关闭延迟、并发独立fallback；两轮tags/chat真实回环连接off4/on length1、chunked2、gatedEOF2，不等EOF。无本轮模型/设备速度主张。

DeepSeek主执行7字段服务计时末marker、事件、深复制快照与安全baseline；Codex独立补空/截断、交错请求/echo隔离、隐私与真实代码fake管线门禁。完整956/69.17s，aef3497精确CI36741916141 Win956/53.14s、Ubuntu956/45.43s。新复审P2：无标点短答先诊断sink后flush，独立6failed/7.49s；Codex隔离修复6046cf1先最终文本入队，65passed/3.04s。原DeepSeek另实现默认不变的可选validate --max-failures，独立真实失败探针验证控制、合并70通过，代码cf5dfce。两单元分别提交、一次推送；最后本地默认完整992/70.72s、编译与依赖成功，精确新CI/Review闭环按当前断点与在线核实。

保留负面证据，不背书中间稿：HTTP首报告声称日志缺失；pool43failed/90passed与34failed/95passed，Codex只接管方法绑定/closed/兼容与脱敏属性映射；diag26/103与10/44，遗漏import、错误夹具、原dict发布、全量copy、浅snapshot经明确反馈修正98通过；助手误猜桩/事件序列的报告原样保存。diag-2总结后挂住，核对精确本批PID/父/命令后只终止27560 pytest子，关联Python全退出，未删日志/改ACL；单纯输出总结不是正常exit。failfast首4/11漏python绑定仅报告记录，无独立原日志不补造。

原dsh最后36轮516步空闲；本地任务/失败/报告/XML详证outputs/workbench/quota-loop-20260930/run.md。旧展开断点保留于Git e0a3472（更早25c663e），新增经过保留本轮run，本次恢复文件精简而不是丢历史。周额度约11%进入收口，10%缩批、7%保护，不因短窗口自然重置兑换/无限续跑。无下载/安装/训练/付费，新项目元数据下界69871文件/2911729066bytes/1读取缺口，不含外部缓存。原真实Piper/ASR小样本继续在自然语音指南，不重做或混入此批假模型门禁。

最终代码cf5dfce的精确CI36745093363双平台 **992** 通过（Win55.33s/Ubuntu46.49s），compileall/pip check成功。随后回复4146984782并解决诊断短答P2，未合并/不当新复审批准；附属文档CI/新Review按现场核对。

## 2026-10-05：授权合并、首响可回退与来源门禁

- 用户新授权允许本项目普通受保护 merge；PR23 在精确 CI/最终复审后普通合并40d80ea，main CI37274469160成功。未删分支、改权限或直写main。
- DeepSeek原桌面对话主要执行审计、ASR开关、工作台修复与自有测试；Codex裁定方法、核对原始证据、独立回归并局部接管挂起夹具/重放接口和投影契约。韵律候选暖态仅28ms、冷import116ms及边界风险被否决，提案/失败仍保留。
- ASR f5aa091 默认预览兼容，显式0减少仅显示的解码，完整final/同步不变；定向57、full1021、CI37281435624双平台成功，最终复审5990571139无重大问题。真实已有Piper/缓存small的12轮公开重放显示三题端点到final p50省632.5/39/63ms、六对中位109.5ms且一对退步16ms；不称全链路或老人儿童听感验收，RSS缺测不造0。
- 工作台两轮新五P2均独立复现（27/1及13失败）；DeepSeek主体实现，Codex补键感知投影、严格类型/来源、无路径探测与有限JSON。1bf211f完整691/CI37282999618双平台成功；9f87af9完整734（项目外独占GUID根）/CI37286779611双平台成功，各五线程在证据齐备后回复/解决。8b初次双平台CI6失败/728通过是新夹具漏设假项目根，失败留存、只修夹具、不改路径保护或降低断言。
- 随后来源复审指出JSON路径字符串仍可外泄，5f8a304统一五个路径字段投影，补8项加载/重载/导出与CLI回归，完整742/18.13s及精确CI37287703762双平台成功后回复4182368646/解决；最终复审5991467915另核对，不称已合并。主目录最终代码5de3602的CI形状完整1164/79.62s成功、compileall/pip check0。
- 主目录正常merge保存两轮修复，完整1113及1156通过，包括系统临时根形状；新来源修正集成后须最后复验/推送。所有晚返回意见继续检查，不用旧SHA批准后续提交。工具预算多次超限和被覆盖首失败日志均如实记录，未冒充额度节省。
- 原始任务/日志/失败/模型JSON保存在主目录outputs/workbench/accessibility-20261005及附着副本review7/review8；DeepSeek44轮613步已闲置，无新模型下载/替换、安装、训练、付费或全局设置修改。最新主目录占用下界约2.725GiB、1读取缺口，外部缓存/附着副本另计。

## 按需加载索引

- 当前目标、授权缺口、助手状态及恢复步骤：WORK_CHECKPOINT.md。
- 长期规则：仓库根目录 AGENTS.md；派工格式：DSH_TASK_TEMPLATE.md。
- V1.8 全流程：V1.8_AUTOMATION_LOG.md；真实设备验收：V1.8_REAL_DEVICE_TESTS.md。
- 环境变量历史审计：V1.8_ENVIRONMENT_RESTORE.md；首响原因和逐轮测试命令：V1.8_FIRST_AUDIO_LATENCY.md。
- 本地输出/录音/配置/模型不在本索引中展开；任务实际需要时按授权访问，外发和 Git 提交边界另行核实。
