# 项目持续开发与已采纳决策

更新日期：2026-07-17

本文是跨 Codex 会话的持久开发记忆。它记录用户明确同意的方向、当前实现状态和后续会话
必须遵守的决策流程；它不是聊天记录，也不能替代对代码、运行状态和测试结果的现场检查。

状态含义：

- **Accepted**：用户已经同意的产品或工程方向。
- **Implemented**：代码已经实现，但仍需结合测试项判断是否可靠。
- **Verified**：已有可重复的测试或端到端证据。
- **Pending**：方向已同意，但功能尚未实现。
- **Superseded**：已被后续明确决策替代，保留用于追溯。

## D-001：FWI 任务入口、参数确认与人工控制

- 决策状态：**Accepted**
- 实现状态：**Partially implemented；P1 Guided 人工审批闭环 Verified**
- 用户确认日期：2026-07-15

### 已采纳方向

1. 保留自然语言作为方便入口，但不能把不断增加近义词正则当作最终可靠方案。
2. 自然语言应先生成结构化 `FWIJobDraft`，至少包含模型、设备、preset、迭代数和关键
   数值参数；任何缺失或歧义都应显式呈现。
3. Web UI 的默认交互应展示参数确认卡片，由用户选择“批准运行”“修改参数”或“取消”。
4. 后续可以提供“全权交给 Agent”策略，但只能由用户显式启用，并继续经过白名单、参数
   上限、资源检查和审计记录，不能靠一句模糊文本永久授权。
5. 提供确定性的高级入口，例如：

   ```text
   /fwi model=marmousi_94_288 device=cuda iterations=50
   ```

   该入口仍必须进入同一个结构化校验和 MCP 提交流程，而不是拼接 shell 命令。
6. 为批量实验和复现保留“受验证配置文件 + Worker CLI”入口；配置文件是输入，唯一运行
   目录仍由 Worker 创建。
7. **不采用**“把任务文件手工放进 `/root/fwi-runs` 后由 watcher 自动执行”的设计。
   `FWI_RUN_ROOT` 是受控输出和状态目录，手工触发会带来半写文件、重复 job、路径信任、
   并发竞争和误执行风险。
8. **Legacy MCP renderer（对 Guided 已由第 9 条取代）**：旧直接执行路径只有收到合法
   `fwi_job_submitted` 和 `job_id` 才能显示已提交；只有文本、代码或绘图示例时必须明确
   显示“任务未提交”。
9. **P1 Guided 替代边界**：日常 Web 执行请求先生成 `TaskDraft`/`PlanGraph`，用户批准后
   以 SQLite `task_id`、dispatch intent/receipt 和 RunEvent 作为提交与运行事实；前端不暴露
   Worker `job_id`。这不改变固定 Adapter 与 Worker 内部幂等绑定。

### 当前已经实现并验证

- 固定白名单 `marmousi_94_288`、设备 `cpu|cuda`；D-006 将当前反演迭代整数范围扩展为
  `1–10000`，默认 smoke/demo 仍为 2/5 次。
- 自然语言中的标准表达及一组受限口语表达可确定性映射到 MCP；例如“做一下 marmousi
  的反演测试，迭代 50 次，完成后展示结果”。
- MCP 通过固定参数和安全进程启动器异步运行 Worker，不接受模型路径、shell 或
  `extra_args`。
- Web 可显示 `queued/running/succeeded/failed`、自动轮询状态、加载 manifest、指标和
  六张结果图。
- Web 在执行请求没有合法 job 回执时显示“FWI 任务未提交”，不会把生成代码当成执行。
- P1 Guided Web 从固定 Catalog 和严格九字段表单确定性组装 schema-valid `TaskDraft` 与单节点
  `PlanGraph`，展示真实 `task_id`/revision/`plan_hash`；用户可修改、批准或放弃 pre-runtime
  草稿，执行型快捷按钮和聊天文本不能再绕过确认卡直接提交。D-007 在原有
  七项输入上增加 `optimizer` 和可读十进制字符串 `learning_rate`，服务端将后者
  确定性转换为 Draft/Plan 中的整数 `learning_rate_milli`。
- 普通 Web 聊天在 HTTP/A2A 和 gRPC bridge 两条 transport 固定携带 legacy-submit opt-out；
  Orchestrator 在 actual tool plan 后、MCP 执行前拒绝 `fwi_submit_demo`。字段缺省继续兼容旧
  CLI/MCP/A2A 客户端；这是防 classifier 漂移的 loopback 产品策略，不是身份认证。
- 批准后复用 SQLite TaskService、固定 Adapter 和 one-shot dispatch；页面只用 GET 轮询真实
  状态。P1 Guided 原始 checkpoint 成功后展示并受控下载标准 NPY/CSV ArtifactManifest，且已
  通过真实 CUDA 和 Web 重启读取验证；D-008 当前八 artifact/六图扩展的最终验证单独记录，
  不反向改写这条历史证据。
- 2026-07-15 使用上述口语请求实际完成过一次 50 次 CUDA 端到端验证；运行目录和
  job ID 属于本机临时状态，不写入版本库。该验证只是当前小型合成 Marmousi 配置下的
  链路证据，不是普遍反演效果声明。

### 尚未实现

- Agent 从任意自然语言生成通用 `FWIJobDraft`、处理缺失/歧义并提出候选方案；P1 只有固定
  Guided composer，不冒充 P4 Planner。
- `/fwi` 确定性命令解析器。
- 用户可切换且可审计的“每次人工审批 / 本会话授权 Agent”执行策略。

在这些项目真正完成测试前，不得向用户声称 P1 固定 Guided 已是通用 Agent Planner、
确定性 `/fwi` 命令或全权代理模式。

## D-002：跨会话建议采纳和决策维护规则

- 决策状态：**Accepted**
- 实现状态：**Implemented / Verified**（通过根目录 `AGENTS.md`、本文和隔离测试）
- 用户确认日期：2026-07-15

### 已采纳规则

1. **本条的无条件完整读取已由 D-010 Superseded。** 新 Codex 会话先读取有界
   `docs/PROJECT_CURRENT_STATE.md`，再检查当前 Git/运行状态并按任务定向读取本文相关决定；
   阶段/范围变化、决策冲突、账本 reconciliation 和安全/发布审计仍需完整深读对应真源。
2. 未来如果发现新的、证据更可靠的架构或工作流建议，应先向用户说明：
   - 要解决的问题；
   - 证据和可靠性边界；
   - 收益；
   - 成本与风险；
   - 对现有接口、安全边界和数据的影响。
3. 只有用户明确同意后，才把建议加入本文并标记 **Accepted**；未经批准的建议只能留在
   当前讨论中，不能伪装成项目决定。
4. 落地时分别更新“实现状态”和“验证证据”。代码存在不等于已验证，测试通过也不代表
   科学结论可以外推。
5. 如果新决策替代旧决策，旧项标记 **Superseded**，写明替代项和日期，不删除历史依据。
6. 本文只记录持久、可执行的结论，不保存 API Key、`.env`、凭证、私有数据或无必要的
   原始对话内容。
7. 用户正常在仓库中打开 Codex 后可以直接提问，不需要记忆或手动执行项目启动脚本。
   根目录 `AGENTS.md` 负责自动触发首次任务的持续记录读取和实时状态检查。
8. 用户说“保留这个”“以后都这样”或“记录下来”时，表示相关内容需要持久化；默认并入最
   相关的既有 `D-*` 或进度账本，不代表用户授权新建一个编号决策。
9. 即使内容值得长期保存，Codex 也不得自行分配新的 `D-*` 编号（包括 Proposed）。只有先向
   用户展示拟新增决定的标题与范围，并获得“新增该编号决定”的明确批准后才能创建。Accepted
   计划的范围、阶段顺序、依赖、安全边界和出口变化同样必须再次获得用户明确同意。

### 当前实现与验证

- 根目录 `AGENTS.md` 是普通 Codex 新会话的自动入口；D-010 后它要求 Codex 先读取短入口，
  再按当前阶段/决定定向读取本文和完整计划/账本，检查 Git/diff，并验证相关代码、测试、
  服务和任务状态。用户无需执行脚本。
- `scripts/codex-project.sh --print-context` 降级为 Codex 可自行调用的内部只读诊断工具，
  不再是用户入口。它不可用时 Codex 必须直接执行等价检查，而不是要求用户代为运行。
- 内部摘要不落盘，不读取 `.env`、日志、私有 prompt 或 FWI status 的自由文本；敏感
  文件名会被过度过滤。
- 工作区本身由所有会话实时共享；启动摘要可能立即过期，因此 Codex 仍须在相关操作前
  重新检查文件、Git、测试、服务和任务状态。这里不使用后台 watcher。
- 隔离测试覆盖自动接续规则、固定 CLI 参数、shell 注入、敏感路径过滤、FWI 状态字段
  白名单和工作树不变性。具体机制见 `docs/CODEX_WORKFLOW.md`。

## D-003：双模式科研任务平台与持久任务内核

- 决策状态：**Accepted**
- 实现状态：**P0 + P1 最小持久垂直切片 Verified / P2.1–P2.9A 与 P2.9B1/B2
  有界切片 Verified / 完整 P2 Pending**
- 用户确认日期：2026-07-15
- 完整计划：`docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md`
- 进度账本：`docs/PROJECT_PROGRESS.md`

