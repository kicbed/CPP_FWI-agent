# 科研任务 Agent Runtime 实施计划

<!-- scientific-agent-runtime-plan: v1 -->

- 决策编号：`D-003`
- 决策状态：**Accepted**
- Runtime 实现状态：**P0 + P1 Verified；P2.1 任务发现/重开、P2.2 可恢复回收站、P2.3
  本地结果永久删除、P2.4 启动 receipt 收养/状态追赶、P2.5A 控制面 fenced lease/持续状态泵与
  P2.5B 固定 Adapter 托管 Worker launch fence、P2.5C fenced Worker 证据投影/late adoption
  均为有界 Verified；完整 P2 仍 Pending**
- 用户确认日期：2026-07-15
- 实现分支：`feature/scientific-agent-runtime`
- 基线分支：`feature/fwi-deepwave-2d-acoustic`
- 基线提交：`ffeb5bc`
- 进度真源：`docs/PROJECT_PROGRESS.md`

本文记录已经获批的目标架构、阶段边界和验收标准。它不是完成报告。实际进度必须以
`docs/PROJECT_PROGRESS.md`、当前代码、Git 状态和测试结果共同判断，不能仅凭本文推断。

## 1. 目标

把当前“固定单跳路由 + 专家 Agent + FWI 特例任务”升级为一个面向科研计算的工作台：

1. 用户选择或安全导入数据；
2. 用自然语言描述反演、正演、预处理、去噪、质量评价或结果分析目标；
3. Agent 识别意图、暴露歧义、提出方法与参数建议；
4. 用户确认目标和计划，或显式授予有限的会话级自主权限；
5. 系统把计划编译为受验证、可审计的任务图；
6. 独立步骤在资源允许时并行执行，长任务能够等待、取消、重试和恢复；
7. 结果以统一 artifact 协议展示，并由分析 Agent 给出带证据和限制的结论；
8. 新算法通过标准 manifest、适配器和一致性测试接入。

目标交互参考 Codex/Claude Code 的任务式体验，但执行核心保持科研工作流所需的确定性、
可复现性和安全边界。不会让 LLM 直接拼接 shell、接收任意服务器路径或绕过审批执行计算。

## 2. 核心架构决定

### A-001：Guided/Agent 双模式，共用单一任务内核

- **Guided 模式**：用户直接选择数据、算法和参数，适合复现和已验证流程。
- **Agent 模式**：Agent 从自然语言生成任务草稿、候选方法和计划，适合探索性任务。
- 两种模式都产生相同的 `TaskDraft` 和 `PlanGraph`，经过相同校验、审批、调度、审计和
  artifact 收集，禁止形成两套执行栈。

### A-002：动态规划控制面，确定性执行面

- LLM 负责理解、建议、拆解、解释和结果分析。
- Schema validator、Policy Engine 和 Task Runtime 决定计划是否可执行。
- Agent 给出的意图/参数置信度只用于解释和触发澄清，不能代替确定性执行门。执行前必须
  同时验证：`DatasetRef` 版本/hash/access scope、算法真实注册且版本 pin/allowlist、I/O 类型、
  参数 Schema、资源、DAG 无环、无未确认字段、当前 `ApprovalDecision` 未过期且其
  `plan_hash` 等于当前规范化计划、side-effect policy 以及幂等键合法性。
- 数值计算由版本固定的 Algorithm Adapter/Worker 执行。
- 子 Agent 适合数据检查、方法比较、计划评审、日志归纳和结果分析；GPU/CPU 作业由资源
  调度器管理，不能用子 Agent 会话代替。

### A-003：只引用注册数据，不信任任意路径

- 任务只接受不可变 `DatasetRef`/`asset_id`。
- 本地上传先进入隔离 staging，检查大小、后缀、MIME、sidecar、shape、dtype、单位和哈希，
  再注册为新版本。
- 浏览器文本、LLM 输出和 MCP 参数不能直接成为服务器绝对路径。

### A-004：批准绑定不可变计划

