# 科研任务 Agent Runtime 进度账本

<!-- project-progress-schema: v1 -->

- 最后更新：2026-07-19
- 活跃决策：`D-001`、`D-002`、`D-003`、`D-004`、`D-006`、`D-007`、`D-008`、`D-009`、`D-010`、`D-011`、`D-012`；`D-005` 仍为 Proposed
- 最高优先级治理锁：历史 D-001～D-012 保持原状；D 的任何新增/删除/重编号/重排/正文修改，只有用户
  单独原样复制 Codex 预先给出的唯一 `D-AUTH` 句才获单次授权；普通进度只更新本账本
- 活跃分支：`feature/scientific-agent-runtime`
- 基线：`feature/fwi-deepwave-2d-acoustic@ffeb5bc`
- 总体状态：**P0、P1、P2 均已验证；P2-001～P2-009B2、reconciliation 矩阵、checkpoint /
  Waiting / same-attempt resume 与只读 SSE 均为 Implemented / Verified，P2 阶段出口已通过。
  P3 readiness、SQLite v18 node/claim、PlanGraph 1.2 typed bytes binding、SQLite v19 all-input
  binding、SQLite v20 单节点纵向内核，以及 SQLite v21 deterministic multi-node runtime 均为
  Implemented / Verified。v21 已接通 readiness→admission、fan-out/fan-in、durable descendant
  blocking、Task 聚合、active-term 重启/取消恢复及 inherited CPU/GPU `flock` 容量围栏；v22
  已验证 scope-local node cache、可信 artifact/递归 Dataset lineage 与 exact DAG same-live
  checkpoint 协作。Recipe/Guided 与阶段出口仍 Pending。P3 In progress，P4–P6 Pending；只有
  P6 出口通过才算全项目完成**
- 当前阶段：**P3 确定性 DAG In progress；阶段出口尚未满足**
- 下一动作：另开 P3 固定 Recipe/Guided 工作包；不混入公开 DAG API/UI 扩张或 P3 阶段出口，
  `dag` capability 继续保持 false
- 交付粒度：D-011 在 `0cbe131` 的全项目 P2–P6 历史粗估基线约 12 个；此后 8 个切片已
  Verified，当前滚动粗估约 4 个，P2 为 0、P3–P6 合计暂估约 4 个；均非固定配额
- 默认接续：路线切片保持上述估算；用户只说“继续 D-003”时只执行一个有界工作轮次。轮次只含
  一个主要验收目标和一个风险边界，可在切片仍 `In progress` 时交接，不新增切片或改变余量
- 默认验证：轮次内只跑受影响/失败测试；切片出口只对候选最终 tree 跑一次相关 aggregate；阶段
  出口才跑完整回归和代表性 CPU/CUDA。默认一次综合审阅，第三轮及以后须先获用户明确批准
- 当前阻塞：无
- 完整计划：`docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md`

本文是跨 Codex 会话的**执行进度真源**，但不是实时进程数据库。每个新会话必须先核对
Git、代码、测试、服务和 Task Store，再使用这里的状态。发生冲突时，实时证据优先，并在
同一变更中修正本文。

## 阶段状态

| 阶段 | 状态 | 已完成内容 | 验证证据 | 下一出口条件 |
|---|---|---|---|---|
| 准备 | Verified | D-003 计划/进度、D-004、D-005 提案、安全门和真实新会话冷启动 reconciliation | branch/diff/ancestor/helper/live tests + launcher/continuity/runtime-secret：PASS | —（阶段完成） |
| P0 最小 FWI 契约 | Verified | 七类 v1 Schema、canonical plan hash、Gate、fingerprint、状态/API/Adapter/Proto 规范、威胁模型和旧合同审计；Gate 后续补强 draft/plan 及 manifest port 一致性 | 合同当前 32/32；P0 checkpoint 回归：CTest 39/39、FWI Runner 1/1、FWI Python 27/27、Web/embedding Python 13/13、UI/governance PASS | —（阶段完成） |
| P1 最小持久垂直切片 | Verified | 既有 P1 Task Store/Registry/Adapter/atomic submit/Guided Web 全闭环及 D-006/D-007；D-008/P1-008 增加 Conversation/Task 可选引用、无级联本地对话删除和当前 Algorithm/Adapter 1.4 的 2 数值 + 6 PNG 结果画廊 | Runtime 165/165、Worker 28/28、Web 29/29、Embedding 6/6、CTest 39/39、MCP 1/1 及 UI/治理 PASS；fresh v6 CUDA 10 events、8 artifacts/6 PNG、数值更新和重启不变性 PASS | —（P1 及当前维护切片完成） |
| P2 持久可靠性加固 | Verified（阶段完成） | SQLite v17；两次串行/累计 2W finite retry；positive/exact-negative/transient/uncertain reconciliation；current Deepwave Algorithm/Adapter 1.6 same-attempt checkpoint/Waiting/resume；scope-bound 只读 RunEvent SSE 与有限 GET polling 回退 | 候选最终树完整 P2 aggregate 592/592 + Node UI PASS；真实 CPU/CUDA Guided HTTP/SSE E2E 均 Succeeded，连续游标续传、same-attempt resume、8 artifacts/6 PNG 与有限非零更新通过 | —（P2 阶段完成；下一出口为 P3） |
| P3 确定性 DAG | In progress | readiness；v18–v20 durable binding/单节点内核；v21 deterministic multi-node runtime、重启/取消恢复与 inherited CPU/GPU `flock`；v22 scope-local node cache、可信 artifact/递归 Dataset lineage、same-live DAG checkpoint 协作 | 第 1～7 轮见下；第 7 轮候选 aggregate 一次运行 520 项：509 PASS、11 个环境/selector errors；仅重跑失败入口与正确 scheduler 子集 17/17 PASS；`py_compile`/治理 PASS | 固定 Recipe/Guided 与 P3 阶段出口通过 |
| P4 Agent Planner | Pending | 无 | 无 | 澄清、计划校验、审批和子 Agent 通过 |
| P5 算法 SDK | Pending | 无 | 无 | 受控数据可匹配多个独立算法；新增真实插件无需 Orchestrator 关键词且 conformance 通过 |
| P6 评测与加固 | Pending | 无 | 无 | 安全、故障、审计和部署验收通过 |

## 滚动剩余交付估算（D-011）

这是执行估算账本，不修改 P0–P6 的固定内容、顺序或出口。当前值按同一全项目范围滚动：

| 截至 checkpoint | 范围 | 本 checkpoint 新增 Verified | 自基线累计 Verified | 本 checkpoint 用户批准调整 | 当前剩余粗估 |
|---|---|---|---|---|---|
| `0cbe131`（D-011 基线） | 当时完整 P2 余项 + P3–P6 | 0 | 0 | 无 | 约 12 个（历史“约十余个”锚点；非承诺） |
| `419c41f`（P2-009B2） | 当前完整 P2 余项 + P3–P6 | 6：P2-006、P2-007、P2-008、P2-009A、P2-009B1、P2-009B2 | 6 | 无 | 约 6 个；P2 = 2，P3–P6 合计暂估约 4；阶段内容和质量门不变 |
| 2026-07-17（reconciliation 矩阵） | 当前完整 P2 余项 + P3–P6 | 1：负向/不确定 reconciliation 矩阵 | 7 | 无 | 约 5 个；P2 = 1，P3–P6 合计暂估约 4；剩余 P2 路线切片跨两个工作轮次，阶段内容和质量门不变 |
| 2026-07-17（P2 阶段出口） | 当前完整 P2 余项 + P3–P6 | 1：SSE + 完整 P2 阶段出口 | 8 | 无 | 约 4 个；P2 = 0，P3–P6 合计暂估约 4；阶段内容和质量门不变 |

更新公式：`本期剩余 = 上期剩余 - 本 checkpoint 新增 Verified + 本 checkpoint 用户明确批准的调整`；
同时用“基线余量 - 自基线累计 Verified + 自基线累计批准调整”交叉核算，避免重复扣减。每个
Verified 交付必须在同一 checkpoint 按公式递减；不得继续展示旧基线。若结果持平或上调，或需
新增拆分/改变范围，必须先说明证据、收益、成本、风险与兼容性，并取得用户明确同意。

工作项允许状态：`Pending | In progress | Partially implemented | Implemented | Verified | Blocked`。阶段只有在所有必需
交付物达到 `Verified` 且出口测试通过后才可标记 `Completed`。方向获批、文档化、
实现和现场验证是四件不同的事。

## 当前 checkpoint

### 已确认事实

- 当前可运行基线是实验分支上的 Deepwave 二维声学 FWI MVP。
- 现有 FWI 固定白名单、参数校验、独立 Worker 和 artifact 路由是迁移时必须保护的安全边界。
- 当前通用 Orchestrator 仍以固定/单跳路由为主；P1 的历史 checkpoint 以 atomic submit 后的
  one-shot dispatch 形成最小闭环。P2-006 当前产品路径已经改为 HTTP submit 只做 prepare/Gate 与
  `Queued/pending` 原子 admission，active-term Runtime Supervisor 才能首次派发 current `1.6.0`。
  Worker 执行和同机容量仍由 P2-005B inherited `flock` 围栏；SQLite v10/v11 不是 Worker lease。
  P2-007 已提供有界 exact-attempt user cancel；P2-008 已提供有界 exact-attempt timeout；P2-009A
  已验证严格正向 receipt 证明下的有界 resolution/adoption；P2-009B1 已验证 exact stopped
  pre-running failure 的一次自动重试，P2-009B2 已验证 exact post-ready `worker_exit` 的一次自动
  重试与 current effective target 替换；positive/exact-negative/transient/uncertain reconciliation
  矩阵已关闭；same-attempt checkpoint / Waiting / resume 与只读 SSE 也已验证。P2 已通过完整
  故障回归及代表性 CPU/CUDA 阶段出口；P3 v20 只复用这些边界执行一个 dataset-root 节点。
- P3 首个有界工作轮次已验证无副作用的 deterministic DAG readiness kernel：它重新验证 PlanGraph
  Schema/canonical hash 和 exact node-state map，按稳定 topological layers 推导 runnable/waiting，
  不重复选择 active/Succeeded 节点，并只在依赖分支内传递 Failed/Cancelled/Blocked proof。它不读写
  Task Store、不持有 lease/资源锁、不创建 dispatch intent，也不决定 task-wide fail-fast 或聚合终态。
- P3 接续前的独立浏览器兼容风险已复现并关闭：服务端 current Catalog 发布 Algorithm/Adapter
  `1.6.0/1.6.0`，旧浏览器 normalizer 与八输出 Plan 投影只允许到 `1.5.0`。当前前端已接受 exact
  `1.4.0`/`1.5.0`/`1.6.0` 同版本绑定并继续拒绝 mixed binding；测试 fixture 已以 `1.6.0` 为 current，
  历史 1.4/1.5 仍可读。此轮未改变 Task Store、Adapter、派发或 DAG 执行能力。
- P3 第 2 轮以 SQLite v18 持久化 append-only 初始 Pending node facts，并在同一事务中重新计算
  readiness、绑定 current task/plan/hash/approval/node revision 与 active Supervisor term，写入可重放的
  claim candidate。same-term 并发/重放收敛；same-term reapproval 与 successor term 分别追加精确审计。
  candidate 明确 `dispatch_authorized=false`，没有 Task/RunEvent/budget/dispatch intent 写入，也未接
  Supervisor/Dispatcher/Adapter；single-node 或篡改 facts fail closed，`dag` 执行能力继续关闭。
- PlanGraph 1.2 已能 hash-bound typed upstream node/output→downstream input；SQLite v19 durable fact
  复核完整输入与 active term，SQLite v20 建立 exact 单节点 admission/receipt 内核。SQLite v21
  允许同一 Task 保存历史 exact per-node intent，在 active Supervisor term 下按稳定 node-id 顺序、
  每 Task 同时最多一个 active node 接通 readiness→binding→admission；因此 fan-out 的 B/C 串行执行，
  fan-in D 仍严格等待 B/C 均成功，跨 Task Worker 并发受全局 CPU 上限约束。
- v21 将 Failed/Cancelled 只持久阻断后代，独立分支继续；图完全收敛后原子提交 Task 聚合终态。
  task-wide cancel 后不再 admission 新节点，已有 exact active attempt 继续沿 P2 cancel 边界裁决；
  active-term 重建、terminal-won crash window、stale term/ABA/lease lost、receipt 分歧均 fail closed。
  公开 `dag` capability 仍为 false。
- SQLite v22 以 versioned canonical key 绑定 node contract、Algorithm registry/Adapter、全部输入
  content hash（node-output 另含 size/schema/media）、参数/输出合同、clean Git tree/commit、环境锁、
  Approval 与 project/principal scope。只有 exact Worker Succeeded terminal receipt、adopted fingerprint、
  immutable cache entry 与 Adapter nofollow artifact 重验同时一致才命中；hit 以 append-only RunEvent、
  cache fact 和 `Pending → Succeeded` revision 原子记录，`worker_runtime_started=false`，不创建 intent、
  admission、launch attempt 或 retry reservation。缺失 entry 只会 miss；bytes/hash/size/manifest/
  lineage/media/schema/symlink/scope 分歧均拒绝复用。控制面重启从 durable hit 重建且不重跑节点。
- DAG 控制面恢复、node cache、Worker checkpoint 是三个不同边界。v22 只允许 exact 当前 Running
  DAG node 沿既有 P2 checkpoint 进入 Waiting/resume；attempt id/number 与 Worker ticket/ready PID
  不变，不制造 D-012 attempt。跨进程 Worker checkpoint 仍未实现。可信 node-output 绑定保留
  producer semantic key、receipt/lineage hash 与 transitive Dataset roots，可从 D→C→B 重建到原始
  Dataset；当前 fixed Deepwave 执行 Gate 仍只运行 dataset-root 节点，未宣称通用自动算法流水线。
- D-006/P1-006 已把固定 FWI 的显式整数上限扩展为 10000；该 checkpoint 使用
  Algorithm/Adapter `1.1.0`。D-007 的 `1.2.0` 是不可变六参数历史快照，`1.3.0` 是已验证
  checkpoint；D-008 当时的新提交使用 `1.4.0`，D-012/B1 当时推进为 `1.5.0`，本工作轮次的
  current 新提交为 `1.6.0`；旧 `1.0.0`–`1.5.0` manifest、persisted Plan 与已 dispatch 收据保持
  精确兼容。
- D-007/P1-007 已验证 Adam/SGD、严格学习率、证据分级建议与轮询滚动保持；
  P2-001 scope-bound SQLite 任务发现/重开也已完成自动化与真实 HTTP/重启验收。
- D-008 已验证浏览器 Conversation/ScientificTask 可选引用与本地无级联对话删除；SQLite v6
  终态任务回收站保持不可变任务证据并支持恢复；当前 `1.4.0` persisted Plan 精确声明八个
  artifact（NPY + CSV + 六张固定 PNG），历史 `1.0.0`–`1.3.0` 继续精确声明两个。
- D-009/P2-003 已验证 SQLite v7 两阶段永久删除：只允许已在 Trash 的 resolved terminal
  task；受控 Adapter 根据 durable receipt 删除专属本地 Worker 目录，完成后 active/trash 不再
  列出任务，SQLite 审计历史和 conversation/message 保留，引用显示已永久删除。
- P2-004 已验证：loopback Workbench 先成功 bind 且尚未 listen，再对最多 10000 个 active task
  做 scope-bound 全分页扫描。pending 从不 claim/首次派发；dispatching 只通过固定 Adapter 的
  单一确定性 control record 做无副作用 lookup，只有 current 1.4 `launched` exact receipt 才写
  唯一 SQLite outcome。missing/preparing/launching/failed/corrupt/历史版本均 deferred，已有
  `reconciliation_required` 不重试。dispatched task 只追赶一次 status/RunEvent；事件历史跨过
  1000 条仍分页读取；单任务 status 脱敏错误/CAS 冲突不阻断其他任务，receipt 分歧仍硬失败。
  随后才 activate、发布 API 和 serve。
- P2-005A 已验证：SQLite v8 为 `(project_id, principal_id)` 保存单一当前控制面 lease、连续递增
  fencing token、append-only term/closure 和受 term 约束的 supervisor RunEvent commit。租约时间
  在取得 SQLite 写事务后采样；过期接管、ABA、时钟回退、heartbeat/release 与写入围栏均
  fail closed。Workbench 在 recovery 后、listen/publish 前取得 lease 并启动非 daemon
  Runtime Supervisor；它只扫描 active 视图，只为 intent outcome 精确为 `dispatched` 的
  Queued/Running task 调用现有 status bridge。pending/dispatching/reconciliation/missing 均
  deferred，绝不调用 launcher。lease 丢失会自我隔离且不修改 Worker 状态或容量。
- P2-005B 已验证：current 1.4 新提交先持久化唯一 launch attempt 和 managed ticket，再发布
  queued config/status；固定 launcher 同时取得 stable per-submission 执行 `flock` 与有界 capacity
  slot，并经 `pass_fds` 交给轻量 bootstrap。子进程在导入 Torch/Deepwave 前验证 ticket、锁 inode
  和 slot generation，启动独立 heartbeat thread 后写 exact ready receipt；控制器崩溃或漏写
  `spawned` 时子进程仍持锁并可自我补齐，任何 heartbeat 过期都不授权替换。Safe launcher 只在
  exact ready 后返回；Popen 后未知结果保留 `launching`，capacity/pending 不写 immutable dispatch
  outcome。startup lookup 只收养同 attempt 证据，绝不猜 PID 或扫描 run root。