### 已采纳方向

1. 产品交互升级为 Guided/Agent 双模式：人工可以直接选数据、算法和参数，Agent 也可以
   从自然语言生成结构化任务草稿、建议和计划。
2. 两种模式共用唯一任务内核：
   `DatasetRef → TaskDraft → PlanGraph → ApprovalDecision → Durable Task Runtime → ArtifactManifest`。
3. 采用“动态规划控制面 + 确定性执行面”。Agent 负责理解、建议、拆解、评审和分析；
   Schema、Policy、Scheduler 和版本固定的 Algorithm Adapter 决定实际执行。
4. 默认在执行前由用户确认计划。会话级 Agent 授权必须显式、限域、有限预算、可撤销且
   可审计，不能把一句模糊文本解释为永久全权授权。
5. 长任务必须支持持久事件、幂等、等待、取消、有限重试、checkpoint 和重启 reconciliation。
6. 新算法通过标准 AlgorithmManifest、输入输出/参数 Schema、资源与安全声明、Adapter 和
   conformance test 接入；MCP/A2A 作为适配与互操作边界，而不是任务状态唯一真源。
7. 数据只通过已导入、哈希和 metadata 校验的 ID/版本引用，不接受 LLM 或浏览器传入的任意
   服务器路径。
8. Deepwave FWI 是第一个标准 Adapter 和回归基线；后续多算法能力首先表示多个真实算法可被
   独立发现、选择、校验和执行。自动把低频外拓、质量评价与 FWI 串成完整流程不是当前多算法
   接入的完成条件；只有用户明确选择工作流时才生成相应多节点计划。
9. P0 只定义覆盖最小 FWI 链路的可演进契约；最小 SQLite TaskService、幂等创建和
   状态查询提前到首个垂直切片，不在无持久任务身份时先做表面 Web 闭环。
10. SQLite/Task Store 是任务、计划、批准、状态和事件的唯一权威事实源；Redis 只用于
    缓存、锁、lease 和通知。
11. 可复现性必须记录算法/代码版本、commit/tree/dirty/diff 身份、镜像 digest 或环境
    lock、Python/CUDA/PyTorch/Deepwave、随机种子、硬件、deterministic flags、规范化配置和
    数据 hash；这是 provenance，不冒充跨 GPU/库版本 bitwise 一致保证。
12. Agent 置信度只用于解释/澄清；数据版本/hash/权限、算法注册/allowlist/版本 pin、
    I/O 类型、Schema、资源、DAG 无环、字段完整、有效批准/plan hash、side-effect policy 和幂等键
    等确定性检查才能打开执行门。
13. P4 Agent Planner/子 Agent 必须在 P0–P3 可靠性基线 Verified 后启动，不作为近期主线。
14. P2.8 wall-time timeout 采用三项明确语义：终态固定为 `Failed / WALL_TIME_EXCEEDED`，不得
    冒充用户 `Cancelled`；计时从 exact attempt 首条 SQLite durable `spawned + ready + running`
    observation 的可信 `observed_at` 开始，具体是 Store 对该 current Worker 的首条 durable
    observation；deadline 到来且 active term 持久化 authorization 前 Worker mutation 为零。到期后
    先给 cooperative grace，耗尽时由 exact Worker 自行退出，控制面绝不根据持久 PID 发送 signal。
    P2.8 当时只对能证明 v2 exact-stop capability 的 current 1.4/private 1.1 managed attempt 开放；
    timeout delivery authorization 与 durable user-cancel admission first-writer-wins。自然终态在
    authorization 前完成为 `not_triggered`，cancel admission 先赢为 `suppressed`，authorization/
    request 后自然终态先赢为 `superseded`，完整 request + ack + stopped heartbeat + idle fence
    proof 才是 `timed_out` 与 `Failed / WALL_TIME_EXCEEDED`。这样已经持久接受的用户取消不会因
    Supervisor 尚未投递而被后来到期的 timeout 重分类。D-012 后 current 1.5 attempt 1/2 已按
    private 1.1/1.2/1.3 复用同一语义；这只是消费者迁移，不改变 timeout 决策。
15. 完整 D-003 的固定阶段顺序是 P0 → P1 → P2 → P3 → P4 → P5 → P6；必须通过 P6 的评测、
    观测与安全加固出口才算整个项目完成。摘要或估算不得把 P5 写成项目终点。

D-003 是 D-001 的通用化，不替代 D-001。现有 FWI 白名单、参数边界、路径校验、MCP 安全
启动和“未提交不得伪装为执行”规则，在新任务内核通过等价或更强测试前继续有效。

### 当前实现边界

- 已验证：获批架构计划、跨会话进度账本、Git 管理规则、冷启动 reconciliation 协议，
  以及一次真实新会话中的 branch/diff/ancestor/ledger/live-test reconciliation 演练。
- P0 已实现并验证：只覆盖最小 Marmousi/Deepwave FWI 链路的七类 Draft-07 JSON Schema、
  版本化 extensions、规范化 plan hash、批准失效与确定性 Gate 参考实现、执行指纹、状态/API/
  Adapter/Proto 规范、威胁模型和旧合同差异审计；现场审计补齐 draft 状态及 plan/draft task
  type、参数、资源一致性 Gate；P1.1a 时合同测试增至 27 项，P1.1b 后为 28 项，当前为
  32 项。
- P0 合同和 Gate 参考实现不持久化、不调度、不提交 Worker，不能冒充 TaskService。
- P1.1a 已验证：文件型 SQLite WAL migration、唯一权威 task identity、不可变 draft/plan/
  approval/event、create 幂等、CAS 修订、project/principal/actor 限域、状态/event 原子事务、
  专用 application/schema/integrity 检查、重启读取和关系损坏 fail-closed；33 项聚焦测试通过。
  该基础没有 submit/Worker 副作用。
- P1.1b 已验证：SQLite v2 连续/原子 migration、不可变 Dataset Catalog/Algorithm Registry、
  project/principal/permission 限域读取、批准任务预算持久行、服务端 registry snapshot 解析、
  Marmousi sidecar/hash 到无路径 DatasetRef 的固定映射和 Deepwave manifest；22 项 Registry、
  33 项 TaskService 与 28 项合同测试通过。`Queued` 仍关闭。
- P1.2a 已验证：固定 `deepwave.acoustic_fwi@1.0.0` 六方法 Adapter，只支持单节点
  `acoustic_fwi_2d` 反演；首次执行绑定服务端 Registry snapshot 和本地物理 identity，使用固定
  venv/argv、跨线程/实例/进程幂等、私有 openat/no-follow 状态、脱敏 status、P1 unsupported
  cancel 与严格 NPY/CSV/metrics ArtifactManifest 收集。Adapter 17 项、Scientific Runtime 组合
  100 项和真实 CUDA 一次迭代 submit/status/collect/replay 通过；provenance 仍明确为 development。
- P1.1c 已验证：SQLite v3 在同一 `BEGIN IMMEDIATE` 重读 current aggregate/Registry/budget，
  执行完整 Gate 和固定单 FWI capability guard，原子消费预算并写 submit idempotency、durable
  intent、首个 `task_queued` 与 `Queued`；commit 后才通过固定 dispatcher 调用 Adapter。preflight
  与实际 fingerprint 分层，one-shot claim/receipt 显式保留 pending/dispatching/reconciliation
  crash 状态，精确 replay 不自动重发。Scientific Runtime 117 项与全量回归通过。
- P1 Guided Web 已验证：SQLite v4 为 revise/plan/approval/abandon 增加不可变 mutation ledger
  和 pre-runtime abandonment；固定 Guided composer、同源 Workbench API、CSRF/Host/Origin/
  JSON 边界、普通 Web legacy-submit opt-out、确认/修改/批准/放弃卡、Adapter status→RunEvent
  单调桥和 task-scoped NPY/CSV 下载已接入根启动器。139 项 Runtime 自动化、真实一迭代
  CUDA、artifact 字节 hash/脱敏、source-policy 绕过请求和 Web 重启后同一 task/event 查询均
  通过；详细边界见
  `docs/architecture/SCIENTIFIC_RUNTIME_P1_GUIDED_WEB.md`。
- D-006/P1-006 已实现并验证：Guided/MCP/Worker 参数上限为 10000；该 checkpoint
  使用 `deepwave.acoustic_fwi@1.1.0` 与 Adapter `1.1.0`，旧 `1.0.0` Registry snapshot 保持
  不可变并可继续读取已有 dispatched task。Runtime 143/143 与既有数据库/HTTP 兼容
  验收通过；默认次数与人工批准不变。D-007 的 Algorithm/Adapter `1.2.0` 是不可变
  六参数历史快照，`1.3.0` 是 D-007 已验证 checkpoint；D-008 当前新提交推进至
  `1.4.0`，并保留 `1.0.0`–`1.3.0` 任务、计划和收据的严格读兼容。
- D-007/P1-007 优化器控件、证据分级建议卡和轮询滚动保持已验证；P2-001
  scope-bound SQLite 任务分页发现、左栏索引与关闭后重开也已验证。自动化、真实
  loopback HTTP、服务重启后的旧任务读取、CUDA/SGD 两步任务以及页面重载后发现/重开
  均通过；这只验证本有界切片，不代表完整 P2。
