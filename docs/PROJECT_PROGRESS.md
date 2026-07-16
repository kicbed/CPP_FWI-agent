# 科研任务 Agent Runtime 进度账本

<!-- project-progress-schema: v1 -->

- 最后更新：2026-07-16
- 活跃决策：`D-003`、`D-004`、`D-006`、`D-007`、`D-008`、`D-009`、`D-010`、`D-011`；`D-005` 仍为 Proposed
- 活跃分支：`feature/scientific-agent-runtime`
- 基线：`feature/fwi-deepwave-2d-acoustic@ffeb5bc`
- 总体状态：**P0 + P1（含 P1-008）已验证；P2-001 有界发现/重开已验证，P2-002 回收站、
  P2-003 永久删除、P2-004 启动 receipt 收养、P2-005A 控制面 Supervisor lease/连续状态泵与
  P2-005B 固定 Adapter 托管 Worker launch fence、P2-005C fenced Worker 证据投影/late adoption、
  P2-006 可恢复 fenced scheduler/受监督首次派发与 P2-007 exact-attempt user cancellation 均为
  有界 Verified；完整 P2 Pending**
- 当前阶段：**P2-007 exact-attempt user cancellation 已验证；完整 P2 继续进行**
- 下一动作：先确认 timeout 的终态语义、计时起点和 force policy，再实现 timeout；随后推进
  有限 retry 与 reconciliation resolution
- 交付粒度：D-011 采用弹性中等切片，约十余个只是估算；保留逐切片验证和阶段质量门，不为
  migration/字段/单项测试单独制造路线切片，确有安全/验证证据时允许合理增加并记录原因
- 当前阻塞：P2-007 无阻塞；timeout 产品语义等待用户决定
- 完整计划：`docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md`

本文是跨 Codex 会话的**执行进度真源**，但不是实时进程数据库。每个新会话必须先核对
Git、代码、测试、服务和 Task Store，再使用这里的状态。发生冲突时，实时证据优先，并在
同一变更中修正本文。

## 阶段状态

| 阶段 | 状态 | 已完成内容 | 验证证据 | 下一出口条件 |
|---|---|---|---|---|
| 准备 | Verified | D-003 计划/进度、D-004、D-005 提案、安全门和真实新会话冷启动 reconciliation | branch/diff/ancestor/helper/live tests + launcher/continuity/runtime-secret：PASS | —（阶段完成） |
| P0 最小 FWI 契约 | Verified | 七类 v1 Schema、canonical plan hash、Gate、fingerprint、状态/API/Adapter/Proto 规范、威胁模型和旧合同审计；Gate 后续补强 draft/plan 及 manifest port 一致性 | 合同当前 31/31；P0 checkpoint 回归：CTest 39/39、FWI Runner 1/1、FWI Python 27/27、Web/embedding Python 13/13、UI/governance PASS | —（阶段完成） |
| P1 最小持久垂直切片 | Verified | 既有 P1 Task Store/Registry/Adapter/atomic submit/Guided Web 全闭环及 D-006/D-007；D-008/P1-008 增加 Conversation/Task 可选引用、无级联本地对话删除和当前 Algorithm/Adapter 1.4 的 2 数值 + 6 PNG 结果画廊 | Runtime 165/165、Worker 28/28、Web 29/29、Embedding 6/6、CTest 39/39、MCP 1/1 及 UI/治理 PASS；fresh v6 CUDA 10 events、8 artifacts/6 PNG、数值更新和重启不变性 PASS | —（P1 及当前维护切片完成） |
| P2 持久可靠性加固 | In progress（P2-001–P2-007 有界切片 Verified；完整 P2 Pending） | 在既有调度、控制面 lease 与 Worker kernel fence 上，P2-007 增加 SQLite v11 durable cancel、active-term delivery、exact Worker self-cancel 证明及 Guided Web 状态 | P2-007 最终自动化/治理证据见本页对应小节；运行 CPU smoke，未运行 CUDA | timeout、有限重试、完整 reconciliation 与 SSE |
| P3 确定性 DAG | Pending | 无 | 无 | 依赖、并行、资源锁和 checkpoint 通过 |
| P4 Agent Planner | Pending | 无 | 无 | 澄清、计划校验、审批和子 Agent 通过 |
| P5 算法 SDK | Pending | 无 | 无 | 受控数据可匹配多个独立算法；新增真实插件无需 Orchestrator 关键词且 conformance 通过 |
| P6 评测与加固 | Pending | 无 | 无 | 安全、故障、审计和部署验收通过 |