- 用户批准 `plan_hash`，不是批准一段自然语言。
- 节点、算法版本、参数、输入数据、资源或权限发生实质变化时，旧批准自动失效。
- 默认每个有副作用的任务都需审批。
- “本会话授权 Agent”只能限制在明确的数据集、算法白名单、GPU/CPU、最长时间、最大迭代
  和费用/任务数量预算内，并保留审计事件。

### A-005：任务状态独立于聊天记录

- Conversation 保存对话和滚动摘要。
- Project Memory 保存经批准的长期偏好和决定。
- Task Store 保存草稿、计划、审批、事件、节点状态和 artifact。
- Conversation 与 Task 通过可选引用关联：对话可以没有任务、引用多个任务；同一任务也可被
  多个对话引用。删除对话或移除引用不能级联取消、隐藏或删除任务。
- 不能通过重新解析聊天文本猜测任务是否已提交或完成。

### A-006：MCP/A2A 是适配与互操作边界，不是全部运行时

- MCP 用于发现和调用工具；A2A 用于 Agent 能力描述与委派。
- Dataset Catalog、Algorithm Registry、Task Store、资源调度和审批仍有独立真源。
- 任何 submit/修改/删除类工具必须经过显式副作用标记、Policy Gate 和幂等键；不得在 LLM
  解析失败时自动调用“第一个候选工具”。

### A-007：持久任务采用事件与 checkpoint 恢复

- 本地 MVP 使用 SQLite WAL 作为任务、计划、审批、状态和事件的唯一权威事实源；
  存储接口允许未来切换 PostgreSQL。
- Redis 只承担对话缓存、短期锁、lease 和事件通知，不保存第二份权威任务状态；
  Redis 丢失后必须能从 Task Store 重建可恢复的派生状态。
- 节点提交使用 idempotency key。
- Worker 使用 lease/heartbeat；控制面重启后 reconciliation 真实进程和外部状态。
- 已完成且具备相同输入/版本/参数的幂等节点不得因恢复而重复执行。

## 3. 目标数据流

```text
Dataset Catalog / Safe Import
              |
              v
         DatasetRef(s)
              |
     +--------+---------+
     |                  |
Guided Composer    Agent Intent Planner
     |                  |
     +--------+---------+
              v
           TaskDraft
   (goal, inputs, constraints,
    missing fields, suggestions)
              |
       clarification/edit
              v
           PlanGraph
  (typed nodes, deps, resources,
   risks, estimates, acceptance)
              |
       Policy + Approval
              |
        Durable Task Runtime
  (queue, DAG, leases, retry,
   cancel, checkpoint, resume)
              |
 Algorithm Adapter / MCP / A2A / Worker
              |
 ArtifactManifest + Metrics + Analyzer
```

## 4. 公共契约 v1

v1 只覆盖“已注册 Marmousi 数据 -> 参数确认 -> Deepwave FWI -> artifact”这一条
最小垂直链路，不尝试一次描述所有未来算法。每个契约都必须带 `schema_version`；
顶层未知字段默认拒绝，只能通过显式命名空间的 `extensions` 容器或新 schema 版本演进。

| 对象 | 最小职责 |
|---|---|
| `DatasetRef` | `id/version/hash/type/metadata/lineage/access_scope`，不含任意执行路径 |
| `AlgorithmManifest` | 算法 ID/版本、任务类型、参数 Schema、I/O 契约、资源和安全声明 |
| `TaskDraft` | 目标、数据引用、约束、缺失项、建议、置信度、修订号 |
| `PlanGraph` | 节点、依赖、算法版本、参数、资源、风险、验收标准和计划哈希 |
| `ApprovalDecision` | plan hash、批准人/来源、范围、有效期、资源与自主权限上限 |
| `RunEvent` | task/node 状态、进度、checkpoint、错误、时间和单调序号 |
| `ArtifactManifest` | artifact 类型、来源节点、路径/URL、hash、指标、展示 Schema、执行指纹和血缘 |