- D-008/P1-008/P2-002 已验证：浏览器对话与 SQLite 任务分离并用可选引用关联；本地删除对话
  不级联任务；SQLite v6 为 resolved terminal task 提供可恢复、append-only、CAS/idempotent
  的 Trash/Restore；当前 Algorithm/Adapter `1.4.0` 精确声明两个数值 artifact 与六张固定
  PNG，历史 `1.0.0`–`1.3.0` 仍精确读取各自两个标准 artifact。完整自动化与 fresh 私有 v6
  SQLite 上的真实 CUDA 六图、Trash/Restore、拒绝边界和同库重启复验均通过。
- D-009/P2-003 有界本地结果永久删除已验证：SQLite v7 两阶段 purge tombstone/outcome、
  pending 禁止 Restore/状态/artifact、终态 receipt-bound Adapter 清理和 Web 强确认已闭环；
  完成后任务不再出现在 active/trash 列表，对话与失效引用继续保留。Scientific Runtime
  180/180、Web 29/29 与 Node UI 通过；按用户要求只使用临时目录/Fake Dispatcher/轻量 HTTP，
  没有重复运行真实 FWI/CUDA 实验。
- P2-004 有界启动 receipt 收养/状态追赶已验证：Workbench 先成功 bind 但不 listen，再对最多
  10000 个 active task 做 project/principal scope-bound 全分页扫描。pending 不 claim/首次派发；
  dispatching 只用 intent 推导的单一 Adapter control record 做只读 lookup，仅 current 1.4
  `launched` exact receipt 可写唯一 outcome。missing/preparing/launching/failed/corrupt/历史
  Algorithm/Adapter identity 1.0–1.3
  均保持 deferred；lookup 不调用 launcher、不做 readiness probe、不扫描 run root。已有
  `reconciliation_required` 不重试；dispatched task 仅追赶一次 status/RunEvent。成功后才
  activate/publish/serve，忙端口零 recovery；成功 recovery 的 summary 只含计数和稳定 code。
  单任务 event high-water 扫描硬上限为 100000。TaskService 80/80、Adapter 27/27、Scientific
  Runtime 201/201、Web 36/36 与其余回归通过；未运行真实 FWI/CUDA。
  这是 P2-004 当时的生命周期证据；P2-006 已将 lease 前 pass 改为只读 inventory，并把 current
  1.4 legacy private schema 1.0 exact receipt adoption 移到 active term 内。
- P2-005A 控制面 fenced lease/持续状态泵已验证：SQLite v8 为 project/principal scope 保存
  连续 fence term、当前 lease、append-only closure 与受 active term 约束的 RunEvent commit
  audit；租约时间在 SQLite 写事务内采样，expiry/takeover、ABA、时钟回退和迟到写均 fail closed。
  Workbench 在 startup recovery 后、listen/publish 前取得 lease 并启动非 daemon Supervisor；只为
  durable outcome 精确为 `dispatched` 的 Queued/Running task 持续调用 status bridge。pending、
  dispatching、missing、`reconciliation_required` 均 deferred，模块没有 launcher/dispatch 能力，
  lease 丢失只使控制面自我隔离，不会把 Worker 标为失败、重启 Worker 或释放 Worker 容量。
  Scientific Runtime 226/226、
  Worker 28/28、Web 45/45 与其余回归通过；未运行真实 FWI/CUDA。
- P2-005B 固定 Adapter 托管 Worker staged launch fence 已验证：current 1.4 私有 control
  schema 1.1 绑定唯一 attempt；stable per-submission 与 capacity-slot `flock` 经 `pass_fds` 跨
  exec 由子进程保持；轻量 bootstrap 在数值 import 前验证 fence、启动 heartbeat 并写 exact
  ready。Popen 后未知结果保持 `launching`，startup 只收养同 attempt receipt；capacity 满不写
  immutable dispatch outcome。purge 全程持有空闲 execution fence，legacy CLI 与 Web 均不能
  读取/进入托管 private sidecar。Runtime 234/234 + launch-control 8/8、Worker 29/29、Web 46/46
  与完整回归通过；未运行数值 FWI/CUDA。
- P2-005C fenced Worker 证据投影/late adoption 已验证：SQLite v9 保存 current managed
  Adapter 的 immutable attempt、每次 Supervisor 实际 sample 的 append-only ready/heartbeat
  evidence 和唯一 active-term adoption；latest-only replay、heartbeat high-water/terminal、
  canonical JSON 与关系列一致性及 stale term 均 fail closed。Adapter/Dispatcher 只按 intent 读取
  已有 exact attempt，零 launcher/scan/TTL takeover；Supervisor 对 dispatching 每轮观察，对
  dispatched evidence 使用独立 60 秒 cadence且不减慢 status refresh。TaskService 82/82、Adapter +
  launch-control 44/44、Scientific Runtime 241/241 及非数值全量回归通过；未运行 Deepwave 数值
  FWI/CUDA。kernel `flock` 仍是执行/容量权威，v9 evidence 不是 Worker lease 或完整历史 backfill。
- P2-006 可恢复 fenced scheduler/受监督首次派发已验证：HTTP submit 只做 prepare/Gate 与
  `Queued/pending` 原子 admission；SQLite v10 append-only authorization 只允许 active term 执行
  pending 首派、dispatching/no-record 接管或 `staged_attempt_resume`。Adapter 在 submission lock
  内复核并复用同一 attempt/job；`preparing` 或尚未取得 launch lease 的 `launching` exact staged
  attempt 可恢复，leased/spawned/ready 不二次 `Popen`，capacity/lock busy deferred。current 1.4
  legacy private schema 1.0 exact launched receipt 通过只读 proof 与独立 active-term audit 收养；历史
  Algorithm/Adapter identity 1.0–1.3 仍 deferred。startup pass 已改为 lease 前纯只读 inventory，
  active Supervisor 才派发/adopt/status；kernel `flock` 仍是执行/容量权威，heartbeat 不授权替换。
  Scientific Runtime 251/251、固定 venv Worker 非数值 + launch-control 37/37、Web 46/46、
  Embedding 6/6、CTest 39/39、MCP 1/1、Node UI 与治理回归通过；未运行数值 FWI/CUDA。
- P2-007 exact-attempt user cancellation 已验证：原 checkpoint 的 SQLite v11 request/authorization/
  outcome 把请求绑定到 Algorithm/Adapter 1.4、private schema 1.1、dispatched
  intent、最新 spawned+ready+running observation 和 Worker-published capability。HTTP 只持久化
  admission；active Supervisor term 才交付 append-only request。Worker 自行 ack/unwind 或
  `os._exit(75)`，不使用持久 PID kill；只有 ack + stopped heartbeat + idle execution `flock` 才提交
  Cancelled，自然 Succeeded/Failed 抢先则 cancellation 为 superseded。Guided Web 区分
  requested/cancelled/superseded，未知响应保留原 Idempotency-Key，关闭视图/放弃/回收均不冒充取消。
  D-012 后 current 1.5 的 attempt 1/private 1.1 与 attempt 2/private 1.2/1.3 已接入同一 exact cancel 边界。
- P2-008 exact-attempt wall-time timeout 已验证：原 checkpoint 的 SQLite v12 immutable window、
  active-term authorization 与 outcome 覆盖 1.4/private 1.1、durable dispatched、latest managed
  attempt 和 v2 exact-stop capability。clock 精确使用 Store 对该 current Worker 的首条 durable
  spawned+ready+running observation 的 `observed_at`，不使用 submit/PID/heartbeat freshness；
  deadline 到来且持久化 authorization 前 Worker mutation 为零。自然终态在 authorization 前完成为
  `not_triggered`；durable user-cancel admission 先赢为 `suppressed`；authorization/request 后自然
  终态先赢为 `superseded`；只有 request + ack + stopped heartbeat + idle execution fence 全部成立
  才是 `timed_out` 并提交 `Failed / WALL_TIME_EXCEEDED`。grace 后 exact Worker 自行退出，控制面
  不 signal 持久 PID。Workbench 只读 timeout 投影精确包含 `state`、`wall_time_seconds`、
  `started_at`、`deadline_at`、`resolved_at`、`failure_code`、`terminal_status` 七个字段，不暴露
  ID/hash/PID/path；`POST /timeout` mutation 不存在。Scientific Runtime 304/304、固定 venv Worker
  32/32（CPU smoke 3/3）、launch-control 25/25、Web 46/46、Embedding 6/6、CTest 39/39、MCP 1/1、
  Node/governance 及真实固定 venv CPU Deepwave timeout E2E 均 PASS；未运行 CUDA。D-012 后
  current 1.5 attempt 1/private 1.1 与 attempt 2/private 1.2/1.3 已复用该 exact timeout 边界。
- P2-009A positive receipt resolution/adoption 与 P2-009B1/B2 finite retry 均已 Verified；B1/B2
  最多两个 attempt、只读公开投影和禁止 attempt 3 的具体边界见 D-012 与进度账本。
