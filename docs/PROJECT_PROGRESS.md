# 科研任务 Agent Runtime 进度账本

<!-- project-progress-schema: v1 -->

- 最后更新：2026-07-15
- 活跃决策：`D-003`、`D-004`；`D-005` 仍为 Proposed
- 活跃分支：`feature/scientific-agent-runtime`
- 基线：`feature/fwi-deepwave-2d-acoustic@ffeb5bc`
- 总体状态：**Ready to start P0（治理静态验证通过，真实新会话冷启动演练待完成）**
- 当前阶段：**P0（尚未开始）**
- 下一动作：等待用户发出 README 中的 P0.1 开始口令或等价指示
- 当前阻塞：无
- 完整计划：`docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md`

本文是跨 Codex 会话的**执行进度真源**，但不是实时进程数据库。每个新会话必须先核对
Git、代码、测试、服务和 Task Store，再使用这里的状态。发生冲突时，实时证据优先，并在
同一变更中修正本文。

## 阶段状态

| 阶段 | 状态 | 已完成内容 | 验证证据 | 下一出口条件 |
|---|---|---|---|---|
| 准备 | Implemented | D-003 计划/进度、D-004 验证、D-005 提案与静态安全门 | launcher/continuity/runtime-secret/diff/SSH checkpoint：PASS | 新会话冷启动演练（非 P0 授权阻塞） |
| P0 最小 FWI 契约 | Pending | 无 | 无 | 七类可演进 Schema、Gate、API/Adapter 规范与测试 |
| P1 最小持久垂直切片 | Pending | 无 | 无 | SQLite TaskService→FWI Adapter→Guided Web |
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
- 当前通用 Orchestrator 仍以固定/单跳路由为主；尚无通用 TaskDraft、审批、DAG、幂等、
  服务端取消或重启恢复。
- D-003 已批准“双模式单任务内核、动态规划控制面 + 确定性执行面”。
- 2026-07-15 用户的风险评估已收紧顺序：最小 FWI Schema 先行，最小 SQLite TaskService
  提前到首个垂直切片，Redis 不作为任务事实源，P4 Agent Planner 后置。
- Git 动态快照（截至 2026-07-15）：当前实现分支基于 `ffeb5bc`；本地 `main`
  相对 `origin/main` 为 ahead 57 / behind 2。下次操作必须现场重查，不把快照当成永久事实。

### 尚未开始

- 没有创建 D-003 的 Schema、TaskService、数据库或新 Web API。
- 没有把现有 FWI 改造成通用 Algorithm Adapter。
- 没有实现 Guided/Agent 新 UI、审批卡、DAG 或子 Agent 调度。

### 准备阶段静态验证（2026-07-15）

- `bash tests/test_codex_project_launcher.sh`：PASS；
- `bash tests/test_project_continuity_contract.sh`：PASS；
- `bash tests/test_runtime_secret_isolation.sh`：PASS；
- `./scripts/codex-project.sh --check`：PASS；
- `git diff --check`：PASS；
- `bash tests/test_project_continuity_contract.sh` 内置的 Git-visible 禁止路径和高置信密钥/
  私钥扫描：PASS（除两处精确白名单的脱敏 C++ 安全 fixture 外无命中）。
- 首个 SSH checkpoint：`b5ac633` 已推送到 `origin/feature/scientific-agent-runtime`；
  推送后 `git rev-parse HEAD` 与 `git rev-parse @{upstream}` 一致。

这些结果只验证跨会话治理切片；本次未改动 C++、Python 数值路径或
Web 运行时，因此不把旧 FWI 测试记为本切片的重跑证据。
一个真实的新 Codex 会话仍需在首个请求时完成 branch/diff/ledger 冷启动 reconciliation；
在那之前不把“自动恢复”标记为端到端 Verified。
该演练是跨会话治理的非阻塞验收项：用户现在可以授权 P0.1；但新会话在修改 P0
文件前必须先完成该 preflight，并在账本中将准备阶段更新为 Verified/Completed。

### 下一可执行切片：P0.1

1. 审计现有 `ExperimentSpec`、`AlgorithmCard`、A2A Task、FWI config/manifest 的可复用字段；
2. 新建仅覆盖最小 FWI 链路的版本化 `DatasetRef`、`AlgorithmManifest`、`TaskDraft`、`PlanGraph`、
   `ApprovalDecision`、`RunEvent` 和 `ArtifactManifest` Schema；
3. 定义 `schema_version`、受控 `extensions`、规范化/hash、嵌套执行环境指纹（含
   commit/tree/dirty/diff 身份、deterministic flags 与已知非确定性）和版本演进规则；
4. 编写正例、缺/未知字段、越界资源、任意/无权数据、hash 不符、未注册/非 allowlist/
   版本未 pin/类型不匹配算法、有环 DAG、未确认字段、幂等键异常、side-effect policy 失败、
   批准过期/plan hash 不符和 dirty provenance 缺失测试；
5. 只落契约和测试，不改变当前 FWI 运行路径。D-005 未获批时不审计/迁移旧 prompt。

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

记录规则：

- 写测试名称和结果，不使用“应该能用”作为证据；
- 不写 API Key、`.env`、私有 prompt、原始对话、模型内容或临时 job message；
- 临时 job ID、PID 和服务健康状态留在运行系统中，不作为长期 checkpoint；
- 失败、回滚和未完成项必须保留，不得为了看起来顺利而隐藏；
- commit hash 由 Git 历史提供，不在提交前猜测尚未产生的 hash。
