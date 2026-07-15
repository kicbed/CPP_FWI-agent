# 科研任务 Agent Runtime 进度账本

<!-- project-progress-schema: v1 -->

- 最后更新：2026-07-15
- 活跃决策：`D-003`、`D-004`；`D-005` 仍为 Proposed
- 活跃分支：`feature/scientific-agent-runtime`
- 基线：`feature/fwi-deepwave-2d-acoustic@ffeb5bc`
- 总体状态：**P0 已完成并验证；P1 进行中，P1.1a Task Store、P1.1b Registry 基础与 P1.2a Deepwave Adapter 已验证**
- 当前阶段：**P1（In progress；P1.1 为 Partially implemented）**
- 下一动作：P1.1c 把 current registry/draft/plan/approval/budget、完整 Gate、submit
  idempotency、durable dispatch intent 与首个 queued event 合并为单 SQLite 事务；事务提交后
  才能调用 Adapter，不能在事务内直接 `Popen`
- 当前阻塞：无
- 完整计划：`docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md`

本文是跨 Codex 会话的**执行进度真源**，但不是实时进程数据库。每个新会话必须先核对
Git、代码、测试、服务和 Task Store，再使用这里的状态。发生冲突时，实时证据优先，并在
同一变更中修正本文。

## 阶段状态

| 阶段 | 状态 | 已完成内容 | 验证证据 | 下一出口条件 |
|---|---|---|---|---|
| 准备 | Verified | D-003 计划/进度、D-004、D-005 提案、安全门和真实新会话冷启动 reconciliation | branch/diff/ancestor/helper/live tests + launcher/continuity/runtime-secret：PASS | —（阶段完成） |
| P0 最小 FWI 契约 | Verified | 七类 v1 Schema、canonical plan hash、Gate、fingerprint、状态/API/Adapter/Proto 规范、威胁模型和旧合同审计；Gate 后续补强 draft/plan 及 manifest port 一致性 | 合同当前 28/28；P0 checkpoint 回归：CTest 39/39、FWI Runner 1/1、FWI Python 27/27、Web/embedding Python 13/13、UI/governance PASS | —（阶段完成） |
| P1 最小持久垂直切片 | In progress | P1.1a Task Store + P1.1b immutable Catalog/Registry + P1.2a 固定单节点 Deepwave Adapter；仍无产品 submit/queue 入口 | Adapter 17/17、Registry 22/22、TaskService 33/33、contract 28/28（组合 100/100）；真实 Adapter CUDA smoke、CTest 39/39、runner 1/1、FWI 27/27、Web/embedding 13/13、UI/governance PASS | 同事务 Gate/budget/submit intent/Queued、事务后 dispatch 与 Guided Web |
| P2 持久可靠性加固 | Pending | 无 | 无 | lease、取消、重试、恢复和 SSE 通过 |
| P3 确定性 DAG | Pending | 无 | 无 | 依赖、并行、资源锁和 checkpoint 通过 |
| P4 Agent Planner | Pending | 无 | 无 | 澄清、计划校验、审批和子 Agent 通过 |
| P5 算法 SDK | Pending | 无 | 无 | 去噪→QC→FWI 多算法流程通过 |
| P6 评测与加固 | Pending | 无 | 无 | 安全、故障、审计和部署验收通过 |

工作项允许状态：`Pending | In progress | Partially implemented | Implemented | Verified | Blocked`。阶段只有在所有必需
交付物达到 `Verified` 且出口测试通过后才可标记 `Completed`。方向获批、文档化、
实现和现场验证是四件不同的事。

## 当前 checkpoint

### 已确认事实

- 当前可运行基线是实验分支上的 Deepwave 二维声学 FWI MVP。
- 现有 FWI 固定白名单、参数校验、独立 Worker 和 artifact 路由是迁移时必须保护的安全边界。
- 当前通用 Orchestrator 仍以固定/单跳路由为主；独立 P1.1a/P1.1b 模块已有 SQLite 持久
  task/registry 基础，P1.2a 已有固定 FWI Adapter，但尚无把二者连接的 submit/queue 入口、审批
  API、DAG 调度、服务端取消或运行中断后的恢复。