- 尚未实现：负向/不确定 `reconciliation_required` 完整矩阵、SSE 和完整自动
  reconciliation。不完整 staging 继续 fail-closed；浏览器 GET 仍可走无 Supervisor lease 的既有
  单调 CAS，因此不能声称所有 runtime write 都由唯一控制面独占；P2-006 也不覆盖 standalone
  CLI/C++ MCP capacity，不按 heartbeat 新鲜度接管。P2-007/P2-008 不支持 pending/staged、legacy
  private schema 1.0 或公共 Adapter 1.0–1.3；`resources.wall_time_seconds` 的 runtime enforcement
  只在上述 P2-008 exact 边界成立。P3 DAG、
  Agent Planner、通用 Algorithm SDK 和去噪链路也仍 Pending。服务器
  transcript 永久删除、SQLite 审计历史硬删除以及备份/外部副本清除仍未实现；P2-001–P2-009B
  的有界 Verified 切片仍不得被报告为完整 P2 已实现。
- 不得因本文存在就声称下一代任务平台已经可用；每项真实状态见进度账本并现场验证。

## D-004：Git checkpoint 管理

- 决策状态：**Accepted**
- 实现状态：**Verified — first SSH checkpoint pushed**
- 用户确认日期：2026-07-15
- 完整规则：`docs/GIT_AND_PROMPT_POLICY.md`

### 已采纳规则

1. D-003 从 FWI 已验证基线创建独立 `feature/scientific-agent-runtime` 分支，按可验证
   纵向切片提交并通过 SSH 推送，不直接修改或推送 `main`。
2. 提交前检查相关 diff、测试、进度账本、暂存清单和敏感信息；不提交构建、环境、模型、
   数据、运行状态、数据库、日志或缓存。

验证证据：首个受审 checkpoint `b5ac633` 已通过 SSH 推送到
`origin/feature/scientific-agent-runtime`，推送后本地 `HEAD` 与 upstream 一致。

## D-005：AI 提示词分类管理

- 决策状态：**Proposed / awaiting user confirmation**
- 实现状态：**Pending**
- 提案日期：2026-07-15
- 完整提案：`docs/GIT_AND_PROMPT_POLICY.md`

### 待确认提案

1. 临时开发 prompt、原始聊天和窗口交接文本不上传，使用专用 Git 忽略位置；
2. 决定产品行为的脱敏运行时 prompt 作为可复现源码版本化、评审和测试；
3. 不用“先上传、最后再删”保护隐私，因为删除最新文件不会清除 Git 历史；
4. 旧 prompt-like 文档不破坏性批量删除；D-005 获批后再在 P0 审计。

当前安全默认：在 D-005 获批前，本分支不上传临时 prompt/原始聊天，也不迁移或删除
已有产品运行时 prompt。这是本次 checkpoint 的保守处理，不等于用户已批准全部提案。

## D-006：Marmousi FWI 高迭代上限

- 决策状态：**Accepted**
- 实现状态：**Implemented / Verified**
- 用户确认日期：2026-07-15

### 已采纳方向

1. 固定 `marmousi_94_288` 的反演入口允许显式指定 `1–10000` 次整数迭代，供少数长次数
   验证使用；`10001`、小数、负数、布尔值和服务端 API 字符串继续拒绝。
2. `fwi_smoke`/`fwi_demo` 默认仍为 2/5 次，不把新上限变成推荐默认值；所有 Guided 任务仍需
   生成确认卡并由用户批准。
3. 参数接受集合用新的 Algorithm/Adapter minor version `1.1.0` 固定，不原地修改已注册的
   `deepwave.acoustic_fwi@1.0.0` / Adapter `1.0.0` 身份。旧 manifest 上限仍为 100；已有
   dispatched task 的状态和 artifact 读取保持兼容。
4. 页面明确提示超过 100 次可能长时间占用计算资源。10000 不构成完成时间保证，也不扩展
   P1 权限或可靠性范围；运行中 cancel、timeout、checkpoint、retry 和 reconciliation 仍属 P2。

### 当前实现与验证

- Contract、Registry、Workbench、Adapter、Worker、MCP、C++ planner 和 Web UI 已统一到新
  边界；通用 TaskDraft envelope 最大 10000，旧 AlgorithmManifest 仍用自己的 max=100 拒绝
  高值。
- 已通过聚焦边界测试：10000 可生成无执行副作用的 Draft/Plan 或通过配置验证；10001、
  小数、负数、布尔值被拒绝；旧/新 manifest 可在 SQLite Registry 共存且旧快照不变。
- Scientific Runtime 143/143、根 CTest 39/39、MCP 1/1、FWI 27/27、Web 27/27、Embedding
  6/6 和 UI/治理检查通过。既有数据库实际启动后可同时读取 1.0/1.1 Registry；两个旧 dispatched
  成功任务的状态与 artifact 均可读取。HTTP 10000 只生成确认卡后被放弃，没有 dispatch/job；
  10001 返回 422 且不创建 task/dispatch/job。
- D-006 checkpoint 的读取兼容强制 Algorithm/Adapter/fingerprint 只能是 `1.0↔1.0` 或
  `1.1↔1.1`，当时新 dispatch 仅允许 `1.1.0`。D-007 的 `1.2↔1.2` 六参数
  快照保持不可变并继续严格读兼容，D-007 checkpoint 使用严格的 `1.3↔1.3`；D-008
  当前新 dispatch 使用严格的 `1.4↔1.4`，同时 `1.0↔1.0`–`1.3↔1.3` 保持读兼容。
  MCP runner 合同不因 Web 优化器控件或图片展示而获得任意参数、路径或 shell 能力。
- 不执行真实 10000 次回归，以免无必要地长期占用 GPU/CPU。Worker 每轮会原子重写并
  `fsync` 累计 `loss.csv`，高次数存在 O(N²) 写放大；7200 秒资源值不是 runtime timeout，
  P1 仍无运行中取消、timeout、checkpoint、retry 或 reconciliation。

## D-007：FWI 优化器可见性、非干扰轮询与持久任务找回

- 决策状态：**Accepted**
- 实现状态：**Verified；完整 P2 仍 Pending**
- 用户确认日期：2026-07-15

### 已采纳方向

1. FWI 确认卡必须显式展示并允许选择优化器和学习率，不能把它们隐藏在
   Worker 默认值中。当前白名单为 `adam|sgd`；Web/API 学习率是最多三位小数的
   十进制字符串，公共 Draft/Plan 使用 `learning_rate_milli = learning_rate * 1000` 的整数
   定点表示，保持 canonical plan hash 的无浮点边界。
2. 建议卡必须显式分级证据：Adam LR=10 是固定 Marmousi 已验证基线；Adam LR=2
   只是微型 CPU finite-update 的保守起点；SGD LR=10000000 已通过固定 Marmousi CUDA
   两步 finite/model-update 校准，但仍是实验性起点，不称为长程收敛推荐。
3. `gradient_clip_quantile=0.98` 作为当前 Algorithm/Adapter 版本固定值明示，本轮不
   提供无证据的任意调整。Adam 学习率范围为 `0.1..100`，SGD 为
   `100000..1000000000`；越界、非纯十进制或精度过高必须在创建任务前拒绝。
4. 任务状态轮询不得在每次迭代重绘时强制把阅读位置拉到页面底部。用户已在
   底部时可继续跟随；用户已向上阅读时保留原 `scrollTop`；显式打开/重开任务时
   可一次性展示目标卡。
5. 左栏任务必须来自 SQLite Task Store 的 scope-bound 分页发现 API，不从聊天文本、
   `localStorage` 或 `FWI_RUN_ROOT` 扫描猜测任务。关闭卡片只关闭视图；任务仍在左栏，
   页面重载后可重新发现并按 `task_id` 打开。
6. 本次 P2-001 只承诺发现与重开。列表 GET 不刷新 Adapter；重开已批准但 submit
   结果未确认的任务时，如果页面已没有原 Idempotency-Key，必须 fail closed 为只读审计，
   不生成新 key 重发。这不实现 cancel/retry/lease/reconciliation/SSE。

### D-007 checkpoint 的实现与验证边界

- D-007 已实现 Algorithm/Adapter `1.3.0`、合同 minor `1.1.0` 的六参数 Draft/Plan、推荐卡、
  受校验表单、滚动保持、SQLite v5 scope index、有界 cursor 列表、左栏和单任务重开。
  `1.2.0` 保留为不可变六参数历史快照，只用于严格读取既有任务和收据。
- D-007 的 `1.3.0` manifest 只声明 `acoustic_fwi_2d` 与 `fwi_smoke|fwi_demo`，iterations
  是 `1..10000`、seed 是 `0..2147483647`，Adam/SGD 的
  `learning_rate_milli` 使用各自条件边界；旧 Worker/MCP `forward` 没有进入当前标准
  Algorithm/Adapter capability。`/v1` create/revise 为既有
  loopback 客户端保留精确历史七个 form 字段（revise 另要求 `expected_revision`）。服务端
  先以不可变 1.0/1.1 composer 重建候选并精确匹配 scope/operation/key/request hash；只有
  未命中历史 durable record 才确定性补为 Adam/LR 10 后按当时的 `1.3.0` 校验和建模。同 key
  不同 payload 仍冲突；当前浏览器发送完整九个 form
  字段，缺一项或只提供一半优化器字段均拒绝。该兼容层不允许用旧
  `1.0.0`/`1.1.0`/`1.2.0` 发起新 dispatch。
- 旧 `1.0.0`/`1.1.0` Algorithm/Adapter 与四参数 `schema_version=1.0.0` Draft/Plan，
  以及 `1.2.0` Algorithm/Adapter 与六参数 `schema_version=1.1.0` Draft/Plan 都不就地
  改写；当前读取端必须继续验证它们各自的严格版本绑定。