- P2-005C 已验证：SQLite v9 以 intent/attempt/project/principal/current fencing term 为边界保存
  immutable Worker attempt、每次实际 Supervisor sample 的 append-only ready/heartbeat evidence 和
  唯一 late adoption。固定 Adapter observation 只读 intent 推导的 current managed record；exact
  ready 可幂等完成已有 `launching → launched`，但绝不调用 launcher、扫描 run root 或按 heartbeat
  TTL 接管。Supervisor 对 `dispatching` 每轮观察直到 exact adoption；对 `dispatched` evidence 使用
  独立 60 秒 cadence，原 status refresh cadence 不变。每次实际观察的新 heartbeat 都持久化 high-water，
  历史 replay/同序列分歧/终态回退/JSON 与关系投影分歧/stale term 均 fail closed。
- P2-006 已验证：SQLite v10 以 `(intent_id, fencing_token)` 保存 append-only supervised dispatch
  authorization，并以同事务 pending claim、active-term/时钟/scope trigger 和 exact staged projection
  约束调用资格；另以独立 audit 围栏 current 1.4 legacy private schema 1.0 exact launched receipt
  adoption。Supervisor 负责 pending 首派、dispatching/no-record 接管和 exact staged 同 attempt
  恢复；submission lock 与 inherited execution/capacity `flock` 关闭 SQLite 与文件系统之间的竞态。
  startup pass 已收敛为 lease 前只读 inventory，不再调用 Adapter 或写 outcome/status。
- P2-007 已验证：SQLite v11 以 request/authorization/outcome 三组 append-only 记录把 user cancel
  绑定到 current Algorithm/Adapter 1.4、private schema 1.1、durable dispatched intent、最新 v9
  spawned+ready+running observation 与 Worker-published capability。HTTP admission 只做只读能力
  probe 和持久化，不发布 Worker request；active Supervisor term 才投递。Worker 通过 append-only
  request/ack 自行 cooperative unwind，宽限耗尽则 exact process `os._exit(75)`，控制面从不根据
  持久 PID 发 signal。只有 ack、stopped heartbeat 与 idle execution `flock` 同时成立才提交
  Task/RunEvent `Cancelled`；自然 Succeeded/Failed 抢先则 cancellation 记为 superseded。
- P2-008 已验证：自动 wall-time enforcement 只覆盖 current 1.4/private 1.1 且能证明 v2 exact-stop
  capability 的 latest managed attempt。clock 精确从 Store 对该 current Worker 的首条 durable
  spawned+ready+running observation 的 `observed_at` 开始；deadline 到来且 active term 持久授权前
  Worker mutation 为零。自然终态在 authorization 前完成为 `not_triggered`；durable user-cancel
  admission 先赢为 `suppressed`；authorization/request 后自然终态先赢为 `superseded`；完整
  request/ack/stopped/idle proof 才是 `timed_out` 并提交 `Failed / WALL_TIME_EXCEEDED`。grace 后由
  exact Worker 自退出，控制面不 signal 持久 PID。
- P2-009A 已验证：只处理 immutable `reconciliation_required` intent 的两类 current Adapter
  `1.4.0` 严格正向证明——managed spawned + exact ready + heartbeat
  （`running`/`succeeded`/`failed`），以及 current Adapter 下 legacy-private schema `1.0` exact
  launched receipt。公共 Adapter `1.0`–`1.3`、缺失/歧义/损坏/不匹配/部分/
  launch/ticket failed 或 staged 证据均不进入 resolution，继续保持 `action_required`；terminal
  heartbeat `succeeded`/`failed` 仍是正向 receipt，认领后同一周期执行 status catch-up。只有 active
  fenced Supervisor term 可追加 `resolved` adoption 事实。既有 outcome 不被改写或删除，也不触发
  launcher、reset、refund、retry，也不根据 reconciliation 负向推断或合成 Task terminal；terminal
  receipt 只沿既有 exact status bridge 追赶。Workbench/API 当前只暴露有界 `action_required`/
  `resolved` 投影，不暴露 handle/hash/PID/path，不增加浏览器 mutation。
- D-003 已批准“双模式单任务内核、动态规划控制面 + 确定性执行面”。
- D-011 已批准：保持阶段、依赖、测试和安全质量；路线切片与有界工作轮次分离，裸“继续 D-003”
  每次只推进一个轮次。当前全项目路线切片粗估约 4、P2 为 0；轮次不进入余量公式。多算法是
  独立可选工具，自动串联不作为当前出口。
- D-012 已批准：每个新批准 Task 最多两个 append-only Worker attempt；Approval 显式绑定最坏
  资源预算，只对 exact stopped 的 pre-running launch failure 与 post-ready `worker_exit` 自动重试。
  普通数值失败、timeout、cancel、成功、损坏/分歧/不确定 reconciliation 不重试。B1/B2 均已
  Verified；effective handle、artifact、cancel、timeout 已在 B2 同一安全出口内共同迁移。
- 向前生效的 D 锁要求：变更任何编号条目前，Codex 先展示单 D 精确 diff/SHA-256 并停止；用户
  必须单独原样复制对应 `D-AUTH` 句。“同意/继续/固定/记录/修正”均无效；历史 D 正文不改。
- 2026-07-15 用户的风险评估已收紧顺序：最小 FWI Schema 先行，最小 SQLite TaskService
  提前到首个垂直切片，Redis 不作为任务事实源，P4 Agent Planner 后置。
- Git 动态快照（截至 2026-07-15）：当前实现分支基于 `ffeb5bc`；本地 `main`
  相对 `origin/main` 为 ahead 57 / behind 2。下次操作必须现场重查，不把快照当成永久事实。

### 尚未开始或尚未完成

- P1 已接入根启动器下的本机 Guided Web/API 与默认仓库外 SQLite Task Store；它无用户
  认证，只在 loopback 绑定时启用，不是容器/远程多用户部署方案；
- P1.1c 历史 checkpoint 已实现后端 submit 幂等、预算消费、durable intent 与 Queued；P2-006
  当前 submit 已改为 enqueue-only，current 1.6 pending/no-record 首派由 active Supervisor term
  负责。P2-009A 只在上述两类 exact positive receipt 下推进 immutable
  `reconciliation_required` 的有界 resolution/adoption；B1/B2 已关闭两类有限 retry；本 checkpoint
  已让 exact negative 终结为 `Failed / DISPATCH_NOT_STARTED` 且不退还 Task admission budget，
  transient/uncertain 继续保持 `action_required`；
- Deepwave Adapter 只覆盖固定 `acoustic_fwi_2d` 单节点，尚未成为通用 Algorithm SDK；旧
  forward 因输出语义不匹配而未接入标准 Adapter；
- P2-006 已让 Web 进程在 scope-level fenced term 下调度并持续更新 task，不依赖浏览器 GET；
  startup inventory 现为无 lease 的纯只读路径，浏览器单任务 GET 仍可走既有单调 status CAS，故不能
  声称所有 runtime write 都由唯一 Supervisor 独占。kernel `flock` 仍是执行/容量权威，heartbeat
  不是 lease 或 takeover 信号；standalone CLI/C++ MCP 不在该投影/容量边界，升级前或首次扫描前
  已终态的 task 不保证 evidence backfill。不完整 staging、确定性 Adapter/receipt 错误仍需未来
  reconciliation；finite retry 策略已由 D-012 接受且 P2-009B1/B2 已 Verified；same-attempt
  checkpoint / Waiting / resume 与只读 SSE 已 Verified；P3 v22 node cache/可信 lineage 与 DAG
  same-live checkpoint 协作也已 Verified，但跨进程 checkpoint restart、P3 Recipe/Guided、
  P4 Agent Planner、P5 Algorithm SDK
  与 P6 加固仍未实现。
  P2-007/P2-008 不支持 pending/staged、legacy private schema 1.0 或公共 Adapter
  1.0–1.3；P2-009A 只额外覆盖 current Adapter 下 legacy-private schema 1.0 的 exact launched receipt，
  不把公共 Adapter 1.0–1.3 纳入；
  `resources.wall_time_seconds` 的 runtime enforcement 只在 P2-008 已验证的 exact 边界内成立。
- P2-002 的普通“删除”仍只是可恢复 visibility Trash/Restore；D-009/P2-003 另提供强确认的
  本地 Worker 目录/result purge，但不硬删 Draft、Plan、Approval、RunEvent、幂等记录或
  SQLite 审计历史，也不清除备份/外部副本。服务器 transcript 永久删除仍未实现。
- 图片 GET 为执行严格安全重验会在每次请求完整 collect/解码固定图片；fresh E2E 的六图 GET
  合计 0.7527 s。单任务 visibility 历史读取随 Trash/Restore 事件数线性增长；两项均为后续
  性能加固债务，不扩展本次功能范围。

### 准备阶段与冷启动验证（2026-07-15）

- `bash tests/test_codex_project_launcher.sh`：PASS；
- `bash tests/test_project_continuity_contract.sh`：PASS；
- `bash tests/test_runtime_secret_isolation.sh`：PASS；
- `./scripts/codex-project.sh --check`：PASS；
- `git diff --check`：PASS；
- `bash tests/test_project_continuity_contract.sh` 内置的 Git-visible 禁止路径和高置信密钥/
  私钥扫描：PASS（除两处精确白名单的脱敏 C++ 安全 fixture 外无命中）。
- 首个 SSH checkpoint：`b5ac633` 已推送到 `origin/feature/scientific-agent-runtime`；
  推送后 `git rev-parse HEAD` 与 `git rev-parse @{upstream}` 一致。

本会话按 `AGENTS.md` 完整读取 continuity/plan/progress/Git policy，确认当前分支、干净工作树、
upstream 和 `ffeb5bc` ancestor，运行只读 helper，随后以代码和测试重新核对账本。真实新会话
冷启动 reconciliation 演练完成，因此准备阶段从 Implemented 更新为 Verified/Completed。

### P0.1 合同切片验证（2026-07-15）

- `contracts/scientific_runtime/v1/`：七类公共 Schema 与共享定义；顶层未知字段拒绝，只允许
  namespaced extensions；DatasetRef v1 只覆盖当前二维速度模型输入；
- `scientific_runtime_contracts/validation.py`：NFC/整数 JSON canonicalization、plan hash 和无
  存储/调度/提交副作用的 deterministic Gate 参考实现；
- `docs/architecture/SCIENTIFIC_RUNTIME_P0_CONTRACTS.md`：状态转换、版本演进、执行指纹、
  API 草案、Adapter v1、Proto 映射、威胁模型及 ExperimentSpec/AlgorithmCard/A2A/FWI 差异；
- `python3 -m unittest tests.test_scientific_runtime_contracts -v`：23/23 PASS，覆盖七类正例、
  缺/未知字段、资源/参数越界、任意路径、数据权限/hash、算法注册/allowlist/version pin、
  I/O 类型、DAG、未确认字段、幂等键、副作用、批准/plan hash、dirty provenance；
- `ctest --test-dir build --output-on-failure`：39/39 PASS；
- `ctest --test-dir mcp_server_integrated/build --output-on-failure`：1/1 PASS；
- FWI Python：26/26 PASS；Web Python：7/7 PASS；UI message、launcher、continuity、
  runtime-secret 和 `codex-project --check`：PASS。

P0 未改动 C++、现有 Python 数值路径、Web 运行时、旧 prompt 或 `FWI_RUN_ROOT` 执行边界。
这些验证证明合同和回归基线通过，不证明 P1 TaskService 或科学效果。

### P1.1a SQLite 持久基础验证（2026-07-15）

- `scientific_runtime/migrations/0001_task_store.sql` 与
  `scientific_runtime/task_store.py`：版本化 migration/checksum、WAL、外键、事务、CAS、不可变
  task/plan/approval、append-only event 和 create 幂等；数据库路径必须为私有绝对路径，读取时
  校验文档 hash 与索引身份；store 与 migration trigger 都禁止任务直接从 runtime 状态创建，
  专用 application ID、串行空库首迁移、live schema/quick/FK 校验拒绝误接管、初始化竞态或
  结构漂移，runtime status 必须有连续且匹配的事件历史，
  Git/Docker 构建上下文都排除数据库和 sidecar；
- `scientific_runtime/task_service.py`：所有读写绑定 `project_id`/`principal_id`，guided 批准者
  必须等于当前主体；验证 draft/plan、算法/资源/数据引用、DAG、事件状态、节点和执行指纹；
- `scientific_runtime_contracts/validation.py`：Gate 增加 `AwaitingApproval` 及
  task type/parameters/resources 的 draft-plan 一致性；
- 创建、查询、CAS、并发幂等、事务回滚、重启恢复、损坏检测、授权和事件原子性已由
  `tests/test_scientific_runtime_task_service.py` 33 个测试覆盖；合同当前为 27 个测试；
- 产品服务故意没有 submit/queue 方法；测试仅在隔离临时库中直接构造 post-submit 状态，
  用于验证 P1.1a 事件持久边界；P2 checkpoint/wait/retry/cancel 仍被拒绝；
- 这只使 **P1.1a Verified**。P1.1 parent 仍为 **Partially implemented**：Catalog/Registry、
  批准预算、Gate + plan + approval + submit-idempotency + `Queued` 同事务在该切片尚未实现；
  后续 P1.1b/P1.2a 已分别补齐 Registry foundation 与固定 FWI Adapter，但 P3 前首个真实 submit
  仍必须增加单 FWI 节点能力门；
- D-005 未获批；本切片未审计、迁移或删除旧 prompt-like 文件，也未改变 `FWI_RUN_ROOT`。

### P1.1b Registry 基础验证（2026-07-15）

- `0002_catalog_registry.sql` 与有序 migration loader：fresh v2、带 task/approval 的 v1→v2
  原位/并发升级、每版 name/checksum、失败全回滚和最终 schema/integrity 验证；
- `dataset_versions`/`dataset_catalog`：跨项目共享不可变 core identity、项目 access snapshot、
  精确 replay、版本共存、permission-scoped get/list、hash/index/schema 损坏 fail closed；
- `algorithm_registry`：版本固定 manifest、allowlist 索引、参数 Schema/port 校验和不可变 replay；
- `approval_budgets`：从既有/新增 approval 固化 max/tasks-used，读取时和 decision 交叉核对；
  本切片不消费预算；
- TaskService 从单 WAL read snapshot 解析注册 Dataset/Algorithm，拒绝未注册、metadata/hash/
  scope/allowlist/task type/parameter/resource/I/O/side-effect 漂移；P0 Gate 同步要求 plan port 集合
  精确匹配 manifest；
- 固定 bootstrap 复用 Worker sidecar 与 NPY/MAT 双 hash 检查，生成无服务器路径的
  `marmousi_94_288@1.0.0` DatasetRef；Deepwave manifest 是已审 metadata，但声明的标准 Adapter
  在本 P1.1b checkpoint 时尚不存在；后续 P1.2a 已实现固定反演 Adapter；
- `tests.test_scientific_runtime_registry` 22/22、TaskService 33/33、合同 28/28；完整回归见阶段表。

### P1.2a Deepwave Adapter 验证（2026-07-15）

- `scientific_runtime/fwi_adapter.py` 实现固定 `deepwave.acoustic_fwi@1.0.0` 的
  validate/estimate/submit/status/cancel/collect，只接受 `acoustic_fwi_2d` 的反演 preset；
- 首次执行必须由服务端注入精确 Registry DatasetRef snapshot，并另用固定 venv probe 验证本机
  模型/sidecar/hash 与 device；current project/principal/access scope 被纳入请求身份。默认本地
  probe 不提供产品 ACL，不能替代 TaskService/Gate；
- submit 使用 task + plan hash + node key 的跨线程/实例/进程幂等域、完整 record envelope hash、
  固定 venv/argv/最小环境和私有 run root；不扫描 `FWI_RUN_ROOT`，不创建 watcher；
- status fail closed 并脱敏 Worker 错误；cancel 稳定返回 P1 unsupported no-op；collect 通过
  openat/no-follow 复核固定 config、状态、NPY/CSV、频率/loss/metrics/runtime 后重新生成两个
  schema-valid ArtifactManifest；
- provenance 明确是 development：旧 Worker 未消费 seed、未启用 deterministic algorithms，
  安装包快照也不是可重建 lock；不声称跨环境 bitwise 可复现；
- 聚焦测试 17/17；与 Registry 22、TaskService 33、contract 28 组合 100/100。固定 venv CUDA
  一次迭代的 submit→status→collect 与跨实例 replay 通过；这是合成链路证据，不是科学效果外推；
- 详见 `docs/architecture/SCIENTIFIC_RUNTIME_P1_FWI_ADAPTER.md`。

### P1.1c 原子 submit 与一次性 dispatch 验证（2026-07-15）

- `0003_submit_dispatch.sql` 增加不可变 dispatch intent/claim/outcome 与 typed submit-idempotency
  link；fresh/v1/v2→v3 和并发升级保留 checksum/schema/integrity 核对；无法解释的旧 runtime、
  已消费预算或 submit 行会使升级整笔回滚，不伪造 intent；
- `TaskService.submit_task` 只接受 task/scope/current approval/mutation key。精确 replay 在
  expiry/budget/status/preflight 前返回；submit key 与 PlanGraph node key 严格分域；
- 单个 `BEGIN IMMEDIATE` 重读 current aggregate、Registry 和 budget，执行完整 Gate 与固定
  Marmousi/Deepwave 单节点 capability guard，原子消费预算、写 intent/idempotency、首个
  `task_queued` 并进入 `Queued`；任一步失败全部回滚；
- `DeepwaveTaskDispatcher` 只用固定代码映射。Adapter preflight 在事务前，`submit/Popen` 在
  commit 后；queued fingerprint 明确是 development preflight evidence 且无 node ID，成功
  handle 返回实际 fingerprint，第一个 node event 必须与 receipt 完全一致；
- dispatch 状态为 pending→dispatching→dispatched/reconciliation_required。P1 exact replay
  不自动重发；两个 crash window 可见但不猜测性恢复，不把 Adapter 异常擅自标成 task Failed，
  不退款、不实现 lease/retry/cancel；
- Scientific Runtime 117/117：contract 28、Registry 24、TaskService 47、Adapter 18；主 CTest
  39/39、MCP 1/1、FWI Worker 27/27、Web/embedding 13/13、UI、launcher、continuity、
  runtime-secret、`codex-project --check` 与 `git diff --check` 全部 PASS；
