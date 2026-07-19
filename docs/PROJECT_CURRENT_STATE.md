# D-003 当前工作入口

<!-- project-current-state: v1 -->

更新日期：2026-07-19

这是新会话的有界路由摘要，不替代进度账本、阶段计划或现场证据。冲突时以 Git、代码、
受影响测试和任务现场为准；普通实现与验证证据只写入 `docs/PROJECT_PROGRESS.md`。

## 当前路由

- 活跃分支：`feature/scientific-agent-runtime`；基线：`feature/fwi-deepwave-2d-acoustic@ffeb5bc`。
- Accepted：D-003 固定 P0→P1→P2→P3→P4→P5→P6；只有 P6 出口通过才算全项目完成。
- Verified：P0、P1、P2。P2-001～P2-009B2、reconciliation 矩阵、checkpoint / Waiting /
  same-live-attempt resume 及只读 SSE 均已实现；完整故障回归和代表性 CPU/CUDA HTTP/SSE E2E
  阶段出口通过，P2 已结束。
- In progress：历史入口“P3–P6 仍按固定顺序 Pending”已推进至 P3；纯 DAG readiness kernel 与
  SQLite v18 dormant 初始节点状态/active-term non-executable claim candidate，以及 PlanGraph 1.2
  typed edge/纯 bytes-hash binding、SQLite v19 active-term all-input binding substrate 均已验证；
  producer causal writer、可执行转换/派发、资源锁、cache/checkpoint 与 Recipe 未实现；`dag` 仍关闭，P4–P6 Pending。
- 滚动粗估：全项目 P2–P6 粗估基线约 12 个；已 Verified 8 个，当前约 4 个，P2 为 0、
  P3–P6 暂估约 4；这是弹性估算，不是配额。
- 当前阻塞：无；下一轮界定 fixed-Adapter producer Succeeded/output causal writer 与首个 node transition；
  v19 只有 dataset-root 可达，node-output 正路径仍无 writer，所有 binding 均不授权 admission/派发。
- D-005 仍为 Proposed，不迁移或删除现有 runtime prompt-like 文件。
- **最高优先级 D 锁**：D-001～D-012 及 D-LOCK 只可按单目标精确 diff/hash 和用户随后单独
  原样复制的一次性 `D-AUTH` 句修改；普通“继续/同意/固定/记录”无效。

## 继续 D-003 时的最小读取集与默认工作轮次

用户只说“继续 D-003”时，默认执行一个有界工作轮次，而不是一次完成整个路线切片或阶段：

1. 完整读取根 `AGENTS.md`、本文和现场 Git；只定向读取进度账本顶部/current checkpoint/当前
   切片、计划的当前阶段/出口，以及本轮确实相关的 D 条目。
2. 动手前简短声明本轮目标、一个风险边界、预计触及面、验证层级和停止点。
3. 实现时只跑受影响或失败测试；发现新的独立风险边界时停在安全交接点，不静默扩张。
4. 只有抵达路线切片出口，才在候选最终 tree 上运行一次相关集成 aggregate 并形成 Verified
   checkpoint；只有抵达阶段出口，才运行完整回归和适用的代表性 CPU/CUDA/E2E。
5. 默认一次综合审阅；第二轮只因具体高风险触发或未解决发现，第三轮及以后须先获用户明确批准。
6. 输出只报告 aggregate 一次；其已包含的子集不重复相加。未到切片出口则如实记录 `In progress`
   和下一安全动作。工作轮次不新增路线切片，也不改变滚动余量。

## 当前不可破坏的边界

- Guided/Agent 共用唯一 Task Runtime；SQLite Task Store 是任务、计划、批准、状态和事件唯一真源。
- 执行只接受注册数据 ID/版本/hash、固定算法版本和结构化参数；不接受任意路径、shell、
  `extra_args` 或浏览器/LLM 生成的执行命令。
- 保留固定 Marmousi/Deepwave Adapter、MCP 白名单、路径校验、批准绑定 `plan_hash` 和通用
  JobBackend dry-run；不实现扫描 `FWI_RUN_ROOT` 并执行任务的 watcher。
- 同机执行/容量权威仍是 inherited kernel `flock`；heartbeat/PID/文件缺失不授权替换、取消、
  timeout、retry 或终态推断。不完整、损坏、分歧或模糊证据一律 fail closed。
- D-012 只允许新 Approval 最多两个 append-only attempt，并只重试 exact stopped 的 pre-running
  launch failure 或 post-ready `worker_exit`；普通数值失败、timeout、cancel、成功和不确定状态不重试。
- current Deepwave Algorithm/Adapter 为 immutable 1.6，历史 1.0–1.5 保持精确兼容。checkpoint 只在
  首个 optimizer update 后创建无 pickle 的有界 JSON+NPY，并仅在同一 live Worker/attempt 内恢复；
  未实现跨进程 restart-from-checkpoint。
- Waiting 保留 Worker 及 execution/capacity `flock`，wall clock 继续且不消耗 retry；cancel/timeout
  优先，lost fence/Worker fail closed。Workbench 无 resume POST；SSE 仅从同一 Task Store 读取
  scope-bound RunEvent，并以有限重连后 GET polling 回退，不成为新的事实源或 mutation 入口。
- reconciliation 的精确负向证明只终结为 Failed，不退款、不重试；transient/uncertain 保持
  action_required。该负向证明可进入 Trash，但无授权清理协议时 purge 必须 fail closed。
- P3 v18 candidate 与 PlanGraph 1.2 artifact binding 均仅是审计/纯验证事实，不授权执行；DAG 只在
  用户明确选择工作流或任务确需拆解时运行；多算法默认独立可选，不自动串成流水线。P6 评测/观测/安全加固是全项目最终出口，P5 不是项目终点。
- 不读取、打印或提交 `.env`、密钥、凭证、私有 prompt、模型、运行 artifact、数据库、日志、
  构建目录或缓存；不 push `main`、force-push 或重写已发布历史。
- Accepted、Implemented、Verified、Pending 必须分开；科学结论只限实际实验边界。

## CodeGraph 使用策略

- 架构、符号、调用流和影响面优先一次有界 CodeGraph；结果缺失、pending sync 或 Markdown/
  Shell/SQL 才定向使用 `rg`/直接读取。Git、diff、测试和运行证据始终以真实现场为准。
- 不为同一问题无理由重复 CodeGraph、宽泛搜索和整文件读取；工具不可用时安全回退，不阻塞任务。

## checkpoint 记录规则

- `docs/PROJECT_PROGRESS.md` 是执行状态、验证证据和余量唯一真源；计划只保存阶段/出口，
  continuity 只保存获批决策，本文只保存路由、当前增量和不可破坏边界。
- 本文必须同时不超过 80 行和 8192 字节；不得复制完整实现史、测试日志或状态矩阵。
- 状态变化时只更新拥有该事实的最少文档，并运行 continuity/launcher/helper/diff 治理检查；
  治理检查不触发 runtime、CPU 或 CUDA 回归。