- D-007 自动化证据：Scientific Runtime 控制面 157/157、固定 venv Worker 27/27、Web
  Python 29/29、Node UI 行为和 `git diff --check` 全部 PASS。真实 HTTP 验证旧 500 次
  CPU/Algorithm 1.1 任务可从持久列表重开为 Succeeded 并读取两个 artifact；当时的 1.3
  CUDA/SGD/LR=10000000 两步任务也实际 Succeeded，artifact 字节/hash、NaN/Inf=0、非零
  模型更新和约 713.3 MiB GPU 峰值均通过。页面重载后的新 session/list 能再次发现并重开
  该任务，因此 D-007/P1-007/P2-001 有界切片为 **Verified**。

## D-008：对话—任务分离、可恢复删除与标准结果画廊

- 决策状态：**Accepted**
- 实现状态：**Implemented / Verified；完整 P2 仍 Pending**
- 用户确认日期：2026-07-15

### 已采纳方向

1. `Conversation`、`ScientificTask` 与 `ConversationTaskLink` 是三个独立对象。对话保存
   交流上下文，可以不创建任务；任务由 SQLite Task Store 独立保存状态、审批、事件与
   artifact；link 只是可选引用，不是任务所有权。一个对话可引用多个任务，任务也可在
   其他对话中再次引用。
2. 执行型自然语言仍先成为对话消息，但只打开独立任务草稿；页面必须明确说明尚未创建、
   尚未运行。只有生成 Draft/Plan 后才建立任务身份，只有显式批准后才进入运行。快捷运行
   可创建不关联任何对话的独立任务。
3. 对话内可以显示所引用任务的受信状态、进度和结果入口，但 SQLite/API 始终是任务事实源；
   `localStorage` 只持久化 task identity/link，不得凭缓存状态宣称任务成功。切换或删除对话
   不取消、不隐藏也不删除任务。
4. 保留对话删除能力并增加明确确认。本轮只安全删除当前浏览器中的对话副本；服务器 transcript
   在没有 conversation ownership/deletion capability 之前仍按既有 TTL 管理，页面不得声称
   已从服务器永久清除。
5. **本条的“无永久删除”边界已由 D-009 部分 Superseded。** 用户“删除任务”采用可恢复 Trash/Restore，而不物理删除不可变 Draft、Plan、Approval、
   RunEvent、幂等记录或 artifact。只有 `Succeeded|Failed|Cancelled` 终态可移入回收站；
   `Queued|Running|Waiting|Retrying`、approved-submit-pending、outcome unknown 和
   reconciliation 状态必须拒绝。待批准草稿要先通过既有 abandon 闭环为 `Cancelled`。
   对话删除和任务回收站操作绝不级联。
6. **本条的 Pending 状态已由 D-009 Superseded。** 永久 artifact purge 是后续独立 retention 能力，必须先有 tombstone、运行中拒绝、崩溃恢复
   和路径安全设计；本轮 Trash 后仍可审计、读取结果并恢复，不把“移入回收站”冒充物理清除。
7. 恢复用户偏好的六张 FWI 图片，但不接回暴露 Worker `job_id`/路径的 legacy URL。新
   Algorithm/Adapter minor version声明两个数值输出和六个固定 PNG 输出；Adapter 从固定
   allowlist 路径 no-follow 读取、完整解码并重算 size/hash，浏览器只通过 scope-bound
   task artifact endpoint 获取。历史 Algorithm/Adapter 及其既有两个标准 artifact 保持不可变。
8. 图片异步加载和任务轮询都必须保持阅读位置；关闭、切换或回收任务时释放浏览器 Blob URL，
   单图失败不能隐藏其余结果。

### 当前实现边界

- 浏览器聊天存储已升级为 schema v3；每个对话只持久化可选的 `taskId/linkedAt` 引用，
  任务状态与结果就绪信息仅作同会话显示缓存并从受 scope 约束的 API 刷新。执行型消息先留在
  对话中并明确“尚未创建/尚未运行”，创建 Draft/Plan 后才产生独立任务；快捷任务默认可不
  关联对话。对话删除有确认和本地持久化失败回滚，且不触碰 SQLite 任务。
- SQLite v6 已增加不可变 visibility event/mutation ledger 与 active/trash projection；列表
  cursor 绑定视图，Trash/Restore 绑定 scope、expected revision 和 idempotency key。只有具有
  pre-runtime abandonment 证据且无 dispatch intent 的 `Cancelled`，或具有 dispatched outcome
  证据的 `Succeeded|Failed|Cancelled` 才能移入回收站；详情、事件和 artifact 在回收站中仍
  可审计读取，restore 不重新运行任务。
- 当前 Algorithm/Adapter `deepwave.acoustic_fwi@1.4.0`/`1.4.0` 由持久 Plan 精确校验八个输出：
  NPY 反演模型、CSV loss，以及 true/initial/inverted model、model error、shot gathers、loss
  curve 六张固定尺寸 PNG。Adapter 对固定 allowlist 文件执行 openat/no-follow、regular-file、
  上限、PNG 签名/完整解码/模式/尺寸和重算 size/hash 校验；Web 通过 task-scoped endpoint
  顺序载入 Blob 图片、保持滚动位置并在关闭/切换时释放 URL。历史 `1.0.0`–`1.3.0` 的
  persisted Plan 继续严格要求两个既有标准 artifact，不被新画廊反向改写。
- 自动化验证通过：Scientific Runtime 165/165（新增 `1.2.0`/`1.3.0` optimizer-aware
  create/revise 响应丢失后的 exact replay 覆盖）、固定 venv FWI Worker 28/28、Web 29/29、
  Embedding 6/6、根 CTest 39/39、MCP 1/1，以及 Node UI、launcher、continuity、
  runtime-secret、`codex-project --check`、shell syntax 和 `git diff --check`。
- fresh 私有 v6 SQLite 的真实 CUDA E2E 使用 Algorithm/Adapter `1.4.0`、Adam、LR=10、
  2 iterations，在 NVIDIA GeForce RTX 4070 Laptop GPU 上完成
  `Queued → Running → Succeeded`：10 个连续事件、8 个 artifact/6 张 PNG，Worker elapsed
  5.42712 s；loss 从 0.0340023860 降至 0.0232094708，模型相对 L2 更新 0.0037406076，
  NaN/Inf=0。manifest collect 为 0.1509 s；六张图片 GET 合计 0.7527 s，全部八项 GET
  合计 1.0156 s，v6 migration checksum 与当前源码一致。
- 同一真实任务的 active/trash 列表切换、trash 后 artifact 读取、visibility revision
  `0 → 1 → 2`、Restore 不重跑均通过；`AwaitingApproval` 直接 Trash 返回 409，先 abandon
  为 `Cancelled` 后可移入回收站。服务使用同一数据库重启后，task/event/artifact 三类
  fingerprint 及 8 个文件的 bytes/hash 均保持不变；验收结束后相关服务已清理，未持久记录
  临时 PID 或任务标识。
- 已知低风险边界：图片 GET 为保持严格安全复核，会在每次请求完整 collect 并解码固定图片；
  当前六图现场合计 0.7527 s。单任务 visibility 历史读取随 Trash/Restore 事件数线性增长；
  这两项是后续性能加固债务，不影响本次有界功能验收。
- 本条目不实现运行中 cancel、lease/heartbeat、task retry、自动 reconciliation、SSE、服务器
  transcript 永久删除或 artifact 永久 purge；这些仍属于后续 P2/P6 能力。

## D-009：任务回收站永久删除本地结果

- 决策状态：**Accepted**
- 实现状态：**Verified（有界 P2-003）；完整 P2 仍 Pending**
- 用户确认日期：2026-07-15

### 已采纳方向

1. 回收站中的任务增加“永久删除”。该操作只接受已经安全进入 Trash 的 resolved terminal
   task，必须再次绑定 scope、visibility revision、幂等键与受控 dispatch receipt；浏览器不得
   传入路径、Worker job ID 或 run root。
2. 永久删除先在 SQLite 持久化不可恢复 purge request/tombstone，再删除该任务专属的本地
   Worker 运行目录及其中 config、日志、状态、NPY、CSV、PNG，最后记录 purge outcome。
   SQLite 继续保留既有不可变任务审计历史，并只新增防重放所需的最小 purge tombstone；
   不把文件系统删除冒充数据库硬删，也不再通过任务用户界面暴露已 purge 的 aggregate。
3. purge request 一旦持久化就禁止 Restore、状态刷新和 artifact 读取。文件删除或最终 outcome
   写入中断时，重试只能继续同一个 purge，不能恢复任务或重新运行 Worker；完成后任务从
   active/trash 列表中消失。对话和消息继续保留，已有 task 引用显示“任务已永久删除”，用户
   可单独移除该失效引用；永久删除任务不得级联删除对话。
4. Adapter 只从已验证 handle/私有 submission receipt 推导固定 job 目录，在 submission lock 下
   确认 Worker 为 succeeded/failed，使用 FD-relative、no-follow 删除且不越过 run root。Queued、
   Running、结果不确定、receipt 损坏、目录缺失但无可信 purge 状态全部 fail closed。
5. Adapter 的极小 control receipt/lock 与 SQLite tombstone可以保留，用于阻止同一 submission
   被重新运行；页面确认文案必须准确说明“本地运行目录与结果文件不可恢复”，不声称抹除了
   Git、备份、外部副本或全部审计元数据。
