# D-003 当前工作入口

<!-- project-current-state: v1 -->

更新日期：2026-07-16

这是新 Codex 会话的**短上下文入口**，用于降低重复读取历史文档的 token 和等待时间。
它不替代长期决策、完整架构计划或进度账本；现场 Git、代码、测试和任务状态与本文冲突时，
现场证据优先，并在同一 checkpoint 修正本文和对应真源。

## 当前状态

- 活跃方向：`D-003` 双模式科研任务平台与持久任务内核。
- 活跃分支：`feature/scientific-agent-runtime`；基线为
  `feature/fwi-deepwave-2d-acoustic@ffeb5bc`。
- 已验证：P0、P1，以及 P2-001 任务发现/重开、P2-002 回收站、P2-003 本地结果永久
  删除、P2-004 有界启动 receipt 收养/一次状态追赶、P2-005A 控制面 fenced lease 与
  observation-only 持续状态泵、P2-005B 固定 Adapter 托管 Worker 的 staged launch、
  attempt/capacity fence、ready/heartbeat 证据，以及 P2-005C SQLite v9 fenced Worker
  evidence projection/late adoption；P2-006 又增加 SQLite v10 受监督派发授权、enqueue-only
  submit、可恢复 fenced scheduler、exact staged attempt 恢复与 current 1.4 legacy-private
  receipt 的受 fence 收养；P2-007 增加 SQLite v11 durable user-cancel admission、active-term
  delivery、exact Worker self-cancel 证明和 Guided Web 取消状态；P2-008 增加 SQLite v12
  immutable timeout window/active-term authorization/outcome、Worker v2 exact-stop 自停止和
  Guided Web 有界只读 timeout 投影；P2-009A 增加 SQLite v13 append-only positive receipt
  resolution/effective dispatch、current-term managed/private adoption、每周期最多一次 probe、同周期
  timeout/status catch-up 和 Guided Web 六字段只读 reconciliation 投影。
- 当前阶段：P2-009A positive receipt resolution/adoption 有界切片已 Verified；完整 P2 仍在进行，
  上述 P2 子项不得表述为完整 P2 已完成。
- 下一安全方向：先请用户确认 finite retry 的次数、最坏预算和可重试失败边界，再推进 P2-009B；
  负向/不确定 reconciliation 与 SSE 继续后置。
- 当前阻塞：无。工作树中的未提交内容可能属于另一个活跃窗口，必须现场检查并保护。
- 已接受 D-011：继续保留逐切片开发，但采用弹性中等粒度；约十余个剩余切片只是估算，允许
  按真实风险小幅增加，不为 migration/字段/单项测试制造路线切片，也不得无说明膨胀成几十个。
  多算法首先是独立可选工具，自动全流程不是当前验收要求；测试分级执行但阶段质量门不降低。
- `D-005` 提示词分类仍是 Proposed，不得表述为 Accepted。

具体状态与测试证据以 `docs/PROJECT_PROGRESS.md` 顶部阶段表、当前 checkpoint 和相应切片
章节为准。不要把本文中的日期或阶段摘要当成实时进程状态。

## 继续 D-003 时的最小读取集

用户说“开始 D-003”或“继续 D-003”时，Codex 默认只需：

1. 完整读取根 `AGENTS.md` 和本文；
2. 立即检查当前分支、`git status --short --branch`、最近提交和相关 diff；
3. 从 `docs/PROJECT_PROGRESS.md` 读取顶部状态/阶段表、当前 checkpoint，以及当前切片或
   下一动作对应的小节；
4. 从 `docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md` 读取当前阶段及其前置/出口条件；
5. 仅在当前任务涉及某项产品决策时，读取 `docs/PROJECT_CONTINUITY.md` 中对应的 `D-*`
   条目；Git 操作或 prompt 材料变化前完整读取 `docs/GIT_AND_PROMPT_POLICY.md`；
6. 用代码、测试、服务和任务现场证据核对摘要，再继续第一个未满足的出口条件。

以下情况才需要完整读取长文档：阶段切换或范围变更、决策冲突/替代、账本与现场不一致、
安全/发布审计、迁移/重构持续记录本身。不要为了形式在每个普通新会话重复加载完整历史。

## 当前不可破坏的边界

- Guided/Agent 两种入口共用唯一 Task Runtime；当前 P4 通用 Planner 尚未实现。
- P3 DAG 继续保留，但只在用户明确选择工作流或任务确需拆解时运行；算法注册本身不授权自动
  串联。P5 以独立发现/选择/执行多个真实插件为主，已有 Profile 内算法应保持薄接入。
- SQLite Task Store 是任务、计划、批准、状态和事件的唯一权威事实源；Redis 不是第二真源。
- 执行只接受注册数据 ID/版本/hash、固定算法版本和结构化参数；不接受任意服务器路径、
  shell、`extra_args` 或浏览器/LLM 传入的执行命令。
- 批准绑定规范化 `plan_hash`；参数、数据、算法、资源或计划变化使旧批准失效。
- 保留固定 Marmousi/Deepwave Adapter、MCP 白名单、路径校验和通用 JobBackend dry-run。
- 不创建扫描 `FWI_RUN_ROOT` 并执行任务的 watcher；运行目录只是受控输出/状态。
- P2-005B–P2-006 的执行/容量安全权威仍是同机内核 `flock`，不是 SQLite 授权行或 heartbeat
  新鲜度。SQLite v10 只允许 active Supervisor term 调度 current managed Adapter：pending 原子
  claim、dispatching/no-record 接管和 exact staged（`preparing` 或尚未取得 launch lease 的
  `launching`）同 attempt 恢复；leased/spawned/ready 只观察或收养，绝不二次 `Popen`。
  current 1.4 legacy private schema 1.0 的 exact launched receipt 可凭只读 proof 受 fence 收养；
  历史 Algorithm/Adapter identity 1.0–1.3、legacy CLI/MCP 仍不在首次派发/容量投影边界，升级前
  已终态任务不保证 evidence backfill。不完整 staging 保持 fail-closed，等待 reconciliation。