`RunEvent`/`ArtifactManifest` 共用一个嵌套的可复现执行指纹，至少记录：算法/适配器
版本、Git commit/tree hash/dirty flag（dirty 时还需精确 diff 或打包源码 hash）、容器镜像
digest 或环境 lock hash、Python/CUDA/PyTorch/Deepwave 版本、随机种子、硬件/设备、规范化
配置/hash、输入数据 hash、deterministic flags 与已知非确定性算子。可复现模式如果无法得到
完整的 clean/dirty 源码身份应拒绝执行；开发模式可运行但必须显式标记 non-reproducible。该指纹
是 provenance，不能自动保证不同 GPU/库版本下 bitwise 一致。它是七类公共契约中的嵌套记录，
不再增加独立顶层对象。

任务状态机：

```text
Draft -> NeedsInput -> AwaitingApproval -> Queued -> Running
                                              |       |
                                              |       +-> Waiting -> Running
                                              |       +-> Retrying -> Running
                                              v
                              Succeeded | Failed | Cancelled
```

状态转换必须由服务端验证。计划未批准、批准失效或资源策略不满足时不能进入 `Queued`。

## 5. 组件责任

### Workbench API/BFF

- 为 Web 提供同源 Dataset、Draft、Approval、Task、Event 和 Artifact API。
- 浏览器不再直接拼接多个本机端口。
- 后续统一承载身份、CSRF/CORS、审计和流式事件边界。

### Dataset Catalog

- 管理不可变数据版本、校验信息、预览和 lineage。
- 数据文件保持在 Git 仓库外；Git 只保存公开、非敏感的 Schema 和示例 manifest。

### Intent/Planner

- 从用户目标和 `DatasetRef` 生成严格 Schema 的 `TaskDraft`/`PlanGraph`。
- 只能选择 Registry 中存在且输入输出契约兼容的 `algorithm@version`。
- 不执行数值任务，不生成自由 shell。

### Policy/Approval

- 验证数据权限、算法安全等级、资源预算、参数边界和副作用。
- 记录批准、拒绝、修改和失效事件。

### Task Service 与 Scheduler

- 持久化状态机、DAG、事件和幂等映射。
- 调度满足依赖的节点，并执行 CPU/GPU 资源锁、优先级、超时、取消和有限重试。
- 通过 SSE（或后续等价事件协议）支持浏览器断线重连和 `last_event_id`。

### Algorithm Adapter

每个算法至少实现或声明：

```text
validate -> estimate -> submit -> status -> cancel -> collect
```

manifest 还必须描述参数与结果 Schema、输入输出类型/shape/dtype/unit、环境/容器、资源、
进度、幂等性、checkpoint 能力、权限、失败模式、health check 和 smoke fixture。

### Analyzer

- 只基于实际 artifacts、metrics、日志摘要和验收标准生成结论。
- 明确区分事实、推断、失败和科学外推限制。
- 后续实验建议重新进入 `TaskDraft`，不能在默认策略下自动启动新任务。

## 6. 实施阶段与完成标准

### P0：契约与架构基线

交付：

- 只支撑最小 FWI 链路的七类对象版本化 JSON Schema/Proto 映射；
- `schema_version`、规范化、受控 `extensions` 和兼容性演进规则；
- 服务端状态转换、计划哈希和批准失效规则；
- 执行环境指纹与确定性可执行门规范；
- Dataset/Task/Approval/Event/Artifact API 草案；
- Algorithm Adapter v1 规范和安全威胁模型；
- 当前 FWI metadata、AlgorithmCard、runner 白名单差异清单。
- 若 D-005 获批，增加旧 prompt-like 文件边界审计；未获批前不迁移或删除。

完成标准：Schema 正反例测试通过；没有改变现有 FWI 执行行为；所有未实现 API 明确标记。

### P1：最小持久垂直切片

交付：

- SQLite WAL 最小 Task Store/TaskService，先提供不变 `task_id`、幂等创建、计划/批准/
  事件持久化和状态查询；