6. 本切片只实现有界 P2-003 本地任务结果 purge，不实现服务器对话 transcript 永久删除、运行中
   cancel、lease/heartbeat、retry、自动 reconciliation 或 SSE。按用户要求使用临时目录、Fake
   Dispatcher 和轻量 HTTP 自动化验收，不重复运行耗时 FWI/CUDA 实验。

### 验证证据

- SQLite v6→v7 原位升级、append-only purge request/idempotency/outcome、scope/CAS、pending
  恢复继续、旧 Restore replay 阻断和 completed 列表隐藏均通过。
- Adapter 临时目录测试覆盖 succeeded/failed、queued/running 拒绝、符号链接、并发锁、
  delete-before-outcome 崩溃恢复和严格 receipt 绑定；本地 job 目录消失而 control receipt/lock
  保留。
- Scientific Runtime 180/180、Web Python 29/29、Node UI 与治理检查通过；未启动真实 Worker、
  CUDA 或耗时 FWI 重复实验。

## D-010：D-003 低 token 自动接续与 CodeGraph 导航

- 决策状态：**Accepted**
- 实现状态：**Implemented / Verified**
- 用户确认日期：2026-07-16

### 已采纳方向

1. 新会话不再无条件完整读取持续决策、完整计划和完整历史账本。根 `AGENTS.md` 先读取
   不超过 160 行的 `docs/PROJECT_CURRENT_STATE.md`，现场检查 Git，再按当前阶段、切片和
   相关 `D-*` 决策定向深读。
2. 完整长文档仍是权威真源，不删除或压缩历史；阶段/范围变化、决策冲突或替代、账本与现场
   不一致、安全/发布审计以及维护持续记录本身时，必须完整读取相应真源。
3. 架构、符号、调用流和修改影响优先使用一次有界 CodeGraph 查询。刚修改、pending-sync、
   未索引的 Markdown/Shell/SQL 直接读取；Git/diff、测试、运行状态和安全证据继续使用真实命令。
4. CodeGraph 不可用时自动回退到 `rg`/定向读取，不阻塞 D-003，不要求用户记忆初始化或启动
   命令。不得为同一问题无理由重复 CodeGraph、宽泛搜索和整文件读取。
5. 开发中先跑聚焦测试并限制输出，形成 Verified checkpoint 时仍运行既定完整回归；该优化
   只减少重复上下文和日志，不降低 D-003 阶段出口、安全边界或科学验收标准。
6. `PROJECT_CURRENT_STATE.md` 只保存当前路由和不可破坏边界；阶段、下一动作、Accepted 决策
   或安全边界变化时与完整真源同 checkpoint 更新。现场证据冲突时现场优先并修正摘要。

### 当前实现与验证

- `AGENTS.md` bootstrap v2、短入口、内部 helper、工作流文档和隔离测试已接入；普通
  “开始 D-003”/“继续 D-003”默认固定读取为 120 行 AGENTS + 81 行短入口，再补当前小节。
  本 checkpoint 四份长文档合计 1635 行，因此减少的是固定重复读取量，不声称精确 token
  比例；后续文档增长时不把该现场计数当成永久常量。
- CodeGraph 当前索引可用，并能从正在开发的 P2-005A 代码直接定位 `RuntimeSupervisor`、
  `RuntimeSupervisorTaskService`、lease 类型及测试符号；它仍不替代编译、测试或运行证据。
- `test_codex_project_launcher.sh`、`test_project_continuity_contract.sh`、helper `--check`、Shell
  语法和定向 diff check 通过；短入口测试强制 160 行上限并拒绝恢复无条件长文档读取。
- 已经打开的 Codex 会话不会可靠地重新构建 `AGENTS.md` 指令链。完成本 checkpoint 后新开
  一次会话即可获得新规则；之后每次正常新会话会自动加载，不需要项目专用启动脚本。

## D-011：D-003 弹性中等切片、多算法边界与分级测试

- 决策状态：**Accepted**
- 实现状态：**Planning/governance Implemented / Verified；运行时阶段不变**
- 用户确认日期：2026-07-16

### 已采纳方向

1. 保留 D-003 的阶段顺序、可靠性、安全边界和出口质量，继续按可审阅、可验证的切片逐步
   开发；减少的是不必要的切片数量，不是测试覆盖或功能质量。
2. 切片数量只是现场规划基线，不是固定配额。相邻工作共享同一状态机、接口、风险边界和出口
   测试时应合并；若出现明确的并发/崩溃窗口、文件所有权冲突、独立安全边界或无法共同验证的
   风险，必须先向用户说明证据并获得明确批准，随后才能增加切片并在进度账本记录依据。
3. 不为单个 migration、Schema 字段、receipt/sidecar 变化或一项测试单独创建产品路线切片；
   也不为追求较小数字把不可独立验证的大量高风险工作强塞进一个提交。`0cbe131` 的历史
   “约十余个”（账本按约 12 记粗估锚点）覆盖当时完整 P2 余项与 P3–P6，不是当前余量或配额。
4. 多算法接入表示用户在 Catalog 中看到多个兼容的独立算法选项，可上传/选择受控数据、描述
   任务、获得建议并选择其中一个执行。自动把多个算法串成端到端处理流水线不是当前验收要求；
   DAG 能力保留，但多节点执行必须由用户明确选择的计划或工作流触发。
5. 新算法的目标接入面接近 MCP 工具：功能描述、参数和 I/O Schema/Profile、固定 Adapter 或
   RPC 入口、资源/权限/环境声明、fixture 与 conformance test。已有 Profile 范围内的算法应为
   薄插件；只有首次引入新数据域或结果类型时才扩展公共 Profile/展示组件。
6. 测试采用分级策略：日常变更跑受影响单元测试，切片出口跑相关集成测试，阶段出口跑完整
   回归与有代表性的真实 CPU/CUDA/E2E。数值 Worker、依赖、数据和规范化配置均未变化时不重复
   同一耗时实验；复用证据必须绑定 Git tree、环境、数据和配置身份。测试减少重复执行，不减少
   必需覆盖，公共执行/安全合同变化仍须扩大回归范围。
7. `docs/PROJECT_PROGRESS.md` 是滚动余量唯一真源。每个 Verified 交付在同一 checkpoint 按
   `本期剩余 = 上期剩余 - 本 checkpoint 新增 Verified + 本 checkpoint 用户明确批准的调整`
   更新，并用自基线累计数交叉核算，避免重复扣减；必须保留基线、已完成项和当前值，不得把旧
   “十余个”原数带到后续阶段。每个 Verified 后若余量持平或上调，以及任何新拆分或范围变化，
   都必须先获用户明确同意。

### 推进规则

- “继续 D-003”默认推进当前一个中等切片；切片内部可有多个小提交，不要求用户在 migration、
  字段或单项测试之间逐次确认。
- 只有真实阻塞、需要改变已接受产品/安全边界、或当前切片达到验收点时才停下；用户仍可在任意
  已验证切片后试用，不要求一次完成整个阶段。
- 历史粗估不是承诺，也不批准跳过依赖；合并可按共同状态机推进，增加拆分或余量必须先说明
  具体安全/验证证据并获得用户明确同意。

## D-012：有限自动重试次数、预算与失败边界

- 决策状态：**Accepted**
- 实现状态：**P2-009B Implemented → Verified；P2-009B1 首次启动失败与 P2-009B2 运行后退出重试均已 Verified；完整 P2 仍 Pending**
- 用户确认日期：2026-07-17

### 已采纳方向

1. 每个已批准 Task 最多执行 **2 次** Worker attempt，即只允许 1 次自动重试。重试沿用
   同一 Task、Plan、Approval 与 dispatch intent，只能追加新的 exact attempt；不得覆盖首个
   attempt、重置 task budget，或把重试伪装成新 Task。
2. Approval 必须同时绑定每次尝试的资源上限、`max_attempts=2` 和由两次顺序执行推导的最坏
   wall/resource budget。旧 Approval 未声明该策略时保持 `max_attempts=1`，不得在升级后自动获得
   重试权限。
3. 只允许两类已经精确证明停止且不再持有执行 fence 的瞬态基础设施失败自动重试：
   **pre-running launch failure**，以及 exact ready 之后的 **`worker_exit`**。heartbeat 过期、PID
   推断、文件缺失或模糊状态都不是重试证明。
4. 数值/算法导致的普通 `worker_failed`、wall-time timeout、用户 cancel、成功终态、证据损坏、
   receipt/SQLite 分歧及仍需 reconciliation 的不确定状态一律不自动重试。第 2 次仍失败后 Task
   进入既有终态；人工再次运行必须创建新 Task、重新生成 Plan 并重新 Approval。
5. 当前不增加浏览器 retry 按钮或新的外部 mutation。active fenced Supervisor 只能在 SQLite 已
   原子授权、Adapter 又在私有 submission lock 与 kernel fence 下复核 exact proof 后启动第 2 次
   attempt；任何一侧不确定都 fail closed。
6. 按 D-011 将实现分为同一状态机的两个安全出口：P2-009B1 先关闭尚未形成 dispatched handle 的
   pre-running failure；P2-009B2 再处理 post-ready `worker_exit`。后者必须先证明 status、artifact、
   cancel 与 timeout 都能切换到同一个新 effective handle，不能只替换 Worker 而留下旧控制目标。
   该拆分源于句柄/产物所有权安全边界，不改变最多两次的已接受产品策略。