- P2-007 只允许 current Algorithm/Adapter 1.4、private schema 1.1、durable `dispatched`、最新
  v9 observation 为 spawned+ready+running 且 exact Worker 已发布 capability 的任务接受取消。
  HTTP 只持久化请求，Task 仍为 Queued/Running；active Supervisor term 只在 exact request +
  Worker ack + stopped heartbeat + idle execution `flock` 全部成立后提交 Cancelled。自然
  Succeeded/Failed 先到则 cancellation 为 superseded 且不改写终态。控制面不根据持久 PID
  发 signal；pending/staged、legacy private schema 1.0、公共历史 1.0–1.3 均不支持该入口。
- P2-008 已验证：只对 current Algorithm/Adapter 1.4、private schema 1.1、durable dispatched
  且能证明 v2 exact-stop capability 的最新 managed attempt 自动执行 wall-time timeout。时钟精确
  从 Store 对该 current Worker 的首条 durable `spawned + ready + running` observation 的
  `observed_at` 开始；deadline 到来且 active term 持久授权前 Worker mutation 为零。自然终态在
  authorization 前完成记 `not_triggered`；durable user-cancel admission 先赢记 `suppressed`；
  timeout authorization/request 后自然终态先赢记 `superseded`；只有 request + ack + stopped
  heartbeat + idle execution `flock` 全部成立才记 `timed_out` 并提交
  `Failed / WALL_TIME_EXCEEDED`。宽限耗尽时 exact Worker 自行退出，控制面不根据持久 PID 发
  signal；旧 Worker/历史任务保持 fail-closed。Workbench 只读投影精确包含 `state`、
  `wall_time_seconds`、`started_at`、`deadline_at`、`resolved_at`、`failure_code`、
  `terminal_status` 七个字段，不暴露 ID/hash/PID/path，也不存在 `POST /timeout` mutation。
- P2-009A 已验证：只对 immutable `reconciliation_required` 的 current Algorithm/Adapter 1.4
  接受两类 exact positive receipt：managed `spawned + ready + heartbeat`（仅 `running`、
  `succeeded`、`failed`）或 current Adapter 下 legacy-private schema 1.0 exact launched receipt。
  active fenced Supervisor term 才能追加 authorization/adoption/resolution；原 outcome 字节/hash
  不变，downstream cancel/timeout/trash 统一消费 effective dispatch。缺失、staged、部分、歧义、
  损坏、不匹配、launch/ticket failed、`stopped` 或公共 Adapter 1.0–1.3 均保持
  `action_required`。每个 Supervisor 周期最多执行一次 receipt probe；managed running 同周期
  arm timeout，terminal receipt 同周期刷新 task status。该路径不新建/替换 Worker、不扫描 run
  root、不按 PID/heartbeat age 推断、不退款或 retry。Workbench 只投影 `failure_code`、
  `recorded_at`、`state`、`result`、`evidence_kind`、`resolved_at`，不暴露 handle/hash/PID/path，
  也不存在 reconcile/retry/timeout POST mutation。该 checkpoint 的整体验证还修复了 same-key
  approve/submit 在首次 replay lookup 后由并发调用先入队的 late-replay race；不同 key/request
  hash 仍保持冲突。
- 不读取、打印或提交 `.env`、API Key、凭证、私有 prompt、模型、运行 artifact、数据库、
  日志、构建目录或缓存；不 push `main`、force-push 或重写已发布历史。
- Accepted、Implemented、Verified、Pending 必须分开报告；科学结论只限实际实验边界。

## CodeGraph 使用策略

CodeGraph 已作为全局 MCP 提供，本仓库 `.codegraph/` 是 Git 忽略的本地索引。使用原则：

- 理解架构、定位符号、追踪跨文件调用流时，优先一次 `codegraph_explore`；
- 查询单个定义位置使用 `codegraph_search`；修改影响使用 `codegraph_callers`、
  `codegraph_callees` 或 `codegraph_impact`；
- 不为同一问题先 CodeGraph、再无理由重复 `rg`/整文件读取；只有结果缺失、含糊或明确提示
  pending sync 时才补充；
- CodeGraph 索引会短暂滞后于写入。刚修改、pending sync 或未索引的 Markdown、Shell、SQL
  应直接读取；Git/diff、测试、运行状态和安全验证始终使用真实命令；
- CodeGraph 不可用或仓库未初始化时安全回退到 `rg`/定向读取，不因此阻塞任务，也不要求
  用户手动启动工具。

## 每个 checkpoint 的维护规则

若已验证阶段、下一安全动作、Accepted 决策或关键安全边界变化，必须在同一 checkpoint：

1. 更新对应的完整真源；
2. 同步本文的短摘要；
3. 运行 continuity/launcher/token-efficient bootstrap 测试；
4. 保持本文简短，不复制完整测试历史，不记录 PID、临时 job ID、密钥或聊天原文。

历史证据继续保留在完整文档中；短入口只承担路由和当前边界，不能用来静默改写历史。