- Dataset Catalog 与已验证 Marmousi 注册的最小实现；
- 现有 Deepwave FWI 的标准 Adapter，不改动数值核心；
- Guided Web 数据选择/预览，仅通过表单/当前确定性 FWI 入口组装 TaskDraft；
- 可编辑 TaskDraft 和参数确认卡；P1 不引入 LLM Planner、Plan Critic 或子 Agent；
- “批准运行 / 修改 / 放弃草稿”流程；放弃只适用于 Draft/NeedsInput/AwaitingApproval，
  运行中取消 API/UI 属于 P2；

完成标准：选择注册 Marmousi 数据、描述反演、确认参数后返回 SQLite 持久的真实
`task_id`；重复请求不重复创建任务；重启后任务身份与已落库事件仍可查询；未经批准
不创建 FWI job；现有 FWI 回归测试继续通过。进程身份 reconciliation、完整取消和
checkpoint 恢复仍属于 P2，P1 不伪装已具备这些能力。

实现与现场验证见
`docs/architecture/SCIENTIFIC_RUNTIME_P1_GUIDED_WEB.md`；P1 已达到上述完成标准，P2 仍为
Pending，未因 Guided Web 状态轮询而提前实现恢复语义。后续 D-007 维护
切片为当前 FWI 补充 Adam/SGD 与受校验学习率，并保持人工批准和单节点
执行边界；D-008 后续结果维护将当前 Algorithm/Adapter 推进至 `1.4.0`，由持久 Plan 精确声明
两个数值输出和六张固定 PNG，并保持 `1.0.0`–`1.3.0` 历史计划的两个标准输出不可变。
这些配置/结果维护不等于 P2 可靠性完成。

### P2：持久任务可靠性加固

交付：

- 在 P1 最小 Task Store 上增加队列、lease、heartbeat 和严格单调事件序号；
- 取消、超时、有限重试和启动 reconciliation；
- SSE 任务事件和浏览器刷新恢复；
- 多任务列表，不再每个对话只保留一个 FWI job。

当前有七个有界先行切片；它们不改变完整 P2 的出口条件。

**P2.1 任务发现/重开**：

- 从 SQLite Task Store 按 `project_id`/`principal_id` 精确限域、定界分页读取持久任务摘要；
- 页面加载时恢复左栏发现索引，用户可重开单个任务卡；关闭卡片只关闭
  视图，不取消或删除任务；
- 列表 GET 不触发 Adapter status refresh，重开后仍由现有的单任务 GET 读取/轮询；
- 轮询重绘保持用户阅读位置：只有用户原本在底部时继续跟随，显式打开/重开
  才一次性展示任务卡。

P2.1 当前为 **Verified**。它不包含自动重建运行意图、
pending/dispatching reconciliation、取消、超时、lease/heartbeat、retry 或 SSE，
也不会在恢复页面后为已批准但未确认 submit 的任务生成新幂等键。因此完整
P2 仍为 **Pending**。

用户后续明确批准的有界 **P2.2 任务可见性回收站** 只为具有 resolved terminal provenance
的终态任务增加 scope-bound、
append-only、CAS/idempotent 的 Trash/Restore。它不物理删除 Draft/Plan/Approval/Event/artifact，
restore 不重跑；未确认 dispatch、reconciliation 和运行中任务拒绝进入回收站。该切片和
Conversation/Task 可选引用、浏览器本地对话删除、1.4 六图结果维护一起记录于 D-008；仍不
代表 cancel、lease、retry、reconciliation 或 SSE 已完成。P2.2 已通过完整自动化与 fresh v6
真实 CUDA 任务的 Trash/Restore、拒绝边界、artifact 可读和同库重启验收，因此为
**Verified**。当前实现每次图片 GET 会完整 collect/解码以执行严格安全复核，visibility 历史
读取也随事件数线性增长；这是后续性能加固边界，不代表完整 P2 已完成。