### 实现与验证状态

- P2-009B1 已于 2026-07-17 Verified：Approval 1.1 只为 current Deepwave `1.5.0` 绑定两次串行
  attempt、累计 `2W` 和固定失败 allowlist；历史 `1.4.0`/Approval 1.0 保持一次。SQLite v14、
  active-term reservation/delivery、Adapter private 1.2 attempt lineage、attempt 2 成功/耗尽和禁止
  attempt 3 均通过竞态与重启回归。
- 第二次 exact pre-running failure 原子提交 `Failed/retry_exhausted`，公开 Workbench/API/UI 只暴露
  有界终态且没有 retry mutation。无 handle 的 Trash/Purge 由 Store cleanup proof 绑定两个 attempt，
  在 idle execution fence 下先写同 purge 墓碑再删除，支持目录间和目录内部分删除的崩溃重放。
- P2-009B2 已于 2026-07-17 Verified：SQLite v15 只在 exact spawned + ready + running、idle
  execution fence、无 cancel/timeout ownership、非 `0/75/76` exit code 与 append-only worker-exit
  receipt 全部一致时授权 active-term retry。reservation 先隐藏旧 effective handle 并退役旧 timeout；
  private 1.3 attempt 2 ready 后原子发布 replacement handle，status、artifact、cancel、timeout 全部
  切换到 replacement。普通 status bridge 对 `worker_exit` 保持只读，因此同周期竞态不能绕过 retry
  decision；B1/private 1.2 与 B2/private 1.3 的 attempt 2 exit 都只会 exact terminalize，不会创建
  attempt 3。
- B2 的 mixed-lineage Trash/Purge 绑定两次 exact attempt；公开 Workbench/API/UI 只投影有限自动
  retry 能力与有界状态，不增加 retry mutation，也不泄漏 receipt、handle、hash、PID 或路径。
  最终分级回归通过 Runtime 360/360、固定 venv Worker 32/32、launch-control 39/39、Web 47/47、
  Embedding 6/6、CTest 39/39、MCP 1/1、Node/治理与三轮独立审计；未重复数值 FWI/CUDA。
- 按 D-011，仅就 P2，B2 后剩余交付压缩为两个不降低质量门的中等切片：先一次关闭负向/不确定
  reconciliation 矩阵，再合并 SSE 与完整 P2 故障、代表性 CPU/CUDA 阶段出口。

## 新会话的最小检查清单

```text
1. 用户在仓库中正常打开 Codex 后直接提问，无需执行项目脚本
2. Codex 依据根 AGENTS.md 自动完整读取 docs/PROJECT_CURRENT_STATE.md
3. Codex 自行检查当前分支、git status、git diff，保护用户已有改动
4. D-003 默认定向读取当前计划阶段、进度 checkpoint/切片和相关 D-*；满足 D-010 条件时深读
5. 代码导航优先有界 CodeGraph，pending-sync/未索引文件和真实证据使用直接工具
6. 现场核对进度账本，区分 Accepted / Implemented / Verified / Pending
7. 从依赖已满足的 next safe action 继续，不依赖旧状态快照或重复已验证工作
8. 用户明确要求固定的内容并入既有决定或进度账本；新增编号决定另获明确批准
9. 修改后同步短入口、真源和测试证据；不写入秘密、模型和运行产物
10. D-005 在用户明确确认前仍是 Proposed，不得报告为 Accepted
```

## 决策变更记录