- 详见 `docs/architecture/SCIENTIFIC_RUNTIME_P1_SUBMIT.md`。

### P1 Guided Web 闭环验证（2026-07-15）

- `0004_workbench_runtime.sql` 为 revise/plan/approval/abandon 增加不可变 mutation ledger 和
  pre-runtime abandonment；同 key 不同请求冲突，响应丢失可精确重放，且 SQL/服务双边禁止
  把放弃草稿冒充成运行中 cancel；
- P1-005 当时的 `GuidedWorkbench` 只接收固定七字段表单（D-007 后当前为九字段），
  从 immutable Catalog 在服务端组装 Marmousi/
  Deepwave TaskDraft、资源上限、单节点 PlanGraph 与稳定 identity；修改使用 revision CAS，批准
  精确绑定 current `plan_hash`；
- 同源 `/api/scientific-runtime/v1` 执行 Host/Origin/CSRF、严格 JSON/Content-Length 和 scope
  边界；Guided API 仅在 loopback 启用，不暴露服务器路径、Adapter handle 或 Worker job ID；
- 执行型快捷按钮和自然语言 FWI 请求统一进入确认卡；批准后页面轮询真实 SQLite task，
  TaskService 将 Adapter status 单调映射为不可变 RunEvent，成功后只展示并下载经 size/SHA-256
  复核的 NPY + CSV ArtifactManifest；
- 普通 Web 聊天的 HTTP/A2A 与 gRPC bridge 固定携带 legacy-submit opt-out；同步/流式 handler
  都在 actual planner 产生 `fwi_submit_demo` 后、MCP 执行前拒绝。字段缺省仍兼容旧客户端；
  这是 loopback Web 的产品来源策略，不冒充认证；
- 最终回归：Scientific Runtime 139/139、根 CTest 39/39、MCP 1/1、FWI 27/27、
  Web/Workbench 27/27、Embedding 6/6，UI Node、launcher、continuity、runtime-secret、
  `codex-project --check`、shell syntax 和 `git diff --check` 全部 PASS；
- 当前代码使用固定 venv 走完一次 CUDA 一迭代 `Queued → Running → Succeeded`，验证 8 个
  连续事件、恰好两个 artifact 的字节数/hash、逻辑 location 和 Worker job ID 脱敏，并在
  Web 重启后使用同一 `task_id` 查到相同终态、事件和 manifest；新建私有库上还验证了
  create/revise/abandon 精确重放、CSRF 拒绝、无 CORS 和两个受控下载；HTTP sync/stream 与
  gRPC bridge 的历史绕过语句均返回 Guided、未新增 legacy job；根
  `./start.sh --no-build --grpc` 实际启动及健康检查 PASS。
  临时 task/job ID 不写入本账本；
- 详见 `docs/architecture/SCIENTIFIC_RUNTIME_P1_GUIDED_WEB.md`。

### P1-006 / D-006 高迭代上限验证（2026-07-15）

- Contract、Registry、Workbench、Adapter、Worker、MCP、C++ planner 与 Web UI 的当前反演
  边界统一为严格整数 `1..10000`；`10001`、小数、负数、布尔值和服务端字符串均拒绝，
  smoke/demo 默认仍为 2/5 次；
- 不原地修改 `deepwave.acoustic_fwi@1.0.0` / Adapter `1.0.0`。新提交固定使用
  Algorithm/Adapter `1.1.0`；MCP `fwi-runner` 公开合同同步为 `1.1.0`。读取端仅接受
  Algorithm/Adapter/fingerprint 的 `1.0↔1.0` 或 `1.1↔1.1` 绑定，旧版不能用于新 dispatch；
- 现有仓库外 SQLite 经根启动器重开后同时保留 `1.0.0`（max=100）并注册 `1.1.0`
  （max=10000）；旧文档/hash 未变，已有两个旧 dispatched 成功任务的状态及两组 artifact
  均可通过当前 HTTP/TaskService 链路读取，且没有重新 dispatch；
- 真实 HTTP 接受 10000 并只创建 `AwaitingApproval` Draft/Plan；现场随后放弃为 `Cancelled`，
  全程没有新增 dispatch 或 Worker job。10001 返回 422 且没有创建 task/dispatch/job；
- Scientific Runtime 143/143（Contract 28、Registry 27、TaskService 55、Adapter 19、Workbench
  14）、根 CTest 39/39、MCP 1/1、FWI Worker 27/27、Web/Workbench 27/27、Embedding 6/6、
  UI Node、launcher、continuity、runtime-secret、`codex-project --check`、shell syntax 与
  `git diff --check` 全部 PASS；
- 没有执行真实 10000 次数值回归。Worker 当前每轮原子重写并 `fsync` 累计 `loss.csv`，高次数
  存在 O(N²) 写放大；7200 秒只是资源策略上限，不是 runtime timeout。P1 仍没有运行中取消、
  timeout、checkpoint、retry 或 reconciliation，10000 不构成性能或完成时间承诺。

### P1-007 / D-007 优化器与非干扰轮询（2026-07-15，历史 checkpoint）

- 当前新 Guided 任务使用 contract minor `1.1.0` 的六参数 Draft/Plan 和
  Algorithm/Adapter `deepwave.acoustic_fwi@1.3.0`/`1.3.0`。`1.2.0` 保留为不可变
  六参数历史快照，并与旧 `1.0.0`/`1.1.0` 注册和 dispatched receipt 一起保持
  严格读兼容；
- D-007 的 `1.3.0` manifest 只广告 `acoustic_fwi_2d` 与 `fwi_smoke|fwi_demo`，将
  iterations 固定为 `1..10000`、seed 固定为 `0..2147483647`，并以 optimizer
  条件 Schema 分别约束 Adam/SGD 学习率；不把 legacy Worker/MCP `forward` 冒充为
  标准 Adapter capability；
- Web/API 允许 `adam|sgd` 和最多三位小数的学习率字符串，按优化器分别限定
  Adam `0.1..100`、SGD `100000..1000000000`；持久 Draft/Plan 用整数
  `learning_rate_milli`，不向 canonical hash 引入 JSON float；
- `/v1` create/revise 继续接受精确历史七个 form 字段（revise 另要求 `expected_revision`）；
  同 key 升级重放先以不可变 1.0/1.1 composer 重建候选，并对 scope/operation/key/request hash
  做 exact durable match，未命中旧记录才补 Adam/LR 10 并走当时的 `1.3.0` 六参数 Draft/Plan。
  D-007 浏览器发送九个 form 字段，部分/混合 optimizer 与 learning-rate shape 返回 422，同 key
  不同 payload 冲突。该 wire compatibility 不开放
  `1.0.0`/`1.1.0`/`1.2.0` Algorithm 的新 dispatch；
- 建议卡把 Adam LR=10 标为固定 Marmousi 已验证基线，Adam LR=2 标为微型 CPU
  finite-update 的保守检查；SGD LR=10000000 已通过固定 Marmousi CUDA 两步
  finite/model-update 校准，但仍标为实验起点而非收敛推荐；
  `gradient_clip_quantile=0.98` 作为当前版本固定值展示，本轮不可编辑；
- Guided 状态/artifact 轮询重绘已区分“原本在底部”和“正在阅读上方”：前者继续
  跟随，后者保留 `scrollTop`；只有显式打开/重开才一次性展示卡片；
- 自然语言请求未明确写 CUDA/GPU 时安全默认为 CPU，页面显式提示且不会在任务
  创建或运行后自动切换设备；已持久 task 的 device 是 plan 身份的一部分；
- 自动化证据已通过：Scientific Runtime 控制面 157/157、固定 venv Worker 27/27、
  Web Python 29/29、Node UI 行为和 `git diff --check` PASS。真实 loopback HTTP 中，非法
  optimizer/学习率均 422 且 task 数不变；当时的 1.3 CUDA/SGD 两步任务从 Queued/Running
  到 Succeeded，两个 artifact 字节与 SHA-256、NaN/Inf=0、非零模型更新及约 713.3 MiB
  GPU 峰值均通过，因此本项为 **Verified**。

### P2-001 持久任务发现与重开（2026-07-15）

- SQLite v5 增加 `(project_id, principal_id, created_at, task_id)` 索引；TaskStore/TaskService
  使用不受 progress 更新扰动的 immutable-creation-order keyset 分页，cursor 必须在同一
  scope 内解析；
- `GET /api/scientific-runtime/v1/tasks?limit=20[&cursor=...]` 只返回受限摘要，不触发
  Adapter status refresh；Web 左栏页面加载时从 SQLite 发现任务，可刷新、加载更多并打开
  单任务；
- 卡片 `×` 只关闭当前视图，不发送 cancel 也不从 SQLite 删除；重开 running task
  后继续使用既有单任务 GET 和轮询。重开 approval-persisted/submit-unconfirmed 任务时如果
  缺少原 Idempotency-Key，只读 fail closed，不生成新 key 重发；
- 控制面、Web 与 Node UI 自动化已在上述 157/157、29/29 与 UI PASS 中覆盖；Node
  额外遍历 125 条任务的 7 个页面，所有条目均可见且末页不越过 cursor。真实 HTTP 从列表
  重开旧 Algorithm 1.1 的 500 次 CPU 任务为 Succeeded、读取两个 artifact，并在页面重载
  后重新发现/重开当前任务，因此 P2-001 为 **Verified**。

### P1-008 / D-008 对话—任务分离与标准六图结果（2026-07-15）

- 浏览器对话存储升级为 schema v3；Conversation 可以没有任务或引用多个任务，引用只持久化
  `taskId/linkedAt`，任务状态、进度和结果从 SQLite/API 刷新。执行型消息先留在对话并打开
  独立草稿，不在消息发送时伪称任务已创建/运行；快捷任务可保持不关联。
- 删除对话只删除当前浏览器副本，带确认和持久化失败回滚，不取消、不隐藏、不回收 SQLite
  任务；服务器 transcript 仍按既有 TTL 管理，当前没有永久删除 capability。
- 新 Algorithm/Adapter `deepwave.acoustic_fwi@1.4.0`/`1.4.0` 在持久 Plan 中精确声明八个
  输出：反演模型 NPY、loss CSV 和六张固定尺寸 PNG。Adapter 固定路径 no-follow 读取、完整
  PNG 解码并复核 mode/dimensions/size/hash；Web 只从 task-scoped artifact endpoint 顺序加载
  Blob，单图失败不遮蔽其他结果，轮询/图片加载保持阅读位置并回收旧 Object URL。
- 历史 `1.0.0`–`1.3.0` 的 Plan/receipt 保持不可变并严格要求两个标准 artifact；读取端按
  persisted Plan 校验输出集合、端口、顺序、类型和图片元数据，不用当前 Registry 反向解释
  历史任务。
- 本项为 **Verified**。自动化结果为 Scientific Runtime 165/165，其中新增 `1.2.0`/`1.3.0`
  optimizer-aware create/revise 响应丢失后的 exact replay 测试；固定 venv FWI Worker 28/28、
  Web 29/29、Embedding 6/6、根 CTest 39/39、MCP 1/1；Node UI、launcher、
  continuity、runtime-secret、`codex-project --check`、shell syntax 和 `git diff --check`
  全部 PASS。
- fresh 私有 v6 SQLite 的真实 CUDA E2E 使用 1.4/Adam/LR=10/2 iterations，在 NVIDIA
  GeForce RTX 4070 Laptop GPU 上由 `Queued → Running → Succeeded`，产生 10 个连续事件和
  精确 8 个 artifact/6 张 PNG；Worker elapsed 5.42712 s，loss
  0.0340023860 → 0.0232094708、模型相对 L2 更新 0.0037406076、NaN/Inf=0。manifest collect
  0.1509 s，六图 GET 合计 0.7527 s，全部八项 GET 合计 1.0156 s；v6 migration checksum
  与当前源码一致。

### P2-002 / D-008 可恢复任务回收站（2026-07-15）

- SQLite v6 增加 append-only visibility events、不可变 idempotency mutation ledger 与
  active/trash projection；列表 cursor 与视图绑定，操作同时绑定 project/principal scope、
  expected visibility revision 和 idempotency key。
- 只有具有 pre-runtime abandonment 证据且没有 dispatch intent 的 `Cancelled`，或具有
  dispatched outcome 证据的 `Succeeded|Failed|Cancelled`，才能移入回收站；运行中、未确认
  dispatch、outcome unknown 与 reconciliation 状态拒绝。待批准草稿必须先走既有 abandon。
- Trash 不物理删除任务历史或 artifact；详情/事件/结果保持可读，Restore 不重跑 Worker。
  Conversation 引用/删除与任务 visibility 操作互不级联。
- 本项为 **Verified**。上述 fresh 任务的 active/trash 列表切换、trash 后 artifact 读取、
  visibility revision `0 → 1 → 2` 和 Restore 不重跑均通过；`AwaitingApproval` 直接 Trash
  返回 409，先 abandon 为 `Cancelled` 后可 Trash。同库重启后 task/event/artifact 三类
  fingerprint 与 8 个文件的 bytes/hash 均不变；现场验收结束后相关服务已清理，未记录临时
  PID 或任务标识。完整 P2 仍未完成。

### P2-003 / D-009 回收站永久删除本地结果（2026-07-15）

- SQLite v7 增加 append-only purge request、idempotency alias 与 outcome。请求精确绑定
  project/principal、当前 trash visibility revision 和幂等内容；request 先提交并立即禁止
  Restore、status/event/artifact 用户读取，outcome 后提交，完成任务从 active/trash 隐藏。
- 有 dispatch receipt 的终态任务通过固定 Dispatcher/Adapter 清理。Adapter 在 submission lock
  下验证 succeeded/failed 与 handle/private receipt，写 `launched → purging` 后用 FD-relative、
  no-follow 递归删除固定 job 目录，再写 `purged`；崩溃重试继续同一 purge。pre-runtime abandon
  没有 Worker 目录时记录 `not_created`，不扫描 run root。
- Web 回收站同时显示“恢复”和红色“永久删除”。永久删除要求输入完整 task ID；pending 禁止
  恢复并显示“继续永久删除”。成功会释放 Blob URL、从任务索引移除；conversation/message
  不级联删除，已有引用显示“任务已永久删除”并可单独移除。
- 本项为 **Verified**：Scientific Runtime 180/180，含 SQLite v6→v7、Service/Workbench、
  Adapter 临时目录删除/并发/符号链接/崩溃恢复；Web Python 29/29、Node UI 与治理检查 PASS。
  按用户要求没有启动真实 Worker、CUDA 或重复 FWI 实验。SQLite 既有不可变任务审计历史和
  Adapter 最小 control receipt/lock 保留；完整 P2 仍未完成。

### P2-004 有界启动 receipt 收养与状态追赶（2026-07-16）

- `TaskService.recover_runtime_on_startup` 先使用既有 active task keyset API 完成全 scope
  分页预扫描，默认/硬上限为 10000；超过上限在任何 SQLite 恢复写入前拒绝。它不接受浏览器
  路径、Worker job ID 或新幂等键，也不重新消费 approval budget。
- startup pass 从不 claim/首次派发 `pending`。对 `dispatching` 也不调用普通 submit/dispatch，
  只让固定 Dispatcher 通过 intent 推导单一 Adapter control record，在既有 flock 下执行只读
  `lookup_existing_handle`。只有 current 1.4 私有记录已经 durable `launched` 且 request/hash/
  task/node/plan/Algorithm/Adapter/fingerprint 全部精确匹配时，才收养 handle 并写唯一
  `dispatched` outcome；并发调用只接受相同 handle 收敛，不同 handle 硬失败。
- missing record、`preparing`、`launching`、`failed`、损坏/符号链接记录及历史 dispatching 版本
  全部保持原状态并以脱敏 code deferred；不会因启动容量不足新增 immutable
  `reconciliation_required`。已有 `reconciliation_required` 仍只读、不重试。lookup 不执行
  readiness probe、不创建 job 目录、不扫描 `FWI_RUN_ROOT`、不猜 PID、也不调用 launcher。
- 对已有/刚收养的 `dispatched` task 启动时只调用一次现有 status bridge；Worker 在停机期间已
  终态时按既有单调规则追赶：Queued→Succeeded 补 `node_started`/`node_succeeded`，
  Queued→Failed 直接写 `node_failed`，Running 再进入对应终态。单任务 Adapter status 错误或
  status CAS 冲突只以脱敏 code 进入 recovery result 并继续其他任务；receipt/outcome 分歧仍
  fail closed 并中止启动。事件历史超过 1000 条时
  先从 Store 固定 high-water 再分页取得正确 sequence，单任务扫描硬上限为 100000，超限以
  path-free code deferred。Task Store 损坏仍使启动失败。
- Web 先成功 bind TCP socket 但不 listen，再构造 Host/Origin/CSRF 边界并执行 recovery；成功后
  才 activate socket、发布 API 并 serve。忙端口执行零 recovery，recovery 失败关闭 socket 且
  API 保持 `None`。成功 recovery 的 summary 日志只记录数量和稳定 code，不记录
  task/project/principal/path。session
  明示 `startup_receipt_recovery=true`、`startup_status_catchup=true`、
  `startup_dispatch_recovery=false` 与 `automatic_reconciliation=false`。
- 本项为 **Verified**：TaskService 80/80、Adapter 27/27、Scientific Runtime 201/201、Web
  36/36、Worker 28/28、Embedding 6/6、CTest 39/39、MCP 1/1、Node UI 与治理检查 PASS。覆盖
  三项以上 pending 零 claim/零 dispatch、
  dispatching no-record deferred、真实固定 Adapter launched-lost-receipt 零重启 Worker 收养、
  malformed/divergent receipt、重复/并发启动、51 task 跨页、scope/limit、1000+ events、status
  错误继续、终态追赶、bind 顺序/忙端口/失败清理与脱敏 summary。没有运行真实 FWI/CUDA。