用户进一步批准的有界 **P2.3 回收站永久删除本地结果** 在 P2.2 Trash 之后增加独立强确认。
SQLite 先提交 append-only purge tombstone，立即阻止 Restore、状态和 artifact 用户读取；固定
Dispatcher/Adapter 再从 durable receipt 推导唯一 Worker job 目录，在 per-submission lock 下
确认 succeeded/failed，以 FD-relative/no-follow 方式删除目录，最后提交 outcome。中断后的
新请求只能继续同一 purge，不能恢复或重跑；完成后任务不再出现在 active/trash 列表。
SQLite 既有任务审计历史和 Adapter 最小 control receipt/lock 保留，conversation/message 不
级联删除，引用标记为已永久删除。本切片已用临时目录、Fake Dispatcher 和轻量 HTTP 验证，
没有按次数重复真实 FWI/CUDA；它不包含 cancel、lease、retry、reconciliation、SSE、服务器
transcript 永久删除、SQLite 审计硬删除或备份/外部副本清理，因此完整 P2 仍为 **Pending**。

用户要求继续 D-003 后实现的有界 **P2.4 启动 receipt 收养与状态追赶** 复用既有 SQLite
intent/claim/outcome 和固定 Adapter control record。真实 Adapter 审查证明 process-local
`max_active` 不能充当重启后的全局容量，因此 startup pass 明确不 claim/首次派发 pending，
也不把普通 dispatch 当 probe。loopback Workbench 先 bind 但不 listen，再完成最多 10000 个
active task 的 scope-bound 全分页扫描。dispatching 只按 immutable intent 推导单一 current 1.4
私有 record，在既有 flock 下只读 lookup；仅 durable `launched` 且 request/hash/receipt 全部精确
匹配时写 `dispatched`。missing/preparing/launching/failed/corrupt/历史版本保持 deferred，既有
`reconciliation_required` 不自动翻转；lookup 不调用 launcher/readiness、不创建 job 目录、不
扫描 `FWI_RUN_ROOT`。已有/新收养 dispatched task 做一次 status/event 追赶，1000+ 事件分页，
单任务 status 脱敏错误/CAS 冲突继续其他任务，receipt 分歧和 Task Store 损坏仍 fail closed。
成功后才 activate/publish/serve，忙端口零 recovery。TaskService 80/80、Adapter 27/27、
Scientific Runtime 201/201、Web 36/36 与全量回归通过；本切片不猜 PID、不创建 watcher，也不
实现 fenced capacity、lease/heartbeat、持续 supervisor、cancel/timeout/retry 或 SSE，因此
完整 P2 仍为 **Pending**。

继续 D-003 后实现的有界 **P2.5A 控制面 fenced lease 与持续状态泵** 在 SQLite v8 增加
`(project_id, principal_id)` scope 的单一当前 lease、连续递增 fencing term、append-only
term/closure 以及 supervised RunEvent commit audit。acquire/heartbeat/release 和受监督写入都在
SQLite 写事务内采样时间；旧 term、过期 term、时钟回退、ABA 和 takeover 后迟到写 fail closed。
这只围栏后台 Supervisor 的状态提交：startup recovery 和浏览器 GET 仍保留既有无租约的单调
CAS 路径，不能据此宣称所有控制面写入都由唯一 owner 独占。

Runtime Supervisor 构造无副作用，在 Web bind 和 P2.4 recovery 成功后、listen/publish 前取得
lease 并 ready。它只对 active 视图中 Queued/Running 且 durable dispatch outcome 精确为
`dispatched` 的 task 持续调用 status bridge；pending、dispatching、missing、
`reconciliation_required` 均 deferred，终态直接跳过、不观察。Supervisor 没有
dispatch/launcher 能力，不扫描 run root，不猜 PID；lease loss 只使控制面自我隔离，不改变
Worker 生命周期或容量。Web 关闭
先 close listener，再 cooperative stop/release Supervisor，定界等待已有 Handler，最后 unpublish；
非 daemon 线程、外层 30 秒 KILL 与 lease expiry 保留任意阻塞 I/O 的最终边界。Scientific Runtime
226/226、Worker 28/28、Web 45/45 与完整回归通过，未运行真实 FWI/CUDA。