工作项允许状态：`Pending | In progress | Partially implemented | Implemented | Verified | Blocked`。阶段只有在所有必需
交付物达到 `Verified` 且出口测试通过后才可标记 `Completed`。方向获批、文档化、
实现和现场验证是四件不同的事。

## 当前 checkpoint

### 已确认事实

- 当前可运行基线是实验分支上的 Deepwave 二维声学 FWI MVP。
- 现有 FWI 固定白名单、参数校验、独立 Worker 和 artifact 路由是迁移时必须保护的安全边界。
- 当前通用 Orchestrator 仍以固定/单跳路由为主；P1 的历史 checkpoint 以 atomic submit 后的
  one-shot dispatch 形成最小闭环。P2-006 当前产品路径已经改为 HTTP submit 只做 prepare/Gate 与
  `Queued/pending` 原子 admission，active-term Runtime Supervisor 才能首次派发 current 1.4。
  Worker 执行和同机容量仍由 P2-005B inherited `flock` 围栏；SQLite v10/v11 不是 Worker lease。
  P2-007 已提供有界 exact-attempt user cancel；当前仍无 DAG、timeout、task retry、
  reconciliation resolution 或 SSE。
- D-006/P1-006 已把固定 FWI 的显式整数上限扩展为 10000；该 checkpoint 使用
  Algorithm/Adapter `1.1.0`。D-007 的 `1.2.0` 是不可变六参数历史快照，`1.3.0` 是已验证
  checkpoint；D-008 当前新提交使用 `1.4.0`，旧 `1.0.0`–`1.3.0` manifest、persisted Plan
  与已 dispatch 收据保持严格读兼容。
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
- D-003 已批准“双模式单任务内核、动态规划控制面 + 确定性执行面”。
- D-011 已批准：保持阶段、依赖、测试和安全质量，减少过细切片但不取消慢速逐步开发；切片数
  是弹性估算而非固定 12。多算法表示多个独立可选工具，自动串联完整处理流程不作为当前出口。
- 2026-07-15 用户的风险评估已收紧顺序：最小 FWI Schema 先行，最小 SQLite TaskService
  提前到首个垂直切片，Redis 不作为任务事实源，P4 Agent Planner 后置。
- Git 动态快照（截至 2026-07-15）：当前实现分支基于 `ffeb5bc`；本地 `main`
  相对 `origin/main` 为 ahead 57 / behind 2。下次操作必须现场重查，不把快照当成永久事实。

### 尚未开始或尚未完成

- P1 已接入根启动器下的本机 Guided Web/API 与默认仓库外 SQLite Task Store；它无用户
  认证，只在 loopback 绑定时启用，不是容器/远程多用户部署方案；
- P1.1c 历史 checkpoint 已实现后端 submit 幂等、预算消费、durable intent 与 Queued；P2-006
  当前 submit 已改为 enqueue-only，current 1.4 pending/no-record 首派由 active Supervisor term
  负责。既有 immutable `reconciliation_required` outcome 的 resolution、退款和 task retry 仍未解决；
- Deepwave Adapter 只覆盖固定 `acoustic_fwi_2d` 单节点，尚未成为通用 Algorithm SDK；旧
  forward 因输出语义不匹配而未接入标准 Adapter；
- P2-006 已让 Web 进程在 scope-level fenced term 下调度并持续更新 task，不依赖浏览器 GET；
  startup inventory 现为无 lease 的纯只读路径，浏览器单任务 GET 仍可走既有单调 status CAS，故不能
  声称所有 runtime write 都由唯一 Supervisor 独占。kernel `flock` 仍是执行/容量权威，heartbeat
  不是 lease 或 takeover 信号；standalone CLI/C++ MCP 不在该投影/容量边界，升级前或首次扫描前
  已终态的 task 不保证 evidence backfill。不完整 staging、确定性 Adapter/receipt 错误仍需未来
  reconciliation；timeout、有限 retry、完整 reconciliation/SSE、P3 DAG 和 P4 Agent Planner
  仍未实现。P2-007 不支持 pending/staged、legacy private schema 1.0 或公共 Adapter 1.0–1.3；
  `resources.wall_time_seconds` 只是资源策略字段，不是 runtime timeout。
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