- 以上是 P2-004 当时的生命周期证据。P2-006 已把 lease 前 startup pass 收敛为纯只读 inventory，
  receipt adoption/status catch-up 移到 active Supervisor term 内；旧证据保留但不再描述当前启动写路径。

### P2-005A 控制面 fenced lease 与持续状态泵（2026-07-16）

- SQLite v8 增加 scope-level `runtime_supervisor_terms`、当前 lease projection、append-only
  term closure 和 supervised RunEvent commit audit。`BEGIN IMMEDIATE` 内采样数据库操作时间，
  同 owner replay、外部持有者、精确到期接管、释放后新 term、时钟回退与 ABA 均使用
  `(project_id, principal_id, fencing_token, owner_id)` fail closed；fencing token 只能连续递增。
- Supervisor status commit 在一个事务内确认当前 term 尚未关闭且写入时间位于 heartbeat/expiry
  窗口，非延长地推进活动水位，再原子写 RunEvent、task projection 和监督审计。旧 term、过期
  term 或接管后的迟到写入统一拒绝。startup recovery、浏览器 GET 等既有无租约路径继续走
  单调 CAS，故本切片不把控制面 lease 误报为所有状态写入的全局独占锁。
- `RuntimeSupervisor` 构造无副作用；`start()` 取得 scope lease 后，由一个非 daemon 线程先完成
  heartbeat/ready，再进入循环。每轮先对 active task 做有界全分页预扫描，超过 10000 在任何 status
  observation 前拒绝；只为 Queued/Running 且 durable dispatch outcome 精确为 `dispatched` 的
  task 调用现有 status bridge。pending、dispatching、`reconciliation_required` 与 missing
  deferred，终态直接跳过、不观察；模块没有 dispatch/launcher 接口，不扫描 `FWI_RUN_ROOT`，
  也不猜 Worker PID。
  稳定的单任务 status 错误相互隔离；Task Store/未知错误或 lease loss 使循环自我隔离。
- Web 生命周期为 bind → startup recovery → supervisor acquire/ready → listen → publish → serve。
  另一进程持有同 scope lease 时在监听/发布前失败。关闭先钝化 SIGINT/SIGTERM 并关闭 listener，
  再 cooperative stop/release supervisor、定界等待已有 Handler、最后取消 API 发布和恢复信号。
  任意底层 I/O 仍可能超过进程内 join；Handler/Supervisor 均非 daemon，外层 `stop_system.sh`
  给予 Web 30 秒后才 KILL，lease expiry 是崩溃后的接管边界，不能描述为无条件优雅退出。
- session capability 明示 `continuous_status_supervision=true` 与 `supervisor_leases=true`；既有
  `startup_dispatch_recovery=false`、`automatic_reconciliation=false` 等能力保持不变。这两个
  true 仅表示 Web 控制面的 observation-only 状态泵及其写入 fence，不表示 Worker
  capacity/attempt lease、Worker heartbeat、首次派发或自动 reconciliation。
- 本项为 **Verified**：TaskService 80/80、Adapter 27/27、Scientific Runtime 226/226、Worker
  28/28、Web 45/45、Embedding 6/6、CTest 39/39、MCP 1/1、Node UI 与治理检查 PASS。新增专项
  覆盖 SQLite v8 migration/trigger、并发 acquire、精确 expiry、时钟回退/事务等待、stale ABA、
  supervised commit 原子围栏、Supervisor empty/多页/error/self-fence、Web 启停/信号/零请求与
  bounded drain。按切片范围未运行真实 FWI/CUDA，也未启动或重新启动任何 Worker。

### P2-005B 固定 Adapter 托管 Worker staged launch fence（2026-07-16）

- current 1.4 私有 submission record 升为向后可读的 control schema `1.1.0`，绑定随机唯一
  `attempt_id`、attempt number、request/job/submission 和 binding hash。Adapter 创建私有 job
  目录后先写 managed launch ticket，再写 queued config/status；旧 CLI 因存在私有 sidecar 而
  拒绝复用，固定 bootstrap 才能以 `managed_launch` 进入。current 1.4 的历史 private schema
  `1.0.0` 仍可重开、status、8-artifact collect 与 purge，Algorithm/Adapter identity 未改变。
- `ParentLaunchLease` 在私有 control tree 中取得 stable per-submission execution `flock`，并从
  固定、持久化策略的 capacity slots 中取得一个 generation-bound `flock`。两个 FD 使用
  `close_fds=True`/`pass_fds` 跨 `Popen/exec` 交给 Worker；父进程只关闭自己的副本，不执行会
  释放子进程 lease 的 unlock。永久 lock inode 有独立 identity receipt，替换、policy 分歧、
  N+1 capacity 与同 submission 重叠均 fail closed。
- 纯标准库 bootstrap 在导入数值包前验证 exact ticket、两个 inherited FD、lock inode、slot/
  generation 和 PID，随后先写 heartbeat、启动非 daemon heartbeat thread、再写 immutable ready
  receipt，最后才导入和调用 Worker。子进程能在父进程 Popen 后崩溃窗口自行完成
  `leased → spawned`；FD 在验证后改为 non-inheritable。heartbeat 是绑定/健康证据，不是替换
  授权；只有内核锁释放才表示执行权与容量真正空闲。
- Safe launcher 只在同 attempt ready + heartbeat 全部精确匹配后返回；pre-ready 已确认退出可
  记为失败，任何无法确认退出的 post-Popen 结果保持 `launching` 并以
  `SUBMISSION_LAUNCH_PENDING` deferred。容量不足同样保持可恢复 dispatch intent，不写 immutable
  outcome。startup receipt recovery 可把已 ready 的 exact `launching` record 提升并收养，
  replacement launcher 调用数保持零；旧 `SUBMISSION_RECONCILIATION_REQUIRED` 语义不被改写。
- purge 对 schema 1.1 receipt 必须取得并在删除全程持有空闲 execution fence；终态 status 先于
  heartbeat 结束时会返回 `PURGE_WORKER_STILL_ACTIVE`。控制 JSON 原子写的临时文件位于私有
  control tree，证据读取/写入不重建已删除 job 目录。Web artifact route 对所有 decoded hidden
  component 返回 403，private launch/ready/heartbeat sidecar 不可下载。
- 本项为 **Verified**：TaskService 81/81、Adapter 32/32、Scientific Runtime 234/234，另有
  launch-control 8/8；Worker 29/29、Web 46/46、Embedding 6/6、CTest 39/39、MCP 1/1、Node UI、
  continuity/launcher/runtime-secret/helper 与 `git diff --check` 均 PASS。专项覆盖真实
  `Popen/exec/pass_fds`、父进程漏写 spawned、cross-process N+1/同 submission、0755 默认 root、
  ready/heartbeat 篡改、timeout ambiguity、abort cleanup、active purge、legacy CLI gate 和 Web
  literal/encoded GET/HEAD。只运行缺失配置的轻量真实 bootstrap，没有启动数值 FWI/CUDA 实验。
  本切片不等于 TaskStore Worker lease/scheduler，不覆盖 standalone CLI/C++ MCP capacity，也未
  实现 heartbeat TTL takeover、pending/no-record 首次派发、cancel/timeout/retry 或 SSE。

### P2-005C fenced Worker 证据投影与 late adoption（2026-07-16）

- SQLite v9 新增 `worker_launch_attempts`、`worker_attempt_observations` 和
  `supervised_dispatch_adoptions`。attempt 绑定 immutable intent/task/scope/submission/job/request/
  binding；每条 observation 绑定 exact active Supervisor term、事务内采样时间、canonical evidence
  JSON/hash 与展开关系列。append-only/连续序列/active-term/最新状态 trigger 与 Store 双重校验
  ticket、capacity generation/PID、ready、heartbeat sequence/time/state 和 terminal 单调性；只允许
  latest exact replay。JSON/hash/关系列分叉作为 corruption 拒绝，adoption 必须引用 ready + heartbeat
  observation 并与唯一 dispatched outcome 同事务提交。
- `read_worker_attempt_evidence` 提供无本地路径的 exact snapshot；staged/spawned ticket、ready 和
  heartbeat 全部重新校验 binding/hash/time/worker identity。Worker 的启动顺序保证 heartbeat 先于
  ready，因此 ready-without-heartbeat fail closed；failed ticket 只允许空 capacity identity 或合法
  slot/generation pair。heartbeat 新鲜度仍不授权替换，P2-005B 的 kernel locks 继续是执行与容量
  权威。
- 固定 Dispatcher 的 observation API 只接受 immutable intent/current Adapter version，并从 intent
  推导唯一 submission。Adapter 在已存在的 submission lock 内读取 current private schema 1.1；
  missing/preparing/legacy evidence 以稳定 code deferred，损坏证据 fail closed。exact started 的
  `launching` record 可幂等提升为 `launched` 并返回同一 handle；该路径不调用 launcher、不创建
  job directory、不扫描 run root、不猜 PID，也没有 TTL/takeover/retry。
- `TaskService.project_worker_attempt` 在固定 receipt 验证后把 evidence/optional handle 交给 Store；
  stale/expired term 零 SQLite 写入。Runtime Supervisor 现在对 active Queued/Running 的
  `dispatching` intent 每轮观察，以便 exact late-ready 在同周期被 adoption 后继续 status refresh；
  `dispatched` evidence 使用独立 60 秒 cadence，而 status refresh 仍按原 poll cadence。投影错误在
  dispatched task 上与 status refresh 隔离，dispatching 不会因此调用普通 dispatch 或 launcher。
  实际采到的每个新 heartbeat 仍完整 append，从而保留 durable high-water；Store 不再借每任务
  sample 高频更新 Supervisor lease。
- 本项为 **Verified**：TaskService 82/82、Adapter + launch-control 44/44、Scientific Runtime
  241/241、Worker 非数值 26/26、Web 46/46、Embedding 6/6、CTest 39/39、MCP 1/1、Node UI、
  continuity/launcher/runtime-secret/helper 与 diff check 均 PASS。覆盖 v8 active lease 原位升级
  v9、stale term、staged/exact ready adoption、latest replay、heartbeat advance/回退/同序列分歧、
  terminal guard、legacy evidence unavailable、missing preparing 零重建、低频 projection 与
  same-cycle status。按既定边界未运行 Deepwave 数值 FWI/CUDA。
- 本切片仍不是 scheduler：pending/no-record 不 claim/首次派发；没有 cancel 到 Worker、timeout、
  有限 retry、`reconciliation_required` resolution 或 SSE。v9 表只记录 Supervisor 实际 sample，
  不保证为升级前/首次扫描前已终态任务 backfill 完整 Worker 历史；60 秒运行中采样仍会随超长
  作业增长，后续 scheduler/retention 设计需继续做容量验收。

### P2-006 可恢复 fenced scheduler 与受监督首次派发（2026-07-16）

- 当前 `TaskService.submit_task` 只执行 side-effect-free Adapter prepare、确定性 Gate 和 SQLite
  `Queued/pending` 原子 admission；不再由 HTTP/Workbench 请求线程 claim 或启动 Worker，正常响应
  为 `dispatch_attempted=false`。精确 submit replay 也不触发 launcher。session 当前明示
  `supervised_runtime_scheduling=true`，三个 startup recovery/catch-up capability 均为 false。
- SQLite v10 新增 append-only `supervised_dispatch_attempts`。pending claim 与
  `pending_first_dispatch` authorization 在同一个 `BEGIN IMMEDIATE` 中提交；后续只允许
  `dispatching_no_record_takeover` 或 `staged_attempt_resume`。每次写入重读 exact active term、scope、
  task/purge/outcome 与事务内时钟；同 term 精确 replay，stale/expired/released/ABA term 零写。
  authorization 不是 Worker/capacity lease，内核 `flock` 仍是执行副作用权威。
- Runtime Supervisor 只为 current managed Adapter/Algorithm 1.4 调度 pending/dispatching。它先做
  v9 exact Worker projection：ready receipt 直接 fenced adoption；已有 leased/spawned/failed/corrupt
  evidence 不调用 launcher；只有 exact no-record 或 `staged + no slot/PID/ready/heartbeat` 才进入
  Adapter 的 lock-protected ensure。Adapter 在 submission lock 内再次确认；容量满或 lock busy
  deferred，之后复用同一 attempt/job。`preparing` 与尚未取得 launch lease 的 `launching` exact
  staged attempt 可恢复；一旦 ticket 已 leased/spawned，绝不二次 `Popen`。
- current Algorithm/Adapter 1.4 使用 legacy private control schema 1.0 时没有 v9 managed evidence。
  Dispatcher 因而提供只读、submission-lock 内的 exact launched receipt proof（schema + record hash +
  handle）；Store 在 active term 下原子写 outcome 与独立 append-only adoption audit，零 launcher、零
  虚构 Worker attempt。历史 Algorithm/Adapter identity 1.0–1.3 仍保持 deferred，不进入此旁路。
- `recover_runtime_on_startup` 现在只在 bind 后、lease 前做最多 10000 active task 的 scope-bound
  read-only inventory：不调用 Adapter、不 claim/adopt、不 status catch-up、不写 runtime。Supervisor
  acquire/ready 后才执行首次派发、late/private receipt adoption 与 status pump；浏览器仍只用 GET
  poll，连接存活不是任务执行条件。
- 故障测试覆盖 authorization/audit 的故障注入原子回滚、并发单 claim、same-term replay、exact
  expiry/new-term takeover、跨 term staged resume、真实 capacity-deferred 状态转换、capacity reset
  崩溃留下的 exact `launching/staged` 恢复、Popen 后 ambiguity 零 replacement、lost projection 后
  exact adoption，以及 current 1.4 private schema 1.0 fenced adoption。Scientific Runtime 251/251、
  固定 venv Worker 非数值 + launch-control 37/37、Web 46/46、Embedding 6/6、CTest 39/39、
  MCP 1/1、Node UI 与治理回归均通过；数值 Worker/依赖/数据/规范化配置未改，只运行轻量真实
  exec，按 D-011 未重复 CUDA。
- 本切片不修复不完整 staging（缺 job dir/ticket/config/status 或内容分歧），也不把确定性 launch/
  receipt 错误自动写成已解决状态；这些保持 fail-closed，等待 `reconciliation_required` resolution。
  P2-007 已在下一切片关闭 exact current managed attempt 的 user cancel；timeout、有限 task retry、
  SSE、standalone CLI/C++ MCP capacity 和通用 Algorithm scheduler 仍 Pending，因此完整 P2 未完成。

### P2-007 exact-attempt user cancellation（2026-07-16）

- SQLite v11 新增 append-only `task_cancel_requests`、`supervised_cancel_attempts` 与
  `task_cancel_outcomes`。admission 在同一事务内绑定 current Algorithm/Adapter `1.4.0`、private
  schema `1.1.0`、durable `dispatched` intent、最新 v9 `spawned + ready + running` exact attempt 和
  Worker 已发布的 capability；同一任务只允许一个 immutable request，精确 idempotency replay
  返回原记录，stale/expired/released/ABA Supervisor term 不能交付或提交 outcome。
- HTTP `POST /api/scientific-runtime/v1/tasks/{task_id}/cancel` 只接受
  `{"reason":"user_requested"}`，复用同源、CSRF 与 Idempotency-Key 边界。请求线程只做 Adapter
  capability 的只读 probe 和 durable admission，不发布 Worker request、不读取持久 PID、更不发送
  signal；请求期 Task 继续保持 `Queued/Running`。
- active Runtime Supervisor term 先于普通 scheduling/projection/status 处理取消，并只向 exact
  attempt 发布 append-only request。Worker 验证 inherited submission/execution/capacity fences 后
  自行 ack，在数值安全点 cooperative unwind；宽限耗尽时由该 exact process `os._exit(75)`，控制面
  没有 PID kill 路径。Adapter/Store 双重验证 request/capability/ack/receipt 哈希链和终态 proof。
- 只有 exact acknowledgement、`stopped` heartbeat 与 idle execution `flock` 同时成立，Supervisor
  才原子提交 `task_cancelled` RunEvent、Task `Cancelled` 和 cancellation outcome。自然
  `Succeeded/Failed` 先完成时保留其终态并把取消记为 `superseded`；二者的竞争只允许一个 durable
  winner，proof 的 terminal status 必须与该 winner 一致。
- Guided Web 只在详情页 exact probe 成功时显示取消能力，并区分 `requested`、`cancelled`、
  `superseded`；未知响应保留原 Idempotency-Key，只允许用户显式重试。关闭任务视图、放弃待批准
  草稿、移入回收站和永久删除都不会冒充 runtime cancel。列表查询继续保持 SQLite-only，避免
  N 次文件系统 probe。
- 本项为 **Verified**：Scientific Runtime 271/271、固定 venv Worker 31/31（含 CPU Deepwave
  smoke 3/3）与 launch-control 17/17、Web 46/46、Embedding 6/6、根 CTest 39/39、MCP 1/1、
  Node UI、continuity/launcher/runtime-secret/helper、shell syntax、`py_compile` 与 diff check
  均 PASS。覆盖空/跨 request/跨终态 proof、direct-SQL bypass、历史/latest attempt、stale term、
  取消与自然终态竞争、宽限后 Worker 自退出、未知 HTTP 响应及 v10→v11 原位升级；本切片运行
  CPU 数值 smoke，未运行 CUDA。
- 本切片不支持 dispatch pending、staged attempt、legacy private schema `1.0.0`、公共
  Adapter/Algorithm `1.0.0`–`1.3.0` 或历史 attempt；也不把 `resources.wall_time_seconds` 当作
  runtime timeout。最终分级回归证据见本页末尾 P2-007 checkpoint 行。

### P2-008 exact-attempt wall-time timeout（2026-07-16）