继续 D-003 后实现的有界 **P2.5B 固定 Adapter 托管 Worker staged launch fence** 为 current
1.4 私有 submission 增加独立 control schema 1.1 的唯一 attempt binding 与 managed ticket。
固定 launcher 在 `Popen` 前取得 stable per-submission execution `flock` 和固定策略下的 capacity
slot `flock`，两个 FD 跨 exec 由 Worker 持有；父控制器退出不能释放子进程的执行权或容量。
轻量 bootstrap 在导入 Torch/Deepwave 前验证 ticket/FD/inode/slot generation/PID，启动独立
heartbeat thread 并写 immutable ready 后才进入数值 Worker；子进程可补齐父控制器遗漏的
`leased → spawned`。heartbeat 只提供 exact attempt 健康证据，不能凭 TTL/过期替换仍持锁的
Worker，内核锁才是本切片的安全权威。

Safe launcher 只有在 ready/heartbeat 与 attempt 全绑定时才返回；已确认 pre-ready 退出可失败，
任何 post-Popen 未确认结果保持 `launching`/deferred。P2.4 lookup 可精确收养已经 ready 的
`launching` receipt，capacity 满也不写 immutable dispatch outcome。purge 必须在整个删除期间
持有空闲 submission fence；managed sidecar 的临时原子文件不进入 artifact 目录，legacy CLI
拒绝托管目录，Web 拒绝所有 hidden artifact component。current 1.4 历史 private schema 1.0
继续严格读取；Algorithm/Adapter 1.4 的科学输出合同不因内部 launch-control schema 改变。

P2.5B 在该 checkpoint 仍不是 SQLite TaskStore Worker lease 或 scheduler；后续 P2.5C 已把
current managed Adapter 的 attempt/ready/heartbeat 证据接入 SQLite 与 fenced Supervisor，但
standalone CLI/C++ MCP 仍不在该 capacity/projection 边界，pending/no-record 仍不会首次派发，
也没有 heartbeat takeover、cancel、timeout 或 retry。

继续 D-003 后实现的有界 **P2.5C fenced Worker 证据投影与 late adoption** 在 SQLite v9
增加 immutable `worker_launch_attempts`、按 Supervisor 实际采样 append-only 的
`worker_attempt_observations` 以及唯一 `supervised_dispatch_adoptions`。每条写入绑定 exact
project/principal/intent/attempt/request/job、当前 active fencing term、canonical evidence hash
和关系列；JSON/关系投影分歧、历史 heartbeat replay、序号/时间/ready/terminal 回退及 stale term
均 fail closed。ready 只能与先写入的 exact heartbeat 共存，heartbeat 仍是采样证据，不是 lease
或替换授权。

固定 Dispatcher 只从 immutable intent 推导 current Adapter record；Adapter 在已有 submission
lock 下读取唯一 managed ticket/ready/heartbeat，不扫描 run root、不调用 launcher、不按 TTL
判断接管。exact ready 的既有 `launching` attempt 可幂等提升为 `launched`，随后 SQLite 在同一
fenced transaction 中写 dispatch outcome 与 adoption。Supervisor 对 `dispatching` 每轮尝试该
只读投影以关闭 late-ready 窗口；对已 `dispatched` attempt 使用独立 60 秒证据采样 cadence，原
status refresh cadence 不变，避免把 Worker 一秒 heartbeat 直接放大为每任务一秒 SQLite 写事务。
每次实际采到的新 heartbeat 仍完整持久化并保留 durable high-water。

P2.5C 没有 dispatch/launcher 接口，不 claim pending，不创建 Adapter record/job directory，
不凭 heartbeat 过期释放 kernel fence 或启动替代 Worker。升级前已经终态、或在 Supervisor 首次
扫描前已经终态的 task 不保证回填 v9 evidence，因此这些表是受 fence 的采样审计而非完整历史
重建。下一子窗口仍须实现可恢复 fenced scheduler 并证明 pending/no-record 首次派发不会重复
启动；随后才安全推进 cancel、timeout、有限 retry、`reconciliation_required` resolution 与
SSE。完整 P2 仍为 **Pending**。