| 日期 | 条目 | 变化 | 依据 |
|---|---|---|---|
| 2026-07-15 | D-001 | 新增并标记 Accepted | 用户明确要求保留自然语言、确认卡片、确定性命令和受验证 CLI 的建议，并拒绝以运行目录 watcher 作为主要入口 |
| 2026-07-15 | D-002 | 新增并标记 Accepted | 用户明确要求跨新 Codex 窗口持续保留决策，并规定新建议需征得同意后加入 |
| 2026-07-15 | D-002 | 增加安全 Codex 项目入口并完成隔离测试 | 用户要求新 Codex 在目录中启动即可了解工程，并能基于共享工作区持续开发 |
| 2026-07-15 | D-002 | 改为普通 Codex 会话自动接续，辅助脚本降级为内部诊断 | 用户明确要求启动 Codex 后直接提问，不再记忆或手动运行项目脚本；“保留这个”等明确表述自动触发持久记录更新 |
| 2026-07-15 | D-003 | 新增并标记 Accepted，运行时实现仍为 Pending | 用户认可 Guided/Agent 双模式、动态规划控制面与确定性任务内核方案，并要求先保存计划和跨会话进度 |
| 2026-07-15 | D-003 | 根据风险评估调整阶段边界 | 用户指出 P0 过度设计、P1/P2 倒置、SQLite/Redis 真值、环境可复现、置信度与 P4 优先级六项风险 |
| 2026-07-15 | D-003 | P0 最小 FWI 合同达到 Verified，持久运行时仍 Pending | 七类 Schema、canonical/hash、Gate 和规范完成；合同/现有 FWI/Web/治理回归通过 |
| 2026-07-15 | D-003 | P1.1a SQLite 持久基础达到 Verified，P1 仍在进行 | WAL TaskStore、无执行副作用 TaskService、create 幂等、不可变历史、作用域与事件原子性通过聚焦及回归测试；submit/Catalog/Adapter 仍 Pending |
| 2026-07-15 | D-003 | P1.1b 注册快照基础达到 Verified，P1 仍在进行 | SQLite v2 原位升级、不可变 Catalog/Registry、批准预算持久行、服务端 snapshot 校验与固定 FWI 注册映射通过聚焦及回归测试；submit/Adapter/Web 仍 Pending |
| 2026-07-15 | D-003 | P1.2a 固定 Deepwave Adapter 达到 Verified，P1 仍在进行 | 六方法单节点 Adapter、Registry/物理 identity 分层、跨进程幂等、固定 launcher、状态脱敏和严格 artifact 复核通过 17 项聚焦、100 项 Runtime 与真实 CUDA smoke；submit/Queued/Web/P2 恢复仍 Pending |
| 2026-07-15 | D-003 | P1.1c 原子 submit 后端达到 Verified，P1 仍在进行 | SQLite v3 同事务 Gate/预算/idempotency/intent/task_queued/Queued，事务后固定 one-shot dispatch、preflight/实际 fingerprint receipt 和显式 crash states 通过 117 项 Runtime 与全量回归；Guided Web/P2 恢复仍 Pending |
| 2026-07-15 | D-003 | P1 Guided Web 达到 Verified，P1 完成；P2 未开始 | SQLite v4、固定 Guided composer、同源审批/任务 API、确认卡、状态事件桥、标准 NPY/CSV 与真实 CUDA + 重启端到端验证通过；按用户要求停在 P2 之前 |
| 2026-07-15 | D-004 | 新增并标记 Accepted | 用户明确要求 Codex 管理 Git，使用独立实现分支和可验证 checkpoint |
| 2026-07-15 | D-004 | 首个 SSH checkpoint 验证通过 | `b5ac633` 已推送到 `origin/feature/scientific-agent-runtime`，本地/upstream 一致 |
| 2026-07-15 | D-005 | 新增并标记 Proposed | 用户对 AI prompt 是否上传仍有保留；记录可审批提案，本次先采用不上传临时 prompt 的安全默认 |
| 2026-07-15 | D-006 | 新增并标记 Accepted，开始实现 | 用户明确要求把固定 Marmousi FWI 迭代上限设置为 10000，供少数高次数验证使用；默认次数与人工批准保持不变 |
| 2026-07-15 | D-006 | 实现与验证完成 | 1..10000 全链路边界、Algorithm/Adapter/MCP `1.1.0`、旧 `1.0.0` 只读兼容、完整回归和既有数据库/HTTP 无执行验收通过；P2 未开始 |
| 2026-07-15 | D-007 | 新增并标记 Accepted；实现完成、最终现场验证待收尾 | 用户明确要求优化器/学习率选择与建议卡、轮询不抢占滚动位置、左栏持久任务及关闭/刷新后找回；范围限于 P1 配置/交互维护与 P2-001 发现/重开，不含完整 P2 可靠性 |
| 2026-07-15 | D-007 | 当前提交身份推进至 `1.3.0`；现场验证仍待收尾 | 保持已注册六参数 `1.2.0` 快照不可变并严格读兼容；用新版本固定 FWI-only、迭代/seed 上界和 optimizer 条件 Schema 的一致声明 |
| 2026-07-15 | D-007 | P1-007 与 P2-001 有界切片 Verified | 自动化、旧 CPU 500 次任务重开、真实 CUDA/SGD 两步闭环、artifact hash 与页面重载后发现/重开均通过；完整 P2 仍 Pending |
| 2026-07-15 | D-008 | 新增并标记 Accepted；实现开始 | 用户明确要求理清对话与任务关系、在对话中查看任务进度/结果、保留对话与任务删除场景，并恢复喜爱的 FWI 六图展示；采用可选引用、无级联、可恢复任务回收站和 task-scoped 标准图片 artifact |
| 2026-07-15 | D-008 | 对话/任务分离、终态回收站与 Algorithm/Adapter `1.4.0` 六图实现完成；最终验证待收尾 | 浏览器 schema v3 可选引用和本地无级联删除、SQLite v6 append-only/CAS/idempotent Trash/Restore、持久 Plan 驱动的历史两 artifact/当前八 artifact 严格读取均已落地；完整回归与 fresh v6 真实端到端证据未登记前不标 Verified，完整 P2 仍 Pending |
| 2026-07-15 | D-008 | P1-008 与 P2-002 有界切片 Verified；完整 P2 仍 Pending | Scientific Runtime 165/165 与全量回归通过；fresh 私有 v6 CUDA 1.4 两步任务验证 10 events、8 artifacts/6 PNG、数值更新与 GPU 证据，当前 v6 checksum、Trash/Restore/409 拒绝/abandon、task/event/artifact 三类指纹及 8 项 bytes/hash 重启不变性和服务清理均通过；保留图片严格重验和 visibility 线性历史读取两项低风险性能债务 |
| 2026-07-15 | D-009 | 新增并标记 Accepted；实现有界 P2-003 | 用户明确要求任务回收站支持永久删除，并要求本地文件随之删除；范围限制为已回收终态任务的本地 Worker 目录，不重复耗时实验 |
| 2026-07-15 | D-009 | P2-003 有界切片 Verified；完整 P2 仍 Pending | SQLite v7 两阶段墓碑、receipt-bound FD-relative Adapter purge、崩溃/并发/符号链接边界、强确认 Web/API 和 conversation 无级联均通过；Runtime 180/180、Web 29/29、Node UI/治理 PASS，未运行真实 FWI/CUDA |
| 2026-07-16 | D-003 | P2-004 有界启动 receipt 收养/状态追赶 Verified；完整 P2 仍 Pending | 用户要求继续 D-003；真实 Adapter 安全审查后禁止 startup 首次派发，改为 bind 后只读收养 current 1.4 exact launched receipt，pending/no-record/ambiguous 全 deferred；严格 handle、并发收敛、1000+ events、忙端口与一次状态追赶通过最终回归，未运行真实 FWI/CUDA |
| 2026-07-16 | D-003 | P2-005A 控制面 fenced lease/持续状态泵 Verified；完整 P2 仍 Pending | 用户继续 D-003；SQLite v8 scope term/lease/closure 与 supervised commit 围栏、observation-only Runtime Supervisor、Web lease-before-listen 生命周期和 cooperative shutdown 通过最终回归。该 lease 只保护后台控制面状态写，不是 Worker capacity/attempt lease 或 heartbeat；未派发/重启 Worker，未运行真实 FWI/CUDA |
| 2026-07-16 | D-010 | 新增并标记 Accepted；低 token bootstrap 与 CodeGraph 路由 Verified | 用户要求“开始/继续 D-003”自动减少 token 和等待时间；短入口、按需深读、CodeGraph 优先/回退、聚焦测试与长文档保真规则通过治理测试 |
| 2026-07-16 | D-003 | P2-005B 固定 Adapter 托管 Worker staged launch fence Verified；完整 P2 仍 Pending | 用户继续 D-003；exec-inherited submission/capacity `flock`、pre-import exact ready/heartbeat、controller-crash self-promotion、post-Popen deferred、active-purge fence 与 private sidecar guard 通过最终回归。该切片仍不是 SQLite Worker scheduler/lease，不覆盖 standalone CLI/C++ MCP capacity，未运行数值 FWI/CUDA |
| 2026-07-16 | D-011 | 新增并标记 Accepted；D-003 阶段与质量门保持不变 | 用户澄清希望在不降低质量的前提下减少过细切片，但不取消逐步开发；切片数保持弹性、多算法按独立选项接入、自动全流程不作为当前验收，测试改为分级执行并保留阶段完整回归 |
| 2026-07-16 | D-003 | P2-005C fenced Worker 证据投影/late adoption Verified；完整 P2 仍 Pending | 用户继续 D-003；SQLite v9 exact attempt/heartbeat sample、active-term adoption 与无 launcher observation 接入 Supervisor，kernel `flock` 仍为执行/容量权威，未运行数值 FWI/CUDA |
| 2026-07-16 | D-003 | P2-006 可恢复 fenced scheduler/受监督首次派发 Verified；完整 P2 仍 Pending | 用户继续 D-003；enqueue-only submit、SQLite v10 active-term authorization、pending/no-record 首派、exact staged 同 attempt 恢复及 current 1.4 legacy-private receipt fenced adoption 通过分级回归；cancel/timeout/retry/reconciliation/SSE 仍 Pending，未重复数值 FWI/CUDA |
| 2026-07-16 | D-003 | P2-007 exact-attempt user cancellation Verified；完整 P2 仍 Pending | 用户继续 D-003；SQLite v11 durable admission/active-term delivery/outcome、Worker-published capability 与 self-cancel、ack + stopped heartbeat + idle execution fence 证明、自然终态 superseded 和 Guided Web 状态通过 Runtime 271/271、固定 venv Worker 31/31 + launch-control 17/17、Web 46/46 及全量分级回归；运行 CPU smoke 3/3，未运行 CUDA。timeout 因终态语义、计时起点和 force policy 尚未决定而单独 Pending |
| 2026-07-16 | D-003 | P2-008 timeout 语义 Accepted；实现 In progress | 用户明确要求按建议实现 timeout：终态为 Failed/WALL_TIME_EXCEEDED；起点为 exact Worker 首条 SQLite durable ready+running observation；grace 后由 exact Worker 自退出，控制面不 signal 持久 PID。能力限定 current 1.4/private 1.1 + v2 exact-stop；timeout delivery authorization 与 durable cancel admission first-writer-wins |
| 2026-07-16 | D-003 | P2-008 exact-attempt wall-time timeout 有界切片 Verified；完整 P2 仍 Pending | SQLite v12 immutable window/authorization/outcome、Store 首条 current Worker durable ready+running `observed_at` 起算、authorization 前零 Worker mutation、四态竞争、v2 Worker self-stop 与七字段只读 Workbench 投影完成；Runtime 304/304、Worker 32/32、launch-control 25/25、Web 46/46、Embedding 6/6、CTest 39/39、MCP 1/1、Node/governance 与真实 CPU Deepwave timeout E2E PASS，未运行 CUDA；下一步有限 retry 与 reconciliation resolution |
| 2026-07-16 | D-003 | P2-009A positive receipt resolution/adoption 有界切片 Verified；完整 P2 仍 Pending | SQLite v13 append-only resolution/effective dispatch、managed/private exact proof、每周期最多一次 probe、同周期 timeout/status catch-up 与六字段只读 Workbench 投影通过 Runtime 319/319、Worker 32/32、launch-control 25/25、Web 46/46 及完整分级回归；未运行 CUDA，finite retry 当时仍待确认 |
| 2026-07-17 | D-012 | 新增并标记 Accepted；P2-009B 开始 | 用户明确要求按建议继续：同一 Task/Plan/Approval/intent 最多两次，只对 exact stopped 的 pre-running launch failure 与 post-ready `worker_exit` 自动重试；批准绑定最坏预算，其余失败不重试，人工再跑创建新 Task/Approval。基于 effective handle/产物/取消/超时目标迁移安全边界，先实现 B1、再实现 B2 |
| 2026-07-17 | D-003 / D-012 | P2-009B1 pre-running launch failure retry Verified；完整 P2 仍 Pending | SQLite v14 + Approval 1.1 两次串行/累计 2W 预算、current Deepwave 1.5 exact stopped proof、active-term reservation/delivery、append-only attempt 2、retry exhaustion 终态、无 handle 双 attempt Trash/Purge 与有界公开投影通过 Runtime 343/343、Worker 32/32、launch-control 26/26、Web 47/47、Embedding 6/6、CTest 39/39、MCP 1/1、Node/治理及独立最终审计；未重复数值 FWI/CUDA。B2、剩余 reconciliation、SSE 和 P2 出口仍 Pending |
| 2026-07-17 | D-003 / D-012 | P2-009B2 post-ready worker-exit retry Verified；完整 P2 仍 Pending | SQLite v15 exact worker-exit receipt/reservation/replacement/exhaustion、receipt-first terminal arbitration、private 1.3 attempt 2、effective handle/status/artifact/cancel/timeout 共同迁移、旧 timeout retirement、mixed-lineage Trash/Purge、只读 Web 与禁止 attempt 3 通过 Runtime 360/360、Worker 32/32、launch-control 39/39、Web 47/47 及完整分级回归；未重复数值 FWI/CUDA。P2 剩余交付压缩为完整 reconciliation 矩阵与 SSE + P2 阶段出口两个切片 |
| 2026-07-17 | D-011 | 建立全项目滚动余量 | `0cbe131` 的约 12 个粗估锚点覆盖完整 P2 余项与 P3–P6；其后 6 个切片已 Verified，当前账本粗估约 6，其中 P2 明确为 2。每个 Verified 必须递减，任何上调或拆分需用户明确批准 |
| 2026-07-17 | D-002 / D-003 / D-011 | 撤销 `419c41f` 中未经授权误建的编号并完成全量文档纠错 | 用户明确说明“固定”不授权 Codex 自行新增 D 编号。该编号从未获得用户批准，当前不构成项目决定；相关内容归入既有决策。完整计划仍为 P0–P6，未改变任何阶段交付、顺序或出口 |