- D-003 已批准“双模式单任务内核、动态规划控制面 + 确定性执行面”。
- 2026-07-15 用户的风险评估已收紧顺序：最小 FWI Schema 先行，最小 SQLite TaskService
  提前到首个垂直切片，Redis 不作为任务事实源，P4 Agent Planner 后置。
- Git 动态快照（截至 2026-07-15）：当前实现分支基于 `ffeb5bc`；本地 `main`
  相对 `origin/main` 为 ahead 57 / behind 2。下次操作必须现场重查，不把快照当成永久事实。

### 尚未开始或尚未完成

- 没有部署运行数据库、submit 事务或新 Web API；Catalog/Registry 只有持久组件、受信任
  bootstrap 和测试注册，不等于已部署服务；
- P1.1 的 submit 幂等只预留了 migration 约束，批准预算也只持久化未消费，尚无产品提交路径；
- Deepwave Adapter 只覆盖固定 `acoustic_fwi_2d` 单节点，尚未成为通用 Algorithm SDK；旧
  forward 因输出语义不匹配而未接入标准 Adapter；
- 没有实现 Guided/Agent 新 UI、审批卡、DAG 或子 Agent 调度。

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

### 下一可执行切片：P1.1c 原子 submit intent 与 Queued

1. 在单个 `BEGIN IMMEDIATE` 中读取 current task/draft/plan/approval/Registry/budget，执行完整
   Gate 和单 FWI 节点 capability guard，消费预算、写 submit idempotency、durable dispatch
   intent、`Queued` task snapshot 与第一个 `task_queued` event；
2. SQLite 提交成功后再由受控 dispatcher 调用 Adapter。不能在数据库事务内直接 `Popen`，也
   不能先启动 Worker 再补 task 状态；P1 对未完成 dispatch 明确失败/待 reconciliation，不提前
   声称具备 P2 自动恢复；
3. 然后接 Guided Web 的数据选择、参数确认和批准闭环；不提前实现 P2 cancel/lease/retry 或
   P3 DAG；D-005 未获批，继续不迁移旧 prompt-like 文件。

## 新会话恢复协议

新 Codex 在执行 D-003 相关工作前必须：

1. 完整阅读 `AGENTS.md`、`docs/PROJECT_CONTINUITY.md`、本进度账本和完整实施计划；
2. 检查当前分支、`git status`、未提交 diff 和最近提交，并验证 `ffeb5bc` 仍是
   当前 D-003 分支的 ancestor；
3. 如果分支不是 `feature/scientific-agent-runtime`、ancestor 不符或工作树不干净，不得自动
   switch/reset/rebase；先保护现有改动，对照 Git/代码/测试记录 reconciliation；
4. 运行内部只读状态刷新，核对服务和最近任务，但不把临时快照写成本项目进度；
5. 对照阶段表检查真实交付物与测试，不能只信状态文本；
6. 从第一个未满足出口条件的切片继续，不重复已经验证的工作；
7. 如果当前用户改变范围，以当前对话为准，并按决策协议更新 D-003/计划；
8. 工作结束前更新本文件的状态、证据、下一动作和阻塞，再按 Git 规则提交。

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

记录规则：

- 写测试名称和结果，不使用“应该能用”作为证据；
- 不写 API Key、`.env`、私有 prompt、原始对话、模型内容或临时 job message；
- 临时 job ID、PID 和服务健康状态留在运行系统中，不作为长期 checkpoint；
- 失败、回滚和未完成项必须保留，不得为了看起来顺利而隐藏；
- commit hash 由 Git 历史提供，不在提交前猜测尚未产生的 hash。
