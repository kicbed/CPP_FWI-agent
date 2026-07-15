# 项目持续开发与已采纳决策

更新日期：2026-07-15

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
- 实现状态：**Partially implemented / Pending**
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
8. 无论入口为何，真正执行都必须返回合法 `fwi_job_submitted` 和 `job_id`。只有文本说明、
   Python 代码或绘图示例时，前端必须明确显示“任务未提交”。

### 当前已经实现并验证

- 固定白名单 `marmousi_94_288`、设备 `cpu|cuda`、反演迭代整数 `1–100`。
- 自然语言中的标准表达及一组受限口语表达可确定性映射到 MCP；例如“做一下 marmousi
  的反演测试，迭代 50 次，完成后展示结果”。
- MCP 通过固定参数和安全进程启动器异步运行 Worker，不接受模型路径、shell 或
  `extra_args`。
- Web 可显示 `queued/running/succeeded/failed`、自动轮询状态、加载 manifest、指标和
  六张结果图。
- Web 在执行请求没有合法 job 回执时显示“FWI 任务未提交”，不会把生成代码当成执行。
- 2026-07-15 使用上述口语请求实际完成过一次 50 次 CUDA 端到端验证；运行目录和
  job ID 属于本机临时状态，不写入版本库。该验证只是当前小型合成 Marmousi 配置下的
  链路证据，不是普遍反演效果声明。

### 尚未实现

- 通用 `FWIJobDraft` 数据结构和服务端草稿生命周期。
- Web 参数确认/修改/批准卡片。
- `/fwi` 确定性命令解析器。
- 用户可切换且可审计的“每次人工审批 / 本会话授权 Agent”执行策略。

在这些项目真正完成测试前，不得向用户声称已经具备交互式审批或全权代理模式。

## D-002：跨会话建议采纳和决策维护规则

- 决策状态：**Accepted**
- 实现状态：**Implemented / Verified**（通过根目录 `AGENTS.md`、本文和隔离测试）
- 用户确认日期：2026-07-15

### 已采纳规则

1. 新 Codex 会话进入本仓库后必须先阅读本文，再检查当前 Git 和运行状态。
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
8. 用户说“保留这个”“以后都这样”或“记录下来”时，视为对该决定的明确批准；Codex
   应在同一次修改中自动更新本文，不把选择记录文件或运行辅助命令的工作转交给用户。

### 当前实现与验证

- 根目录 `AGENTS.md` 是普通 Codex 新会话的自动入口；它要求 Codex 在首个用户请求时自行
  阅读本文、检查 Git/diff，并验证相关代码、测试、服务和任务状态。用户无需执行脚本。
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
- 实现状态：**Governance implemented / Runtime pending**
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
8. Deepwave FWI 是第一个标准 Adapter 和回归基线；后续以真实去噪、质量评价与 FWI 任务图
   验证多算法接入。
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

D-003 是 D-001 的通用化，不替代 D-001。现有 FWI 白名单、参数边界、路径校验、MCP 安全
启动和“未提交不得伪装为执行”规则，在新任务内核通过等价或更强测试前继续有效。

### 当前实现边界

- 已实现并静态验证：获批架构计划、跨会话进度账本、Git 管理规则和冷启动
  reconciliation 协议/启动契约。
- 待验证：一个真实的新 Codex 会话仍需完成冷启动 reconciliation 演练；静态测试不冒充
  该端到端验收。
- 尚未实现：P0–P6 的 Schema、TaskService、Dataset Catalog、审批 API、DAG、恢复调度、
  Guided/Agent 新 UI、通用 Algorithm SDK 和去噪链路。
- 不得因本文存在就声称下一代任务平台已经可用；每项真实状态见进度账本并现场验证。

## D-004：Git checkpoint 管理

- 决策状态：**Accepted**
- 实现状态：**Partially implemented / first SSH checkpoint pending**
- 用户确认日期：2026-07-15
- 完整规则：`docs/GIT_AND_PROMPT_POLICY.md`

### 已采纳规则

1. D-003 从 FWI 已验证基线创建独立 `feature/scientific-agent-runtime` 分支，按可验证
   纵向切片提交并通过 SSH 推送，不直接修改或推送 `main`。
2. 提交前检查相关 diff、测试、进度账本、暂存清单和敏感信息；不提交构建、环境、模型、
   数据、运行状态、数据库、日志或缓存。

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

## 新会话的最小检查清单

```text
1. 用户在仓库中正常打开 Codex 后直接提问，无需执行项目脚本
2. Codex 依据根 AGENTS.md 自动完整阅读 docs/PROJECT_CONTINUITY.md
3. D-003 工作还必须完整阅读架构计划、PROJECT_PROGRESS 和 Git/提示词规则
4. Codex 自行检查当前分支、git status、git diff，保护用户已有改动
5. 现场核对进度账本，区分 Accepted / Implemented / Verified / Pending
6. 从依赖已满足的 next safe action 继续，不依赖旧状态快照或重复已验证工作
7. 用户明确要求保留的决定自动写入本文；其他新建议先征得同意
8. 修改后更新进度和测试证据；不写入秘密、模型和运行产物
9. D-005 在用户明确确认前仍是 Proposed，不得报告为 Accepted
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
| 2026-07-15 | D-004 | 新增并标记 Accepted | 用户明确要求 Codex 管理 Git，使用独立实现分支和可验证 checkpoint |
| 2026-07-15 | D-005 | 新增并标记 Proposed | 用户对 AI prompt 是否上传仍有保留；记录可审批提案，本次先采用不上传临时 prompt 的安全默认 |
