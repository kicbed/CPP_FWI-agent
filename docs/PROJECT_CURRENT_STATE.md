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
  observation-only 持续状态泵。
- 当前阶段：完整 P2 仍在进行；上述 P2 子项不得表述为完整 P2 已完成。
- 下一安全方向：先设计 staged Worker launch、唯一 attempt fence、跨进程 Worker capacity
  lease 与独立 heartbeat；证明接管不会重复启动后，才处理 pending/no-record 调度和
  cancel/timeout，retry、完整 reconciliation 与 SSE 继续后置。
- 当前阻塞：无。工作树中的未提交内容可能属于另一个活跃窗口，必须现场检查并保护。
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
- SQLite Task Store 是任务、计划、批准、状态和事件的唯一权威事实源；Redis 不是第二真源。
- 执行只接受注册数据 ID/版本/hash、固定算法版本和结构化参数；不接受任意服务器路径、
  shell、`extra_args` 或浏览器/LLM 传入的执行命令。
- 批准绑定规范化 `plan_hash`；参数、数据、算法、资源或计划变化使旧批准失效。
- 保留固定 Marmousi/Deepwave Adapter、MCP 白名单、路径校验和通用 JobBackend dry-run。
- 不创建扫描 `FWI_RUN_ROOT` 并执行任务的 watcher；运行目录只是受控输出/状态。
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