完成标准：重复请求不重复建任务；控制面重启后恢复或明确终结任务；取消能到达 Worker；
已提交任务不依赖浏览器连接存活。

### P3：确定性工作流/DAG

交付：

- 节点依赖、fan-out/fan-in、CPU/GPU 资源锁；
- 节点缓存/checkpoint 和失败传播策略；
- 正演、质量检查和 FWI 等固定 Recipe；
- Guided 模式从 Recipe 生成同一种 PlanGraph。

完成标准：依赖正确、有界并行、恢复不重复已完成节点；固定流程参数和结果可追溯。

### P4：Agent Planner 与受控子 Agent

入口门槛：P0–P3 全部通过出口测试并标记 Verified；在此之前 P4 不是近期主线。

交付：

- Data Inspector、Planner、Plan Critic、Analyzer 角色；
- typed intent、缺失字段澄清、候选计划比较；
- Agent 模式生成 PlanGraph，并使用与 Guided 模式相同的批准和执行 API；
- 独立只读分析可有界并行，主 Agent 汇总。

完成标准：歧义任务先请求澄清；不存在或不兼容的算法无法进入可执行计划；未经批准无副
作用；并行子 Agent 不同时修改同一受控资源。

### P5：算法 SDK 与通用结果

交付：

- `algorithm create/validate/test/register` 开发流程；
- manifest、adapter、fixture 和安全一致性测试模板；
- ArtifactManifest 驱动的通用图片/指标/表格/文本组件；
- 接入至少一个真实去噪算法。

完成标准：新算法不修改 Orchestrator 关键词即可被发现；错误 Schema/权限/资源声明无法
注册；完成一次“合成炮集 -> 去噪 -> 质量评价 -> FWI”的真实多算法任务图。

### P6：评测、观测和安全加固

交付：

- 意图、草稿、计划、工具选择和结果分析的 golden eval；
- 重复请求、进程崩溃、断线、取消、GPU 竞争和恶意输入测试；
- 身份、任务归属、审计、预算和算法 allowlist/version pin；
- trace、metrics、日志脱敏和运行手册。

完成标准：核心恢复/审批/越权场景有自动化测试；失败不被伪装成成功；安全边界与部署文档
一致。

## 7. 风险控制与实施顺序约束

1. P0 不建立“万能科研 Schema”；先让可演进的最小契约覆盖一条真实 FWI 链路。
2. 顺序是“最小 Schema -> 最小 SQLite TaskService -> FWI Adapter -> Guided Web 闭环
   -> 取消/恢复 -> DAG -> Agent Planner”，不在无持久任务身份时先做表面 UI。
3. SQLite/Task Store 是唯一权威任务状态；Redis 状态一律视为可重建的缓存、锁、lease
   或通知，不参与“谁是真值”的恢复冲突。
4. 可复现性验收同时检查数据、参数、算法代码/环境、随机种子和硬件指纹，
   不只检查数据 hash 和参数。
5. Agent 置信度不是 Policy Gate；任何可执行计划都必须通过数据身份/权限、算法注册/版本、
   I/O 类型、Schema、资源、无环、完整性、有效批准/plan hash、副作用策略和幂等键验证。
6. P4 在 P0–P3 可靠性基线之后；不以“演示很聪明”代替真实任务可恢复性。
7. 先契约，再 UI/Planner；先持久任务内核，再扩大自主执行。
8. 每一阶段采用纵向可运行切片，不创建只有接口没有真实调用方的大量空壳。
9. 现有 Deepwave FWI 是第一条回归基线，在替代路径通过等价测试前保留旧入口。
10. 不解除通用 `JobBackend` 的 dry-run，不增加任意 shell 或 `FWI_RUN_ROOT` watcher。
11. 不先增加更多近义词规则、装饰性侧边栏或无法执行的 Agent 声明。
12. 每阶段完成必须更新进度账本、测试证据、已知限制和 Git checkpoint。

## 8. 全项目验收场景

目标端到端示例：