- 状态：**Implemented / Verified（有界 P2-008）；完整 P2 仍 Pending**。用户明确接受三项语义：timeout 终态固定为
  `Failed / WALL_TIME_EXCEEDED`，不得冒充用户取消；clock 从 exact attempt 首条 SQLite durable
  `spawned + ready + running` observation 的可信 `observed_at` 开始；cooperative grace 耗尽后由
  exact Worker 自行退出，控制面绝不根据持久 PID 发送 signal。
- 本切片限定 current Algorithm/Adapter `1.4.0`、private schema `1.1.0`、durable dispatched、latest
  managed attempt 和独立 v2 exact-stop capability。旧 Worker、历史版本、pending/staged 或证据不全
  全部 fail-closed；不升级公共 Algorithm/Adapter 或既有 private submission 版本。
- SQLite v12 保存 immutable timeout window、active-term delivery authorization 与 terminal outcome。
  window 只有 capability proof 成立后才 arm；clock 精确从 Store 对该 current Worker 的首条 durable
  `spawned + ready + running` observation 的 `observed_at` 开始。deadline 到来且 active term 持久化
  authorization 前 Worker mutation 为零；armed 状态仍允许 user cancel，timeout delivery
  authorization 与 durable cancel admission 事务内 first-writer-wins，已持久接受的用户意图不会因
  Supervisor 投递延迟而被重分类。
- Worker timeout 与 user cancel 共用 v2 append-only exact-stop slot；Worker 再以启动 monotonic
  elapsed 防止时钟前跳造成提前 ack。Supervisor 只有取得 request + matching ack + stopped heartbeat
  + idle execution `flock` proof 后才把 timeout 记为 `timed_out` 并提交 Task
  `Failed / WALL_TIME_EXCEEDED`。自然 Succeeded/普通 Failed 在 authorization 前完成时 timeout 为
  `not_triggered`；durable user-cancel admission 先赢时为 `suppressed`；authorization/request 后
  自然终态先赢时为 `superseded`。四条竞争路径只允许一个 durable winner。
- Workbench 的 SQLite bounded projection 精确包含 `state`、`wall_time_seconds`、`started_at`、
  `deadline_at`、`resolved_at`、`failure_code`、`terminal_status` 七个只读字段，不暴露
  ID/hash/PID/path；浏览器没有 timeout mutation 按钮，`POST /timeout` 路由不存在。
- 本项为 **Verified**：Scientific Runtime 304/304、固定 venv Worker 32/32（含 CPU Deepwave smoke
  3/3）、launch-control 25/25、Web 46/46、Embedding 6/6、根 CTest 39/39、MCP 1/1、Node UI 与
  governance 均 PASS。真实固定 venv CPU Deepwave timeout E2E 验证了 v2 request/ack/stopped/idle
  proof、`timed_out` 与 `Failed / WALL_TIME_EXCEEDED`；本切片未运行 CUDA。

### P2-009A 有界 positive receipt resolution/adoption（2026-07-16）

- 状态：**Implemented → Verified；完整 P2 仍 Pending**。本切片不是 finite retry；该策略在本
  checkpoint 当时仍 Pending，现已由 D-012 Accepted 并在 P2-009B 单独推进。
- 范围只含 immutable `reconciliation_required` dispatch intent 的两类 current Adapter `1.4.0`
  exact positive receipt：current managed attempt 的 spawned + exact ready + heartbeat
  （`running`/`succeeded`/`failed`），以及 current Adapter 下
  legacy-private schema `1.0` 的 exact launched receipt。公共 Adapter `1.0`–`1.3` 不在范围内。
- 只有 active fenced Supervisor term 可以在严格 proof 成立时追加 bounded resolution/adoption；原有
  `reconciliation_required` outcome 保持不可变。缺失、歧义、损坏、不匹配、部分、launch/ticket failed
  或 staged evidence 继续为 `action_required`；spawned + exact ready 后的 terminal heartbeat
  `succeeded`/`failed` 仍可认领，并在同一 Supervisor 周期执行 status catch-up。不做 launcher、
  negative reset、refund、retry，也不根据 reconciliation 负向推断或合成 Task terminal；已证明的
  terminal receipt 只沿既有 exact status bridge 追赶。
- Workbench/API 只暴露有界 `action_required`/`resolved` 投影；不暴露 handle/hash/PID/path，也不
  增加浏览器 mutation。session 只声明 `positive_receipt_reconciliation=true`，继续保持
  `automatic_reconciliation=false` 与 `retry=false`，不宣称 full/automatic reconciliation。
- SQLite v13 保留原 `reconciliation_required` outcome 的原始字节/hash，另追加 current-term
  authorization、proof-specific adoption、resolution 与统一 `effective_dispatched_intents`；resolution、
  authorization 均 append-only，adoption + resolution 同事务提交，dangling adoption fail-closed。
  cancel、timeout 与 terminal trash SQL guard 统一消费 effective dispatch。
- Adapter/Dispatcher 对 managed/private receipt 做固定身份和 fingerprint 校验；managed
  `launching + exact ready` 只允许在既有 submission lock 内 bookkeeping-promote，不执行新的
  `Popen`、替换 Worker、扫描 run root 或根据 heartbeat age 推断失活。Supervisor 每周期最多 probe
  一个 action-required task，并用既有 projection cadence 保持多任务公平；managed running 同周期
  arm timeout，terminal receipt 同周期 status catch-up。
- 整体验证捕获并修复了既有 same-key 首次 approve/submit 的 late-replay TOCTOU：若另一调用在首次
  replay lookup 后已将 Task 入队，Service 在返回状态冲突前再次读取同一 durable idempotency row。
  该并发用例 10 进程并行重复 PASS，未放宽不同 key 或不同 request hash 的冲突边界。
- 验证：Scientific Runtime 319/319；固定 venv Worker 32/32（含 CPU smoke 3/3）；launch-control
  25/25；Web 46/46；Embedding 6/6；根 CTest 39/39；MCP 1/1；Node UI、continuity/launcher/
  runtime-secret、编译与 diff 检查 PASS。真实 Adapter 文件/锁专项覆盖 managed ready promotion 与
  legacy-private v1 exact receipt，replacement launcher 均为零调用；本切片未运行 CUDA。

### P2-009B / D-012 有限自动重试（2026-07-17）

- 状态：**Accepted / Implemented；B1/B2 均 Implemented → Verified；完整 P2 仍 Pending**。
- 固定策略为最多 2 个 append-only attempt；新 Approval 显式绑定 `max_attempts=2`、每次资源上限
  和两次顺序执行的最坏 wall/resource budget，旧 Approval 保持一次。只允许 exact stopped 的
  pre-running launch failure 与 post-ready `worker_exit`；普通数值失败、timeout、cancel、成功、
  损坏/分歧/模糊状态及 reconciliation uncertainty 不进入自动重试。
- **P2-009B1 已 Verified**：ApprovalDecision 1.1 与 SQLite v14 只为 current Algorithm/Adapter
  `1.5.0` 保存 `max_attempts=2`、串行 1、累计 `2W` 和固定失败 allowlist；公开 Store 入口也在事务内
  复核 durable Plan。历史 `1.4.0`/Approval 1.0 回填并保持 `max_attempts=1`。
- 尚无 dispatched handle 的 attempt 1 必须先有 latest SQLite failed observation、固定 Adapter 的
  exact pre-running/stopped 私有证明且无 ready/heartbeat sidecar；active Supervisor term 原子追加
  一份 reservation 和当前 term delivery，Adapter 在 submission lock + idle execution fence 下复核后
  才追加 attempt 2。同 reservation 可跨 term 稳定重放，attempt lineage 保留同 request/submission 与
  独立 job，任何路径都不能创建 attempt 3。
- attempt 2 成功只发布一个 effective dispatched handle；再次 exact pre-running failure 会原子追加
  terminal RunEvent/commit/exhaustion 并把 Task/intent 收敛为 `Failed/retry_exhausted`。普通数值失败、
  timeout、cancel、成功、损坏/分歧/模糊证据仍不重试。Workbench session/task/events/API/UI 只投影
  有界 retry 状态，不暴露 intent/attempt/hash/private proof/path，也没有 retry mutation。
- B1 同时把 current `1.5.0` latest attempt 接入既有 effective status、artifact、cancel 与 timeout
  消费者：attempt 1 继续使用 private 1.1，attempt 2 使用 private 1.2。该兼容只覆盖 B1 的
  pre-running lineage；不能据此推断 B2 post-ready replacement 已实现。
- `retry_exhausted` 没有 launched handle；D-009 Trash/Purge 因此新增 Store-authenticated cleanup proof，
  将 pending purge、两次 observation/private proof、reservation 与 terminal event/commit 绑定。Adapter
  在同一 idle fence 下先写同 purge 墓碑再 FD-relative 删除两个 attempt；不同 purge 拒绝，目录间和
  目录内部分删除均可幂等续作，旁系目录不删除。
- **P2-009B2 已 Verified**：SQLite v15 只接受 current effective dispatched handle、latest exact
  spawned+ready+running observation、idle execution fence、无 stop/cancel ownership、非
  `0/75/76` exit code 与 append-only private worker-exit receipt。active Supervisor 原子追加
  `node_retrying`/reservation 并立即隐藏旧 handle；Adapter 在 submission lock 下复核后用 private
  1.3 追加 attempt 2。ready 后 Store 原子发布 replacement handle 与 `node_started`，所有 status、
  artifact、cancel、timeout 消费者随后只解析 replacement；旧 attempt timeout window 随 reservation
  退役，新的 timeout window 绑定 attempt 2。
- cancel、到期 timeout、自然成功/失败、普通数值失败、证据损坏/分歧/模糊均不能抢占成 retry。
  普通 status bridge 对任何 `worker_exit` 都只读，必须等 fenced retry pass 决策，关闭了
  “retry 检查后、同周期 refresh 前退出”的竞态。attempt 2 的 private 1.2（B1 lineage）与 1.3
  （B2 lineage）exit 都只会 exact terminalize，不会创建 attempt 3。
- attempt 2 pre-running failure 原子进入 no-handle `retry_exhausted`；Store cleanup proof 以兼容的
  schema 1.1 token 额外绑定 prior `worker_exit` private schema/hash，并在 Adapter 同一 idle fence 下
  精确清理两条 mixed lineage。attempt 2 post-ready exit 保留 replacement handle，以普通 receipt-bound
  purge 处理。公开 Workbench/API/UI 仅增加 `finite_automatic_retry.worker_exit=true` 和有界
  Retrying/terminal 投影；`retry=false` 继续表示没有浏览器手工 retry mutation，内部 proof/handle/
  hash/PID/path 均不外泄。
- B2 最终分级回归：Runtime 360/360；TaskService 106/106；Supervisor Store 62/62；Runtime
  Supervisor 27/27；Adapter 60/60；固定 venv Worker 32/32；launch-control 39/39；Web 47/47；
  Embedding 6/6；根 CTest 39/39；MCP 1/1；Node UI、launcher、continuity、runtime-secret、helper、
  py_compile/diff 和三轮独立边界审计 PASS。使用 production-class receipt/fence、真实 SQLite
  v14→v15、跨 term replay、direct/resolved source、两类 attempt2 exhaustion 和同周期竞态注入；
  按 D-011 未在非阶段出口重复数值 FWI/CUDA。
- 不增加浏览器 retry mutation；第 2 次仍失败后的人工再次运行必须创建新 Task/Plan/Approval。

### P2 reconciliation 负向/不确定矩阵（2026-07-17）

- 状态：**Pending → Implemented → Verified；完整 P2 仍 Pending**。本工作轮次只关闭
  reconciliation 矩阵，没有实现 checkpoint / Waiting / resume 或 SSE，也没有执行 P2 阶段出口。
- current/historical managed 边界只接受 `(Adapter 1.4, private 1.1)` 与
  `(Adapter 1.5, private 1.2)` 的 exact stopped pre-running proof：submission lock 内取得 stable
  idle execution fence，ticket 可为 staged/leased/spawned/failed，但必须没有 ready/heartbeat。
  Adapter/Dispatcher probe 不调用 submit、retry 或 launcher；active fence 为 transient，其余缺失、
  旧版本、损坏、分歧或无法精确绑定的状态均为 uncertain，并继续保持 action_required。
- Dispatcher 独立重算 binding/ticket/evidence/private-proof hash，并把 submission、request、job
  精确绑定到 durable intent；内部哈希自洽但属于另一请求的 proof 也必须 fail closed。SQLite v16
  追加 fenced transient/uncertain observation；exact negative 再以单事务追加 proof、supervised
  terminal event/commit 与唯一 resolution，把 Task 收敛为 `Failed / DISPATCH_NOT_STARTED`。原
  `reconciliation_required` outcome 不改写；positive/negative 只能有一个 winner，stale term 与竞态
  无部分写入。
- `approval_budgets.tasks_used` 是 submit 时已消费的 Task admission budget，不是执行量计数；精确
  负向不退款、不重试、不重新打开 Approval。公开 Workbench 仍只给六字段 reconciliation 投影，
  新增 `exact_negative_reconciliation=true`，保持 `automatic_reconciliation=false`、`retry=false`，
  并剥离 event 中的 intent/attempt/private proof/hash/path。
- exact-negative terminal Task 可进入 Trash。由于本轮没有扩展不可逆清理授权，purge 在创建请求
  前明确 fail closed（`no authorized purge cleanup`），不会先删 Adapter/SQLite 状态；这是一条已
  验证的安全限制，不表示负向 proof 已支持永久删除。
- 综合审阅补上 cross-request proof 绑定和上述 Trash/Purge 回归。首次 Runtime aggregate 捕获一处
  registry 测试仍把 current schema 写死为 15；仅修正为 16 并以聚焦用例通过后，最终候选树
  Scientific Runtime aggregate 为 **373/373**，Web Python 为 **47/47**，Node UI PASS；编译与
  diff、continuity、launcher、runtime-secret isolation 与 helper 检查 PASS。本轮不是 P2 阶段
  出口，按 D-011 未运行数值 CPU/CUDA、完整回归、CTest 或 MCP。
- 现有 P2 路线切片数量没有新增：本轮验证 1 个 reconciliation 路线切片后，P2 余量由 2 递减为
  1。P2 剩余一个路线切片但跨两个工作轮次：依次完成 checkpoint / Waiting / resume，再完成 SSE
  与完整 P2 故障、代表性 CPU/CUDA 阶段出口。

### P2 收尾第 2 轮：checkpoint / Waiting / resume（2026-07-17）

- 状态：**In progress 工作轮次 → Implemented → Verified；剩余路线切片与完整 P2 仍 In progress**。
  这是 D-011 下的有界工作轮次，不是新的路线切片或路线切片出口；因此 P2 余量仍为 1，完整
  P2–P6 滚动粗估仍约 5，本轮不新增滚动余量行。
- current Deepwave Algorithm/Adapter 提升为不可变 `1.6.0` 并显式声明 checkpoint capability；
  历史 `1.0.0`–`1.5.0` 保留各自版本既有兼容边界，其中 exact 1.5 managed lifecycle 继续支持
  原有 observation/cancel/timeout/retry/exhaustion/cleanup；历史版本不取得 Waiting/resume 权限，
  新 prepare/首次 submit 才选择 current 1.6。
- Worker 只在第一个 optimizer update 完成后的安全点创建 checkpoint。artifact 使用有界、无 pickle
  的 JSON + NPY，并复核受控路径、owner/mode、大小/hash、dtype/shape 与有限数；篡改、部分写或
  身份不一致一律 fail closed。恢复仅限同一 live Worker/process/attempt；未实现跨进程
  restart-from-checkpoint。
- SQLite v17 以 append-only checkpoint wait、resume request、active-term authorization 与 outcome
  保存原子 `Running → Waiting → Running`。Waiting 期间保留 execution 和 capacity `flock`，所以
  Worker 与容量不释放，wall clock 继续；resume 不启动新 launcher、不创建新 attempt，也不消耗
  D-012 retry budget。attempt 2 可独立产生自己的 checkpoint index 1，不能产生 attempt 3。
- Runtime Supervisor 按 cancel → timeout → checkpoint → ordinary status 的优先级裁决；cancel/timeout
  可抢占 Waiting/resume。Waiting 不走 ordinary status/retry；lost Worker/fence、ack 缺失或证据分歧
  保持 fail-closed `action_required`，不猜测恢复或重复执行。
- Workbench/API 只投影有界 checkpoint state/index/timestamps、same-attempt 与自动 resume 能力，剥离
  path、hash、内部 ID 和 private extension；没有浏览器 checkpoint/resume mutation，`POST /resume`
  不存在，本轮 `sse=false`。
- 最终候选 tree 的受影响模块 aggregate **457/457**：Supervisor Store 79、Runtime Supervisor 33、
  TaskService + Adapter 194、launch-control + Worker checkpoint 47、Workbench + Web API 49、Registry +
  Purge 43、Worker state/failure 12；全部通过且组间不重复计数。`py_compile`、`git diff --check` 与
  continuity 治理检查通过；本轮未抵达路线切片/阶段出口，按 D-011 未运行完整回归、P2 aggregate、
  数值 CPU/CUDA、CTest 或 MCP。

### P2 收尾第 3 轮：SSE 与阶段出口（2026-07-17）

- 状态：**剩余路线切片 In progress → Implemented → Verified；P2 Completed / Verified**。本轮只
  增加 scope-bound RunEvent 只读传输与阶段出口证据；Task Store、Supervisor 与 inherited kernel
  `flock` 的事实/调度/执行权威不变，也没有新增浏览器 mutation。
- Workbench 新增显式 `GET /tasks/{task_id}/events/stream?after_sequence=N`。响应只从既有
  `GuidedWorkbench.list_events` 读取，首批在提交响应头前完成 scope/权限校验；每页最多 100 条、
  单事件最多 128 KiB，要求 task/sequence 精确连续，终态后拒绝额外事件。HTTP 端提供 keepalive、
  30 秒有标记 rollover、写超时与 shutdown wakeup，不把连接存活当成任务存活条件。