### 完整 P2 仍 Pending

P1 的既有必需交付与退出测试仍为 Verified。用户后续明确授权的 P2-001 任务发现/重开与
P2-002 可恢复任务回收站、P2-003 本地结果永久删除和本次 P2-004 有界 receipt 收养/状态追赶
以及 P2-005A 控制面 Supervisor、P2-005B 固定 Adapter 托管 Worker launch fence、P2-005C
fenced Worker 证据投影/late adoption、P2-006 fenced scheduler/受监督首次派发和 P2-007
exact-attempt user cancellation 均已 Verified。P2-006 已关闭 current managed pending/no-record
首派和 exact staged 同 attempt 恢复；P2-007 只关闭上述 exact current managed attempt 的用户取消。
timeout、task retry、`reconciliation_required` resolution 与 SSE 仍未实现。服务器 transcript
永久删除、SQLite 审计历史硬
删除和备份/外部副本清理仍 Pending。
D-005 仍未获批，没有迁移或删除旧 prompt-like 文件。

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
8. 从第一个未满足出口条件的切片继续，不重复已经验证的工作；
9. 如果当前用户改变范围，以当前对话为准，并按决策协议更新 D-003/计划；
10. 工作结束前同步短入口、本文件的状态/证据/下一动作，再按 Git 规则提交。

## 每个开发切片的记录格式

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
| 2026-07-16 | D-011 / PLAN-002 | Proposed → Accepted → Implemented / Verified（governance）；运行时阶段不变 | 弹性中等切片、独立多算法选项、显式工作流与分级测试规则；P5 不再强制自动去噪/QC/FWI 链 | continuity、launcher、helper、runtime-secret isolation、diff check：PASS；仅文档/治理变更，未运行数值 FWI/CUDA | 继续当前 P2 可恢复 fenced scheduler；仅在真实安全/验证边界出现时增加切片并记录依据 |
| 2026-07-16 | P2-006 | Pending → Implemented → Verified；完整 P2 仍 Pending | enqueue-only submit、SQLite v10 active-term dispatch authorization、pending/no-record 首派、exact staged 同 attempt 恢复、current 1.4 legacy-private receipt fenced adoption 与 lease 前只读 inventory | Runtime 251/251、固定 venv Worker 非数值 + launch-control 37/37、Web 46/46、Embedding 6/6、CTest 39/39、MCP 1/1、Node UI、continuity/launcher/runtime-secret/helper/diff PASS；未运行数值 FWI/CUDA | 下一步基于 exact attempt/kernel fence 实现 cancel/timeout；随后有限 retry、reconciliation resolution 与 SSE |
| 2026-07-16 | P2-007 | Pending → Implemented → Verified；完整 P2 仍 Pending | SQLite v11 exact request/active-term authorization/outcome、Worker-published capability 与 self-cancel、ack + stopped heartbeat + idle execution fence 终态证明、自然终态 superseded 竞争和 Guided Web requested/cancelled/superseded | Runtime 271/271、固定 venv Worker 31/31（CPU smoke 3/3）+ launch-control 17/17、Web 46/46、Embedding 6/6、CTest 39/39、MCP 1/1、Node UI、治理/编译/diff PASS；未运行 CUDA | 先由用户确认 timeout 的终态语义、计时起点和 force policy；随后有限 retry、reconciliation resolution 与 SSE |

记录规则：

- 写测试名称和结果，不使用“应该能用”作为证据；
- 不写 API Key、`.env`、私有 prompt、原始对话、模型内容或临时 job message；
- 临时 job ID、PID 和服务健康状态留在运行系统中，不作为长期 checkpoint；
- 失败、回滚和未完成项必须保留，不得为了看起来顺利而隐藏；
- commit hash 由 Git 历史提供，不在提交前猜测尚未产生的 hash。