1. 用户从 Catalog 选择一个已验证的速度模型和炮集；
2. 输入“先检查数据质量，必要时使用已注册去噪算法，再执行二维声学 FWI，并比较处理前后
   结果”；
3. 系统生成包含输入、建议、替代方案、参数、资源和风险的 TaskDraft；
4. 用户修改或批准 PlanGraph；
5. 独立检查节点并行，依赖节点按图执行；
6. 浏览器关闭、服务重启或任务等待时状态仍可恢复；
7. 用户可以取消或重试允许的节点；
8. 最终展示 lineage、真实指标、图像、失败/警告和分析结论；
9. 未批准步骤、任意路径、未知算法或越界资源请求被明确拒绝。

## 9. 计划变更控制

- 本计划已经获批，后续 Codex 不得静默改变核心方向。
- 新建议先说明证据、收益、成本、风险和兼容性，得到用户明确批准后才更新 D-003 和本文。
- 仅执行进度、测试结果和阻塞变化时更新 `docs/PROJECT_PROGRESS.md`，不重写已批准目标。
- 当前对话中的最新明确指示始终优先。

## 10. 变更记录

| 日期 | 变更 | 来源 |
|---|---|---|
| 2026-07-15 | 收紧 P0 为最小 FWI 契约；把最小 TaskService/幂等/状态查询提前到 P1；明确 SQLite/Redis 责任、执行环境指纹、确定性 Gate 和 P4 后置 | 用户提供的六项风险评估 |
| 2026-07-15 | 增加 D-007 配置/交互维护与 P2.1 有界任务发现；不扩大到取消、重试、reconciliation 或 SSE | 用户明确报告优化器、轮询滚动和任务卡恢复三项问题并要求修复 |
| 2026-07-15 | 增加 D-008 Conversation/Task 可选引用、浏览器本地无级联删除、当前 1.4 六图结果维护与 P2.2 有界可恢复任务回收站；永久 purge 和完整 P2 继续延期 | 用户明确要求理清对话与任务、保留删除场景并恢复 FWI 图片展示 |
| 2026-07-15 | 增加 D-009/P2.3 有界回收站永久删除：SQLite 两阶段墓碑、受控本地 Worker 目录清理、强确认与无级联；完整 P2 继续延期 | 用户明确要求回收站支持删除且本地文件随之删除，并要求不要重复耗时实验 |
| 2026-07-16 | 实现 P2.4 有界启动 receipt 收养/状态追赶：安全审查后禁止 startup 首次派发，采用 bind 后 current 1.4 launched-record 只读 lookup、严格相同 handle 收敛、一次 status 追赶；fenced capacity/lease/cancel/SSE 继续延期 | 用户要求继续 D-003；沿既有 P2 reconciliation 方向推进首个可证明不重启 Worker 的子窗口 |
| 2026-07-16 | 实现 P2.5A 控制面 fenced lease/持续状态泵：SQLite v8 term/closure/commit fence、observation-only Supervisor 和 lease-before-listen 生命周期；Worker staged launch/capacity/heartbeat 继续延期 | 用户继续 D-003；先关闭浏览器连接依赖，同时保持绝不首次派发或重启 Worker 的安全边界 |
| 2026-07-16 | 实现 P2.5B 固定 Adapter 托管 Worker staged launch fence：exec-inherited submission/capacity locks、pre-import ready/heartbeat、exact adoption、post-Popen deferred 与 purge fence；SQLite Worker scheduler 继续延期 | 用户继续 D-003；沿已接受的 Worker 分阶段启动方向关闭控制器崩溃重复启动窗口，同时不扩大到 pending/no-record 首次派发 |
| 2026-07-16 | 实现 P2.5C fenced Worker 证据投影/late adoption：SQLite v9 exact attempt/heartbeat sample、active-term adoption、dispatching observation 与 dispatched 独立低频 cadence；首次派发与 lifecycle control 继续延期 | 用户继续 D-003；先让 Supervisor 在不拥有 launcher 的边界内消费 P2.5B 证据并证明 late-ready 收养不重复启动 |