- 浏览器以 same-origin `fetch` 携带 CSRF 读取 SSE，严格解析 UTF-8/frame/id/task/status；只推进
  durable cursor 并复用既有 Task GET/render，不直接渲染 event。断线有限指数重连，显式 rollover
  从 cursor 继续；连续失败或终态刷新失败后锁定当前 generation 为 GET polling，关闭/重开/
  cancel/purge 均会 abort 旧流。session 现在准确声明 `streaming_events=true` / `sse=true`。
- 候选最终树完整 P2 故障/回归 aggregate **592/592**，组间不重复计数：Scientific Runtime
  413、launch-control 45、FWI Worker 34、Web Python 54、Embedding 6、根 CTest 39、MCP CTest 1；
  Node UI PASS。首次 Scientific Runtime aggregate 暴露 `tests/fwi_worker/__init__.py` 遮蔽产品
  `fwi_worker` 包的 4 个 discovery import error；删除该空测试 package marker 后最终 413/413。
- fresh private SQLite/run root 的 production-composed Guided HTTP/SSE E2E 串行通过。CPU：
  `Succeeded`、11 个连续事件、一次首帧断线/cursor 续传、same-attempt checkpoint resumed、
  8 artifacts/6 PNG、finite loss、`model_update_relative_l2=0.0037406122924204498`；CUDA：相同固定
  Dataset/Algorithm/Adapter/Adam/LR/seed/2-update 边界，`Succeeded`、9 个连续事件、一次续传、
  same-attempt resumed、8/6 artifacts、finite loss、更新 `0.003740607636102564`。两者在终态前均
  未调用 task-detail GET，证明 Supervisor 进展不依赖浏览器轮询。
- 滚动余量按既有公式从约 5 减去本次 1 个 Verified 路线切片，得到约 4；P2 从 1 归零，
  P3–P6 暂估约 4。P2 阶段在此结束；跨进程 restart-from-checkpoint、P3 DAG、P4 Agent、P5 SDK、
  P6 加固，以及服务器 transcript/SQLite 审计历史/备份副本的物理删除仍不因此获得实现声明。
  D-005 仍未获批，没有迁移或删除旧 prompt-like 文件。

### P3 第 1 轮：确定性 DAG readiness / dependency-failure kernel（2026-07-19）

- 状态：**P3 Pending → In progress；本工作轮次 Implemented / Verified，P3 路线切片与阶段仍
  In progress**。本轮不是路线切片出口，不新增滚动余量行；全项目 P3–P6 粗估仍约 4。
- 新增纯 `dag_scheduler`：输入 hash-bound PlanGraph 与包含每个节点的 exact state map，重新执行
  Schema/canonical hash、节点唯一性、依赖存在性和无环校验；用稳定 node ID topological layers
  推导 runnable、waiting、active、Succeeded、Failed/Cancelled 与传递 `blocked_by`。
- Pending 节点只有全部直接依赖精确为 Succeeded 才 runnable；Queued/Running/Waiting/Retrying 与
  Succeeded 永不重复选择。Failed/Cancelled 只阻断其后代，独立分支仍可分类；已持久 Blocked 必须
  能由相同依赖 proof 重建。missing/extra/unknown state、hash drift、未知依赖、环和不可能的 active /
  terminal dependency snapshot 均 fail closed。
- 风险边界：模块没有 Store、clock、lease、Dispatcher、Adapter 或文件访问；未新增 migration，未改
  P2 task→single-intent、retry/cancel/timeout/checkpoint/reconciliation、inherited `flock`、公共 Schema、
  Recipe/API/UI 或 `dag=false`。readiness 不是 durable fact 或 dispatch authorization，也不决定全局
  fail-fast、独立分支取消或 Task 聚合终态。
- 独立现场发现（未并入本轮）：服务端 current Catalog 为 Algorithm/Adapter `1.6.0`，但
  `web/index.html` 的 `normalizeGuidedCatalog` 仍只接受 `1.4.0/1.5.0`；后端/HTTP 通过不证明浏览器
  normalization 可用。下一轮先做聚焦复现与兼容修复，未验证前不改写既有 P2 阶段实验结论。
- 工作轮次受影响测试 **42/42**：新增 kernel 7、既有 contract 32、multi-node submit 零副作用拒绝
  1、Guided 固定 catalog/capability 与单节点 plan 2；`py_compile` 通过。首次 continuity 治理检查逐项
  捕获账本顶部遗漏 `P2-001～P2-009B2`、短入口遗漏历史 Pending 路由两个必需锚点，恢复后复验通过。
  按 D-011 未到切片/阶段出口，未运行 Scientific Runtime aggregate、完整回归、CPU/CUDA、CTest 或 MCP。
- 下一安全动作：先独立复验/处理 current `1.6.0` 浏览器 Catalog normalization 风险；随后设计
  durable per-node state/claim/intent 事实模型，并证明它不削弱 Approval/`plan_hash`、P2
  exact-attempt 生命周期和 Worker kernel fence。资源锁、cache/checkpoint 与 Recipe 属于后续边界。

### P3 第 2 轮：durable node state / claim candidate foundation（2026-07-19）

- 状态：**本工作轮次 In progress → Implemented → Verified；P3 路线切片与阶段仍 In progress**。
  本轮不是路线切片出口，不新增滚动余量行；全项目 P3–P6 粗估仍约 4。
- SQLite v18 新增 append-only `dag_node_state_events` 与 `dag_node_claim_candidates`。事实只允许属于
  current approved 2～32-node PlanGraph 的节点，精确绑定 task/plan/hash/approval/node revision、scope、
  active Supervisor term/fencing token 和 canonical readiness；当前唯一允许的节点事实是 revision 1
  `Pending`，single-node、非当前计划、错误 scope/hash/node、过期或关闭任期均 fail closed。
- Store 在一个 `BEGIN IMMEDIATE` 事务内惰性建立完整 Pending map、重跑纯 readiness kernel、稳定选择
  首个 runnable 节点并写候选。相同 approval/node/revision/term 的重放与并发收敛；同一任期的新
  Approval 和 successor term 分别追加审计候选，不能复用旧授权。读侧验证连续 revision、previous state、
  canonical 时间与完整节点集合；即使存在直接 SQL 篡改也不把异常 facts 隐藏成“无 DAG”。
- 风险边界：候选的 `dispatch_authorized` 恒为 false；未写 Task status、RunEvent、approval budget 或
  dispatch intent，未接 Supervisor/Dispatcher/Adapter/Worker，也没有状态转换、资源锁、cache/checkpoint、
  Recipe/API/UI。现有 P2 task→single-intent、exact-attempt 生命周期与 inherited kernel `flock` 未改变。
- 综合审阅发现并关闭两项一致性问题：candidate identity 纳入 `approval_id`，因此 same-term reapproval
  不会错误复用旧候选；SQL/读侧同时验证 multi-node current Plan 与 readiness 的 indexed identity、
  runnable-selected 关系及完整 latest-state map，拒绝任意 readiness 与 single-node 隐藏行。
- 最终受影响测试 **33/33**：DAG Store 8、migration 初始化/新旧版本升级 15、DAG kernel 7、multi-node
  submit 零副作用 1、既有 P2 dispatch/term fence 2；`py_compile` 通过。首次 continuity 检查捕获短入口
  的固定 P6 锚点被缩写，恢复原文后 continuity/launcher/runtime-secret/helper 治理检查通过。按 D-011
  未到路线切片/阶段出口，未运行 Scientific Runtime aggregate、完整回归、CPU/CUDA、CTest 或 MCP。
- 下一安全动作：先界定 typed 上游 artifact→下游 input/hash binding 及其 canonical validation；当前
  claim candidate 保持 dormant/non-executable，不启动节点 admission、状态转换或多节点派发。

### P3 第 3 轮：typed artifact edge / canonical bytes-hash binding（2026-07-19）

- 状态：**本工作轮次 In progress → Implemented → Verified；P3 路线切片与阶段仍 In progress**。
  本轮不是路线切片出口，不新增滚动余量行；全项目 P3–P6 粗估仍约 4。
- PlanGraph 新增向后兼容的 `1.2.0`：`1.0/1.1` 仍只允许一个 DatasetRef input；1.2 允许最多 32 个
  dataset/node-output input，并把 source node/output port/data type 纳入 `plan_hash`。共享 extractor 与
  readiness/Gate 要求 source 是 direct dependency、上游 port 唯一且 type 精确一致，并跨所有 binding
  kind 拒绝重复 target port；不允许把运行时 artifact hash 偷渡进已批准 PlanGraph。
- 新增纯 `dag_data_binding`：复核 ArtifactManifest Schema、真实 bytes SHA-256/size、task/node/plan、
  algorithm、output port/type、静态 Dataset lineage 及 fingerprint input/seed/device；binding hash 还覆盖
  artifact schema/media。ArtifactManifest 1.0 无法证明 producer 自身消费 node-output 的 chained lineage
  时 fail closed；binding 与 v18 candidate 的 `dispatch_authorized` 都恒为 false。
- TaskService/Registry 可持久化 type-compatible 1.2 dormant Plan；consumer type 不匹配在落库前拒绝。
  P1 expected request、Deepwave Dispatcher、RunEvent 和 Store submit 对未物化 source input 受控拒绝，
  不产生 Task/RunEvent/budget/dispatch side effect；历史 durable single-Dataset dispatch 重建保持原样。
- 综合审阅关闭 malformed plan 非受控异常、1.2 fan-in cap、dataset/source 重复 port、artifact lineage/
  fingerprint 漂移和 media 未入 binding hash 等问题。当前仍只证明单 edge/root-static producer；shape/
  dtype/units、全输入聚合、producer Succeeded revision/receipt、approval/current term 原子围栏均未实现。
- 最终受影响测试 **175/175**：合同、DAG readiness/data binding/Store 与 TaskService；`py_compile` 通过。
  按 D-011 未到路线切片/阶段出口，未运行 Scientific Runtime aggregate、完整回归、CPU/CUDA、CTest
  或 MCP。下一安全动作是 durable all-input binding fact；在其精确复核同一 bytes/hash 前不做 admission。

### P3 第 4 轮：durable all-input binding substrate（2026-07-19）

- 状态：**本工作轮次 In progress → Implemented → Verified；P3 路线切片与阶段仍 In progress**。
  本轮不是路线切片出口，不新增滚动余量行；全项目 P3–P6 粗估仍约 4。
- SQLite v19 新增 append-only `dag_node_input_binding_facts`：精确引用 current claim candidate、Plan、
  Approval、target Pending revision、scope 与 active Supervisor term；Store 在同一 `BEGIN IMMEDIATE`
  事务内重跑 readiness，按 Plan 输入顺序复核完整 input set，并将 DatasetRef 绑定 immutable project
  Catalog document hash。相同 claim/term 并发和重放收敛，reapproval/successor term 不能重标旧事实。
- `dag_node_succeeded_outputs` 是 reserved aggregate receipt contract：绑定 producer exact Succeeded revision、
  prior input-binding hash、原 Approval/term、canonical receipt 与完整 output manifest inventory。v18 的
  initial-Pending-only trigger 保持不变，v19 没有该表的生产 writer；CodeGraph 复核 binding API 只有测试
  caller。测试中显式移除 guard 并 seed future fact 只验证 schema/reader/hash/fail-closed contract，不是
  真实 producer 因果证明，也不证明外部 artifact 文件在 SQLite commit 前后不可变。
- 当前真实可达正路径是 dataset-only runnable root；返回事实的 `dispatch_authorized` 恒为 false，且不写
  Task status、node state、RunEvent、Approval budget、dispatch intent，也不调用 Dispatcher/Adapter/Worker。
  source input 仅对 reserved receipt、latest Succeeded revision、同一 manifest 和本次 immutable bytes
  重算 hash 全部一致时形成 dormant fact；missing/extra/duplicate、byte/lineage/port/type drift 均回滚。
- 候选树受影响测试 **50/50**：v19 root/replay/8-way concurrency、fixture-only receipt reader、rollback/
  append-only，既有 DAG Store/data binding、migration registry/concurrency、Task Store 初始化、新版本拒绝、
  purge upgrade 与 Supervisor schema；`py_compile` 通过。首次注册套件捕获并修复一个陈旧 v17 并发升级
  断言。按 D-011 未到路线切片/阶段出口，未运行 runtime aggregate、完整回归、CPU/CUDA、CTest 或 MCP。
- 下一安全动作：单独界定 fixed-Adapter producer success/output causal writer 与首个 node transition，要求
  在同一 submission/execution fence 内拥有 receipt、manifest 和实际 bytes；随后 admission 仍须重新复核。
  本轮不放宽 transition trigger，不复用 P2 task-wide receipt，不实现 chained lineage 或真实节点派发。

### P3 第 5 轮：executable DAG vertical kernel（2026-07-19）

- 状态：**本工作包 In progress → Implemented → Verified；P3 路线切片与阶段仍 In progress**。
  本工作包不是新增路线切片，不新增或递减滚动余量；全项目 P3–P6 粗估仍约 4。
- SQLite v20 增加 exact admission、append-only transition/terminal facts 与 latest state projection。admission
  在一个 `BEGIN IMMEDIATE` 内重新绑定 current Plan/hash、Approval/scope、node revision、v19 input-binding
  hash、同一 active Supervisor term 与 node idempotency key，消费一次既有 Task admission budget并创建唯一
  P2 dispatch intent/`task_queued`。同 key 并发与重放收敛；旧 term、reapproval、输入篡改和异常状态零授权。
- TaskService 让一个 dataset-root Pending 节点经既有 P2 Dispatcher/Adapter/managed Worker evidence 推进
  `Pending → Queued → Running → Succeeded|Failed`。Task/RunEvent 只做最小节点投影；节点终态时 Task 保持
  Running，避免在尚有 Pending sibling 时伪造 DAG 聚合终态。DAG admission 不启用 Task attempt 2，
  exact-negative 与 post-ready `worker_exit` 均终结当前节点且不 retry。
- 成功路径在 Adapter submission lock 与 inherited idle execution `flock` 持有期间复核 succeeded record、
  exact attempt/handle、每个 artifact 的 bytes/hash/size，再在 fence 内提交 schema 2 receipt。receipt 分开记录
  input 与 completion Supervisor term，因此旧 term 不能写，successor term 可在重新围栏后收敛；terminal
  并发只产生一份 terminal fact。普通 private receipt、cancel、timeout 与 checkpoint 入口对 DAG fail closed。
- 范围边界：每个 Task 当前只允许一个 dataset-root node intent；没有 successor admission、多节点并行/
  聚合、资源锁、cache/checkpoint、Recipe/UI 或 P3 出口。公开 capability 保持 `dag=false`；不扫描
  `FWI_RUN_ROOT`，不接受路径、shell 或 `extra_args`，也不改写 P2 exact-attempt/reconciliation/kernel fence。
- 候选最终树相关 aggregate 运行 **446 项：442 PASS、4 个 system-Python `pydantic` dependency errors**；
  按失败测试规则改用产品配置的固定 Worker venv 后，该失败子集 **4/4 PASS**。覆盖 migration/upgrade、admission 并发与重放、
  stale term/reapproval/input tamper、dispatch 崩溃窗口、receipt/attempt 分歧、terminal 并发、exact-negative、
  worker-exit 无 attempt 2、P2 单节点兼容及真实固定 Dispatcher/Adapter output fence；`py_compile`、完整 diff
  与治理检查 PASS。按范围未运行完整回归、CPU/CUDA、CTest 或 MCP。
- 下一安全动作：另开工作包界定 successor-node admission、同一 Task 多 dispatch intent 与资源所有权；
  在该边界验证前，node-output 只可作为 v20 succeeded receipt 被严格读取，不能授权下游执行。

### P3 第 6 轮：deterministic multi-node runtime / inherited CPU-GPU locks（2026-07-19）

- 状态：**本工作包 In progress → Implemented → Verified；P3 路线切片与阶段仍 In progress**。
  本工作包不新增路线切片或递减滚动余量；全项目 P3–P6 粗估仍约 4。
- SQLite v21 保留 append-only exact intent identity 并允许同一 Task 的历史 per-node intents。
  TaskService 在 active Supervisor term 下重新验证 Plan/Approval/readiness/input binding，以稳定
  node-id 顺序且每 Task 同时最多一个 active node 接通 claim→admission→P2 runtime。A 成功后
  B/C 才依次可派发，D 只有在 B/C 均成功后才可派发；跨 Task Worker 并发受全局上限约束。
- Failed 或 node-local Cancelled 只生成 durable descendant Blocked facts，独立分支继续；fan-in
  缺少任一成功依赖零 admission。图收敛后节点状态、blocker proof 与 Task 聚合事件原子提交；
  node-local Cancelled 最终聚合为 Failed，task-wide cancel 聚合为 Cancelled。
- task-wide cancel admission 后不再启动新节点；已有 exact active attempt 继续使用既有 P2
  cooperative cancel/terminal-won 边界。terminal-won 先经 fenced node terminal fact 投影，再完成
  cancellation outcome；终态 fact 与 outcome 间崩溃可由 successor term 收敛且不重复事件、fact 或 dispatch。
- Supervisor 重启在 A 成功后、B/C 调度中及 receipt 投影前后均从 Store 重建。全成功路径
  `[A,B,C,D]` dispatch 次数为 `[1,1,1,1]`；分支失败或 node-local cancel 为 `[1,1,1,0]`；
  task-wide cancel/terminal-won 为 `[1,1,0,0]`；B/C 重启窗口及 receipt 前后窗口的有效 dispatch
  均保持一次。双控制面竞争、stale term、ABA、lease lost、锁竞争与 receipt 分歧均 fail closed。
- CPU 节点继承 submission/execution 与 generic capacity kernel `flock`；CUDA 节点额外继承固定
  GPU slot `flock`。Parent/bootstrap 双端复核 inode、generation 与 resource projection；PID、
  heartbeat 或 SQLite lease 均不替代执行/容量权威。最大观测 CPU 并行为 2，单 GPU 为 1。
  首次混合版本 CUDA 部署须先 quiesce 不持有 GPU slot 锁的旧 Worker。
- 相关 aggregate 仅运行一次：516 项中 511 PASS，3 failures + 2 errors 均来自陈旧 v21 migration
  upgrade fixture/version assertions；按失败测试规则修正后只重跑该 5 项，5/5 PASS。一次综合审阅
  发现并关闭 node-local Cancelled 与 terminal-won cancel crash-window 两项高风险；`py_compile`、
  diff 与治理检查 PASS。未运行完整项目回归或正式 CPU/CUDA 数值阶段出口。
- 范围边界：未实现 node cache、Recipe/API/UI 或 P3 阶段出口；未改变 P2 same-live-attempt
  checkpoint、固定 Adapter/MCP 白名单或 public `dag=false`。
- 下一安全动作：单独推进 P3 node cache/checkpoint 工作包，然后停止；不得顺带进入 Recipe。

### P3 第 7 轮：node cache / trusted artifact lineage / DAG checkpoint 协作（2026-07-19）

- 状态：**本工作包 In progress → Implemented → Verified；P3 路线切片与阶段仍 In progress**。
  用户明确要求本工作包不作为 P3/P3–P6 新路线切片；滚动粗估仍约 4，非承诺。
- SQLite v22 增加 scope-local append-only cache entry/hit fact。cache key schema `1.0.0` 规范化
  Plan schema/task/node contract、Algorithm manifest/hash、Adapter id/version、Dataset catalog 或
  node-output bytes identity、参数/资源/副作用/输出合同、clean Git commit/tree、environment/runtime/
  seed/hardware/determinism、Approval scope 与 project/principal；task/plan 实例 ID、时间、路径不入 key。
- executed source 只在同一 terminal transaction 中以 exact Succeeded revision、receipt、attempt 1、
  terminal heartbeat、adopted fingerprint 和输出 manifest 形成 cache entry。cancellation terminal-won
  只有 Adapter 终态而无 Succeeded heartbeat 时仍保留 P2 终态，但不会成为 cache source。
- lookup/commit 在 active Supervisor term 内重新执行 Gate，并重新打开物理 source artifact 复核
  bytes/hash/size/schema/media/lineage；合法 hit 原子写 path-free verification、cache RunEvent、output
  alias receipt 与 `Pending → Succeeded` revision，Worker launch/dispatch/admission/attempt/retry 均为 0。
  parameter、input hash、Algorithm manifest/version、Adapter、output contract、Git/runtime/environment、
  Approval/project/principal 任一变化产生不同 key；缺失 entry 降级为 miss，任何篡改或越权拒绝复用。
- cached alias 保留 physical source identity 与可信 transitive Dataset roots；canonical B/C/D 测试可按
  producer semantic key 重建 `D → C → B → Dataset`，每级 direct input hash 与 root catalog/hash 一致。
  当前 fixed Deepwave Gate 仍只执行 dataset-root 节点，node-output 链是可信存储/选择基础而非已公开
  的通用 Recipe 或自动算法流水线。
- DAG checkpoint 只为 exact current Running node 放行现有 P2 same-live Worker/process/attempt 路径；
  Waiting/resume 前后 attempt id/number、ticket/ready PID、单 intent/admission 和 approval consumption
  不变，attempt 2 与两类 D-012 retry reservation 均为 0。DAG recovery/cache/checkpoint 分开记录；
  未实现跨进程 Worker restart-from-checkpoint。
- 开始前现场确认 `f0177bd` 已与 upstream 一致、工作树干净；A/B/C/D 基线 dispatch `[1,1,1,1]`，
  restart 不重复，CPU capacity=2、GPU slot=1。候选代码树相关 aggregate 仅运行一次：520 项中
  509 PASS，10 个 system-Python `pydantic` errors + 1 个旧 selector；按失败入口规则以固定 Worker
  venv 和正确 scheduler 模块仅重跑 17 项，17/17 PASS。一次综合审阅无阻断/高优先级发现；
  `py_compile`、diff 与治理检查 PASS；未跑完整项目回归、正式 CPU/CUDA 数值 E2E、Recipe/API/UI
  或 P3 阶段出口。
- 下一安全动作：单独推进固定 Recipe/Guided 工作包；public `dag=false`，全项目粗估约 4 不变。

## 新会话恢复协议

新 Codex 在执行 D-003 相关工作前必须：

1. 完整阅读 `AGENTS.md` 与 `docs/PROJECT_CURRENT_STATE.md`；
2. 检查当前分支、`git status`、未提交 diff 和最近提交，并验证 `ffeb5bc` 仍是
   当前 D-003 分支的 ancestor；
3. 按短入口定向读取本账本顶部/current checkpoint/当前切片、完整计划的当前阶段和相关
   `D-*`；阶段/范围变化、冲突 reconciliation、安全审计或持续记录修改时完整深读对应真源；
4. 如果分支不是 `feature/scientific-agent-runtime`、ancestor 不符或工作树不干净，不得自动
   switch/reset/rebase；先保护现有改动，对照 Git/代码/测试记录 reconciliation；
5. 代码架构/调用流优先有界 CodeGraph；pending-sync/未索引文件、Git、测试、运行状态和
   安全证据使用直接工具；
6. 运行内部只读状态刷新，核对服务和最近任务，但不把临时快照写成本项目进度；
7. 对照阶段表检查真实交付物与测试，不能只信状态文本；
8. 从第一个未满足出口条件开始声明一个有界工作包：一个主要目标、一个风险边界、预计触及面、
   验证层级和停止点；只执行该轮，不重复已经验证的工作或静默扩张到整个切片/阶段；
9. 轮次内只跑受影响/失败测试；抵达切片出口才跑一次相关 aggregate，抵达阶段出口才跑完整
   回归与代表性 CPU/CUDA。默认一次综合审阅，第三轮及以后须先获用户明确批准；
10. 当前用户改变范围时先更新非 D 计划/账本；涉及任何 D 条目时必须先走单 D diff/hash + 用户
   单独原样复制 `D-AUTH` 句的最高优先级门，未授权不得修改；
11. 工作结束前只同步拥有变化事实的最少文档；未到切片出口如实保留 `In progress` 和下一安全
   动作，抵达出口才按 Git 规则形成 Verified checkpoint。

## 每个开发切片或工作轮次的记录格式

在下面追加一行，保留旧记录用于追溯：

| 日期 | 切片 | 状态变化 | 交付物 | 验证 | 下一动作/阻塞 |
|---|---|---|---|---|---|
| 2026-07-15 | PREP-001 | Pending → Implemented | D-003/D-004、实施计划、进度账本、Git 规则与 D-005 提案 | launcher/continuity/runtime-secret/diff：PASS | 新会话冷启动演练；等待用户开始 P0.1 |
| 2026-07-15 | PREP-002 / D-004 | Partially implemented → Verified | 独立实现分支首个 SSH checkpoint | `b5ac633` 本地/upstream 一致 | 准备阶段仅余首个新会话冷启动演练 |
| 2026-07-15 | PREP-003 | Implemented → Verified | 真实新会话 branch/diff/ancestor/ledger/helper reconciliation | live preflight + governance tests：PASS | 准备阶段完成 |
| 2026-07-15 | P0-001 | Pending → Verified | 七类 Schema、canonical/hash、Gate、fingerprint、状态/API/Adapter/Proto、威胁与差异审计 | contract 23/23、CTest 39/39、runner 1/1、FWI 26/26、Web 7/7、UI/governance：PASS | P0 阶段完成；P1.1 SQLite Task Store/TaskService |
| 2026-07-15 | P1-001 / P1.1a | Pending → Verified（foundation）；P1.1 → Partially implemented | SQLite migration/store、受 scope 约束的 TaskService、task/draft/plan/approval/event/create-idempotency、Gate 补强 | contract 27/27、TaskService 33/33、组合 60/60；CTest 39/39、runner 1/1、FWI 26/26、Web 13/13、UI/governance PASS | P1.1b/P1.2 Catalog/Registry；同事务 submit 与 Adapter 仍 pending |
| 2026-07-15 | P1-002 / P1.1b | Pending → Verified（registry foundation）；P1.1 仍 Partially implemented | SQLite v2 migration、immutable Catalog/Registry、approval budget 行、server-owned snapshot validation、path-free Marmousi/Deepwave registration | Registry 22/22、TaskService 33/33、contract 28/28；CTest 39/39、runner 1/1、FWI 27/27、Web/embedding 13/13、UI/governance PASS | P1.2a Deepwave Adapter；同事务 submit/Queued 仍 pending |
| 2026-07-15 | P1-003 / P1.2a | Pending → Verified（fixed Adapter）；P1 仍 In progress | 固定 Deepwave 六方法 Adapter、Registry/local identity 双边界、跨进程幂等、固定 launcher、脱敏状态、严格 artifact collect | Adapter 17/17、Scientific Runtime 100/100、真实 CUDA 一次迭代 submit/status/collect/replay；全量回归 PASS | P1.1c 原子 Gate/budget/submit intent/Queued；事务后 dispatch 与 Guided Web |
| 2026-07-15 | P1-004 / P1.1c | Pending → Verified（atomic submit backend）；P1 仍 In progress | SQLite v3、同事务 Gate/budget/idempotency/intent/task_queued/Queued、固定 one-shot dispatcher、preflight/actual fingerprint receipt、显式 crash states | Scientific Runtime 117/117；CTest 39/39、MCP 1/1、FWI 27/27、Web/embedding 13/13、UI/governance PASS | P1 Guided Web 选择/确认/批准/状态/结果闭环；P2 recovery 仍 pending |
| 2026-07-15 | P1-005 / Guided Web | Pending → Verified；P1 完成 | SQLite v4 mutation/abandonment、固定 Guided composer、同源 Workbench API、Web legacy-submit opt-out、确认/修改/批准/放弃 UI、status/event/artifact 闭环 | Runtime 139/139、CTest 39/39、MCP 1/1、FWI 27/27、Web 27/27、Embedding 6/6 及 UI/governance PASS；真实 CUDA、两个下载、source-policy、Web 重启和根启动器 PASS | 按用户要求停在 P2 之前；等待明确启动 P2 |
| 2026-07-15 | P1-006 / D-006 | Accepted / Implemented → Verified（P1 配置维护；P2 未开始） | 新 Algorithm/Adapter 与 MCP runner `1.1.0`、`1..10000` 全链路边界、旧 `1.0.0` 只读兼容、长任务警告 | Runtime 143/143；CTest 39/39、MCP 1/1、FWI 27/27、Web 27/27、Embedding 6/6、UI/governance PASS；既有 DB/API 边界验证 PASS | 按用户要求停在 P2 之前 |
| 2026-07-15 | P1-007 / D-007 | Accepted → Verified | D-007 checkpoint Algorithm/Adapter `1.3.0`、不可变 `1.2.0` 六参数读兼容、contract `1.1.0` 六参数 Draft/Plan、Adam/SGD 与定点学习率、证据分级卡、轮询滚动保持 | 控制面 157/157、Worker 27/27、Web 29/29、Node UI 行为、diff check：PASS；真实 CUDA/SGD 两步任务、artifact hash 与 GPU/finite/model-update 指标 PASS | 停在完整 P2 前；交付实际试用说明 |
| 2026-07-15 | P2-001 / D-007 | Pending → Verified；完整 P2 仍 Pending | SQLite v5 scope index、immutable-order cursor 分页、GET task collection、左栏持久索引、关闭视图不取消、单任务重开与无原 key fail-closed | 控制面 157/157、Web 29/29、Node 125 条/7 页遍历及真实 HTTP 重载/重开旧新任务 PASS | 停止；不启动 cancel/lease/retry/reconciliation/SSE |
| 2026-07-15 | P1-008 / D-008 | Accepted → Implemented → Verified | 浏览器 schema v3 的 Conversation/Task 可选引用与本地无级联对话删除；Algorithm/Adapter `1.4.0` 的持久 Plan 驱动 2 数值 + 6 PNG 标准结果画廊；历史 `1.0.0`–`1.3.0` 精确两 artifact 兼容 | Runtime 165/165（含 1.2/1.3 optimizer-aware lost-response replay）、Worker 28/28、Web 29/29、Embedding 6/6、CTest 39/39、MCP 1/1、UI/治理 PASS；fresh v6 CUDA 10 events、8 artifacts/6 PNG、loss/update/GPU/重启不变性 PASS | 向用户交付实际前端试用说明，并停在完整 P2 前 |
| 2026-07-15 | P2-002 / D-008 | Accepted → Implemented → Verified；完整 P2 仍 Pending | SQLite v6 resolved-terminal visibility event/projection/mutation、scope/CAS/idempotent Trash/Restore、trash 后审计/结果可读、restore 不重跑、无级联 | 当前 v6 checksum、revision 0→1→2、trash 后 artifact、Restore 不重跑、AwaitingApproval 409、abandon 后 trash、同库重启三类 fingerprint/8 项 bytes+hash 不变及服务清理 PASS | 永久 purge/服务端 transcript 删除与完整 P2 继续 Pending |
| 2026-07-15 | P2-003 / D-009 | Accepted → Implemented → Verified；完整 P2 仍 Pending | SQLite v7 两阶段 purge/alias/outcome、pending 禁止恢复与读取、receipt-bound FD-relative 本地 job 目录删除、Web 强确认/继续删除、conversation 无级联 | Runtime 180/180、Web 29/29、Node UI/治理 PASS；临时目录 succeeded/failed、queued/running 拒绝、崩溃恢复、并发锁、符号链接和 v6→v7 通过；未运行真实 FWI/CUDA | 等待用户试用；SQLite 审计历史、服务端 transcript、备份/外部副本及完整 P2 后续能力不在本切片 |
| 2026-07-16 | P2-004 | Pending → In progress；完整 P2 仍 Pending | 初始探索启动时处理既有 pending/dispatching 并追赶一次状态，不扫描 run root | 修改前 Runtime 180/180 现场复验 PASS；真实 Adapter 终审发现 process-local capacity 与 bind-before-side-effect 风险 | 禁止 startup 首次派发；收紧为只读 launched receipt 收养，并把 bind 提到 recovery 前 |
| 2026-07-16 | P2-004 | In progress（安全范围收紧） | pending/no-record 全部 deferred；只 lookup current 1.4 exact launched record；bind→recovery→activate→publish→serve | 三项以上 pending 零派发、真实 Adapter replacement launcher 零调用、忙端口零 recovery、不同 receipt 硬失败 | 完成全量回归并更新准确计数 |
| 2026-07-16 | P2-004 | In progress → Verified；完整 P2 仍 Pending | scope/limit pre-scan、只读 launched receipt 收养、共享严格 receipt、相同 handle 并发收敛、1000+ event 分页与一次 status 追赶、安全 server 编排/脱敏 summary | TaskService 80/80、Adapter 27/27、Runtime 201/201、Worker 28/28、Web 36/36、Embedding 6/6、CTest 39/39、MCP 1/1、Node UI/治理 PASS；未运行真实 FWI/CUDA | 下一步先做 fenced capacity/lease/heartbeat 与持续 supervisor；随后才处理 pending/no-record、cancel/timeout；retry/完整 reconciliation/SSE 仍 Pending |
| 2026-07-16 | P2-005A | Pending → Implemented → Verified；完整 P2 仍 Pending | SQLite v8 scope-level fenced 控制面 lease/term/closure/supervised commit audit；observation-only Runtime Supervisor；bind→recovery→lease/ready→listen/publish；cooperative stop 与硬退出边界 | Runtime 226/226、Worker 28/28、Web 45/45、Embedding 6/6、CTest 39/39、MCP 1/1、Node UI/治理 PASS；并发/expiry/ABA/事务内时钟/迟到写、self-fence、信号/零请求/drain 覆盖；未运行真实 FWI/CUDA | 下一步设计 staged Worker launch、attempt fence、跨进程 capacity lease 与独立 heartbeat；pending/no-record、cancel/timeout/retry/reconciliation/SSE 仍 Pending |
| 2026-07-16 | D-010 / PREP-004 | Accepted → Implemented → Verified；D-003 runtime phase 不变 | 81 行短入口、AGENTS bootstrap v2、按需深读、CodeGraph 优先/回退、聚焦测试输出策略和同步规则 | launcher/continuity/helper/syntax/diff PASS；CodeGraph 定位 P2-005A RuntimeSupervisor；固定默认读取 201 行后再定向补充 | 新开一次 Codex 会话加载新指令；继续 P2 staged Worker launch 设计 |
| 2026-07-16 | P2-005B | Pending → Implemented → Verified；完整 P2 仍 Pending | current 1.4 managed attempt ticket、stable submission/capacity `flock` 经 exec 继承、pre-import ready/heartbeat、exact launching adoption、post-Popen deferred、purge fence、legacy CLI/Web private-sidecar guard | Runtime 234/234 + launch-control 8/8、Worker 29/29、Web 46/46、Embedding 6/6、CTest 39/39、MCP 1/1、Node UI/治理 PASS；真实轻量 exec、父控漏写、N+1、0755 root、active purge/篡改/兼容覆盖；未运行数值 FWI/CUDA | 下一步将 Worker attempt/heartbeat 投影到 SQLite 并接入 fenced Supervisor/scheduler；pending/no-record、cancel/timeout/retry/reconciliation/SSE 仍 Pending |
| 2026-07-16 | P2-005C | Pending → Implemented → Verified；完整 P2 仍 Pending | SQLite v9 exact Worker attempt/heartbeat samples、active-term/monotonic/JSON-column fence、latest-only replay、atomic late adoption；Adapter/Dispatcher observation 与 Supervisor dispatching/60s dispatched cadence，零 launcher | TaskService 82/82、Adapter + launch-control 44/44、Runtime 241/241、Worker 非数值 26/26、Web 46/46、Embedding 6/6、CTest 39/39、MCP 1/1、Node UI/治理 PASS；未运行 Deepwave 数值 FWI/CUDA | 下一步实现可恢复 fenced scheduler，先证明 pending/no-record 首次派发/重启不重复；cancel/timeout/有限 retry/reconciliation/SSE 仍 Pending |
| 2026-07-16 | D-011 / PLAN-002 | Proposed → Accepted → Implemented / Verified（governance）；运行时阶段不变 | 弹性中等切片、独立多算法选项、显式工作流与分级测试规则；P5 不再强制自动去噪/QC/FWI 链 | continuity、launcher、helper、runtime-secret isolation、diff check：PASS；仅文档/治理变更，未运行数值 FWI/CUDA | 继续当前 P2 可恢复 fenced scheduler；真实安全/验证边界需要新增拆分时，先获用户明确批准再记录 |
| 2026-07-16 | P2-006 | Pending → Implemented → Verified；完整 P2 仍 Pending | enqueue-only submit、SQLite v10 active-term dispatch authorization、pending/no-record 首派、exact staged 同 attempt 恢复、current 1.4 legacy-private receipt fenced adoption 与 lease 前只读 inventory | Runtime 251/251、固定 venv Worker 非数值 + launch-control 37/37、Web 46/46、Embedding 6/6、CTest 39/39、MCP 1/1、Node UI、continuity/launcher/runtime-secret/helper/diff PASS；未运行数值 FWI/CUDA | 下一步基于 exact attempt/kernel fence 实现 cancel/timeout；随后有限 retry、reconciliation resolution 与 SSE |
| 2026-07-16 | P2-007 | Pending → Implemented → Verified；完整 P2 仍 Pending | SQLite v11 exact request/active-term authorization/outcome、Worker-published capability 与 self-cancel、ack + stopped heartbeat + idle execution fence 终态证明、自然终态 superseded 竞争和 Guided Web requested/cancelled/superseded | Runtime 271/271、固定 venv Worker 31/31（CPU smoke 3/3）+ launch-control 17/17、Web 46/46、Embedding 6/6、CTest 39/39、MCP 1/1、Node UI、治理/编译/diff PASS；未运行 CUDA | 先由用户确认 timeout 的终态语义、计时起点和 force policy；随后有限 retry、reconciliation resolution 与 SSE |
| 2026-07-16 | P2-008 | Pending → Accepted / In progress；完整 P2 仍 Pending | Failed/WALL_TIME_EXCEEDED、首条 durable ready+running observation 起算、v2 exact-stop、grace 后 Worker self-exit、timeout delivery authorization / durable cancel admission first-writer 的实现开始 | 设计边界已核对；实现与测试进行中，尚无 Verified 证据 | 完成 SQLite v12、Supervisor/Adapter/Worker、只读 Web 投影与分级回归 |
| 2026-07-16 | P2-008 | Accepted / In progress → Implemented → Verified；完整 P2 仍 Pending | SQLite v12 immutable window/authorization/outcome、Store 首条 current Worker durable ready+running `observed_at` 起算、deadline/authorization 前零 Worker mutation、四态竞争、v2 Worker self-stop 与七字段只读 Workbench 投影；无 timeout POST | Runtime 304/304、固定 venv Worker 32/32（CPU smoke 3/3）、launch-control 25/25、Web 46/46、Embedding 6/6、CTest 39/39、MCP 1/1、Node UI/governance PASS；真实固定 venv CPU Deepwave timeout E2E PASS，未运行 CUDA | 下一步有限 retry 与 `reconciliation_required` resolution；SSE 继续后置 |
| 2026-07-16 | P2-009A | Pending → In progress；完整 P2 仍 Pending | 限定 current Adapter 1.4 managed spawned + exact ready + heartbeat（含 terminal succeeded/failed）与 current Adapter/legacy-private schema 1.0 exact launched receipt 的 bounded positive resolution/adoption；launch/ticket failed 等负向证据保持 `action_required`，无 retry/退款或 reconciliation 合成终态 | 范围与安全边界已核对；Store/Service/Supervisor、Workbench/API 只读投影及测试进行中，尚无 Verified 证据 | 完成实现与分级回归；finite retry 具体策略仍 Pending，SSE 后置 |
| 2026-07-16 | P2-009A | In progress → Implemented → Verified；完整 P2 仍 Pending | SQLite v13 append-only authorization/adoption/resolution 与 effective dispatch；managed/private exact positive probe；每周期最多一次、同周期 timeout/status；六字段只读 Web 投影且无 mutation；整体验证同时关闭 same-key approve/submit late-replay race | Runtime 319/319、固定 venv Worker 32/32、launch-control 25/25、Web 46/46、Embedding 6/6、CTest 39/39、MCP 1/1、Node UI/治理 PASS；真实 Adapter 文件/锁 probe 中 replacement launcher 零调用；并发用例 10 进程重复 PASS；未运行 CUDA | 先请用户确认 finite retry 次数/预算/失败边界，再推进 P2-009B；负向 reconciliation 与 SSE 后置 |
| 2026-07-17 | P2-009B / D-012 | Pending → Accepted / In progress；完整 P2 仍 Pending | 最多 2 个 attempt、显式最坏预算、仅 exact stopped pre-running launch failure / post-ready `worker_exit`；因 effective handle/产物/取消/超时目标迁移边界拆为 B1/B2，当前先实现 B1 | 策略与安全出口已核对；尚无实现/测试通过证据 | 完成 B1 SQLite/Adapter/Supervisor/Workbench 与竞态回归；B2 仍 Pending |
| 2026-07-17 | P2-009B1 / D-012 | Accepted / In progress → Implemented → Verified；完整 P2 仍 Pending | SQLite v14 + Approval 1.1 两次串行/累计 2W、current Deepwave 1.5 exact stopped proof、active-term reservation/delivery、append-only attempt 2、exhaustion 终态、无 handle 双 attempt Trash/Purge、有界公开投影且无 retry mutation | Runtime 343/343、固定 venv Worker 32/32、launch-control 26/26、Web 47/47、Embedding 6/6、CTest 39/39、MCP 1/1、Node/治理与独立最终审计 PASS；未重复数值 FWI/CUDA | 推进 B2 post-ready `worker_exit` 的 effective handle/status/artifact/cancel/timeout 共同迁移；再完成剩余 reconciliation、SSE 与完整 P2 出口 |
| 2026-07-17 | P2-009B2 / D-012 | In progress → Implemented → Verified；完整 P2 仍 Pending | SQLite v15 exact worker-exit reservation/replacement/exhaustion、receipt-first terminal arbitration、private 1.3 attempt2、effective handle/status/artifact/cancel/timeout 共同迁移、旧 timeout retirement、mixed-lineage purge、只读 Web 与无 attempt3 | Runtime 360/360、TaskService 106/106、Supervisor Store 62/62、Runtime Supervisor 27/27、Adapter 60/60、Worker 32/32、launch-control 39/39、Web 47/47、Embedding 6/6、CTest 39/39、MCP 1/1、Node/治理与三轮独立审计 PASS；未重复数值 FWI/CUDA | P2 剩 2 个交付切片：完整 reconciliation 矩阵；SSE + 完整 P2 故障/代表性 CPU-CUDA 出口。全项目 P2–P6 滚动粗估约 6 |
| 2026-07-17 | P2 reconciliation 负向/不确定矩阵 | Pending → Implemented → Verified；完整 P2 仍 Pending | SQLite v16 append-only observation/exact-negative resolution；idle-fenced no-ready proof、durable transient/uncertain、Failed/no-refund/no-retry、六字段公开投影、Trash 可用且 purge fail closed | 首次 aggregate 捕获并修正 schema 15 陈旧断言；最终 Scientific Runtime 373/373、Web 47/47、Node UI、py_compile/diff 与治理 PASS；未运行数值 CPU/CUDA | P2 剩 1 个路线切片、2 个工作轮次：checkpoint/Waiting/resume；SSE + 完整 P2 故障/代表性 CPU-CUDA 出口。全项目 P2–P6 滚动粗估约 5 |
| 2026-07-17 | P2 收尾第 2 轮 checkpoint/Waiting/resume | In progress 工作轮次 → Implemented → Verified；路线切片与完整 P2 仍 In progress | current Algorithm/Adapter 1.6、SQLite v17 append-only wait/resume、首个 optimizer update 后无 pickle JSON+NPY checkpoint、same-live-attempt `Running → Waiting → Running`、Waiting 保留 Worker/execution/capacity flock 且 wall clock 继续、cancel/timeout 优先、只读 Workbench 且无 resume POST/SSE | 受影响模块 aggregate 457/457、py_compile/diff/continuity PASS；未运行完整回归、P2 aggregate、数值 CPU/CUDA、CTest 或 MCP | 第 3 轮完成 SSE 与完整 P2 fault/代表性 CPU-CUDA 阶段出口；工作轮次不扣减余量，P2=1、全项目 P2–P6≈5 不变 |
| 2026-07-17 | P2 收尾第 3 轮 SSE/阶段出口 | 剩余路线切片 In progress → Implemented → Verified；P2 Completed / Verified | scope-bound 只读 RunEvent SSE、pre-header prime、100 条/128 KiB 边界、连续 cursor/终态校验、keepalive/显式 rollover/停服唤醒；浏览器严格流解析、有限重连与 generation-level GET polling 回退；测试 discovery 包遮蔽修复 | 完整 P2 aggregate 592/592 + Node UI PASS；fresh CPU/CUDA Guided HTTP/SSE 均 Succeeded、首帧断线续传、same-attempt resumed、8 artifacts/6 PNG、finite/nonzero model update；治理检查 PASS | P2 结束；下一次从 P3 首个有界工作轮次继续。余量按公式 P2=0、P3–P6≈4、全项目≈4 |
| 2026-07-17 | D-002 / D-003 / D-011 治理纠错 | 未授权编号撤销；固定 P0–P6 阶段不变 | 删除 `419c41f` 误建的编号；新增编号必须显式批准；把估算范围与滚动递减并入 D-011，并建立基线/已交付/当前余量账本 | 完整计划/进度/continuity/短入口交叉审计；合同 32/32、continuity、launcher、runtime-secret、helper、diff check PASS | 后续每个 Verified 切片同步递减；任何上调、拆分或计划变化先取得用户明确同意 |
| 2026-07-17 | `D-*` 向前授权锁（非编号条目） | Implemented → Verified | 历史 D-001～D-012 逐字保留且不再追查；今后新增/删除/重编号/重排/修改须先展示单目标精确 diff/SHA-256，再由用户原样复制一次性 `D-AUTH`；根入口/continuity 双快照、全仓编号/标题与深层 AGENTS 绕过守卫；P6 为唯一全项目出口 | 12 个既有 D 正文 hash、2 个锁快照匹配；continuity、launcher、runtime-secret、合同 32/32、helper、diff check PASS；两轮独立只读审阅 | 按固定计划继续 P2；普通进度只写账本，任何 D 变更未获原样授权前停止 |
| 2026-07-17 | D-011 精简接续修订 | Accepted → Implemented / Verified（governance）；运行时阶段不变 | 路线切片与工作轮次分离；裸“继续 D-003”只推进一个有界轮次；三级验证、单次 aggregate、阶段出口全回归、审计轮次门和 80 行/8192 字节短入口 | D-011/双锁 hash、continuity、launcher、helper、Shell syntax、diff check PASS；未运行 runtime、CPU 或 CUDA | 按新默认从 P2 reconciliation 路线切片的首个有界工作轮次继续；轮次不改变 P2=2 或全项目约 6 的余量 |
| 2026-07-19 | P3 第 1 轮 DAG readiness kernel | P3 Pending → In progress；本轮 Implemented / Verified | hash-bound PlanGraph + exact node-state 纯决策核；stable topological layers、chain/fan-out/fan-in、active/Succeeded 不重选、分支局部传递 blocked proof 与异常 snapshot fail-closed；无持久化/派发/资源副作用 | 受影响测试 42/42、`py_compile` PASS；首次 continuity 检查逐项捕获并修复两个必需历史锚点遗漏；未运行 aggregate、完整回归、CPU/CUDA、CTest 或 MCP | 先独立复验 current 1.6 浏览器 Catalog normalization 风险；随后设计 durable per-node model；路线余量约 4 不变 |
| 2026-07-19 | P3 接续前 current 1.6 Catalog 浏览器兼容 | 风险复验 → Implemented / Verified；P3 仍 In progress | 浏览器 exact 1.4/1.5/1.6 Algorithm/Adapter binding、current 1.6 八输出 Plan 投影、mixed-version fail-closed 与 current/historical fixture 对齐；未开启 `dag` 执行能力 | Node UI PASS；服务端 current 1.6 path-free Catalog 对照 1/1 PASS；continuity/launcher/runtime-secret/`git diff --check` PASS；未运行 runtime aggregate、完整回归、CPU/CUDA、CTest 或 MCP | 下一轮界定 durable per-node identity/state/dispatch fence；本轮只是有界工作轮次，不改变 P3–P6 路线余量约 4 |
| 2026-07-19 | P3 第 2 轮 durable node state / claim candidate | 工作轮次 In progress → Implemented → Verified；P3 仍 In progress | SQLite v18 append-only 初始 Pending node facts；事务内 exact Plan/Approval/scope/readiness/active-term fence 与 non-executable candidate；replay/concurrency/reapproval/successor-term 审计；single-node/tamper fail closed | 最终受影响测试 33/33、`py_compile`、综合审阅与治理检查 PASS；首次 continuity 捕获并恢复短入口固定锚点；未运行 aggregate、完整回归、CPU/CUDA、CTest 或 MCP | 下一轮关闭 typed upstream artifact→downstream input/hash binding；candidate 仍 dormant；本轮非路线切片，不改变 P3–P6 路线余量约 4 |
| 2026-07-19 | P3 第 3 轮 typed artifact edge / canonical bytes binding | 工作轮次 In progress → Implemented → Verified；P3 仍 In progress | PlanGraph 1.2 typed dataset/node-output inputs；真实 artifact bytes/hash/size、lineage/fingerprint/port/type 的纯 canonical binding；历史 single-Dataset 精确兼容且无执行副作用 | 最终受影响测试 175/175、`py_compile` PASS；未运行 aggregate、完整回归、CPU/CUDA、CTest 或 MCP | 下一轮持久化 all-input binding；本轮非路线切片，不改变 P3–P6 路线余量约 4 |
| 2026-07-19 | P3 第 4 轮 durable all-input binding substrate | 工作轮次 In progress → Implemented → Verified；P3 仍 In progress | SQLite v19 current claim/Plan/Approval/term-bound 完整输入事实与 reserved Succeeded-output reader；dataset-root 正路径，node-output writer 仍 dormant | 最终受影响测试 50/50、`py_compile` PASS；未运行 aggregate、完整回归、CPU/CUDA、CTest 或 MCP | 下一轮实现 fixed-Adapter 单节点执行纵向闭环；本轮非路线切片，不改变 P3–P6 路线余量约 4 |
| 2026-07-19 | P3 第 5 轮 executable DAG vertical kernel | 工作包 In progress → Implemented → Verified；P3 仍 In progress | SQLite v20 exact admission/append-only node state；一个 dataset-root 节点复用 P2 Dispatcher/Adapter/Worker、receipt、RunEvent/reconciliation 完成 Queued/Running/Succeeded或Failed；并发/重放/term/receipt fail closed且无 attempt 2 | aggregate 运行 446 项：442 PASS、4 个 system-Python dependency errors；固定 Worker venv 失败子集 4/4 PASS；`py_compile`、综合审阅、diff/治理 PASS；未运行完整回归、CPU/CUDA、CTest 或 MCP | 下一工作包界定 successor admission、同 Task 多 intent 与资源所有权；公开 `dag=false`，路线余量约 4 不变 |
| 2026-07-19 | P3 第 6 轮 deterministic multi-node runtime / inherited CPU-GPU locks | 工作包 In progress → Implemented → Verified；P3 仍 In progress | SQLite v21 historical exact per-node intents、per-Task 串行 readiness→admission、fan-out/fan-in、durable descendant blocking、Task 聚合、active-term restart/cancel recovery 与 inherited CPU/GPU flock | aggregate 一次 516 项：511 PASS、5 个陈旧 fixture/assertion 失败；修正后失败子集 5/5 PASS；CPU 最大并行 2、GPU 1；一次综合审阅、pycompile/diff/治理 PASS；未跑完整/数值阶段出口 | 下一工作包为 node cache/checkpoint；不进入 Recipe/API/UI/P3 出口；public dag=false、全项目粗估约 4 不变 |
| 2026-07-19 | P3 第 7 轮 node cache / trusted lineage / DAG checkpoint | 工作包 In progress → Implemented → Verified；P3 仍 In progress | SQLite v22 canonical scope-local cache identity、executed Succeeded receipt + Adapter artifact 重验、append-only no-Worker hit fact、restart no-rerun、recursive Dataset lineage；exact DAG Waiting/resume 保持同 live Worker/process/attempt 且无 D-012 attempt | aggregate 一次 520 项：509 PASS、10 个 system-Python dependency errors + 1 旧 selector；固定 Worker venv/正确 scheduler 失败入口子集 17/17 PASS；一次综合审阅无阻断/高优先级发现；pycompile/diff/治理 PASS；未跑完整/正式 CPU-CUDA/P3 出口 | 下一工作包为固定 Recipe/Guided；不扩张 API/UI/public dag，不执行 P3 出口；用户明确保持全项目粗估约 4 |

记录规则：

- 写测试名称和结果，不使用“应该能用”作为证据；
- 不写 API Key、`.env`、私有 prompt、原始对话、模型内容或临时 job message；
- 临时 job ID、PID 和服务健康状态留在运行系统中，不作为长期 checkpoint；
- 失败、回滚和未完成项必须保留，不得为了看起来顺利而隐藏；
- commit hash 由 Git 历史提供，不在提交前猜测尚未产生的 hash。
