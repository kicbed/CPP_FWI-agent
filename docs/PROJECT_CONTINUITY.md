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
- P1 Guided Web 从固定 Catalog 和严格七字段表单确定性组装 schema-valid `TaskDraft` 与单节点
  `PlanGraph`，展示真实 `task_id`/revision/`plan_hash`；用户可修改、批准或放弃 pre-runtime
  草稿，执行型快捷按钮和聊天文本不能再绕过确认卡直接提交。
- 普通 Web 聊天在 HTTP/A2A 和 gRPC bridge 两条 transport 固定携带 legacy-submit opt-out；
  Orchestrator 在 actual tool plan 后、MCP 执行前拒绝 `fwi_submit_demo`。字段缺省继续兼容旧
  CLI/MCP/A2A 客户端；这是防 classifier 漂移的 loopback 产品策略，不是身份认证。
- 批准后复用 SQLite TaskService、固定 Adapter 和 one-shot dispatch；页面只用 GET 轮询真实
  状态，成功后展示并受控下载标准 NPY/CSV ArtifactManifest。该链路已通过真实 CUDA 和 Web
  重启读取验证。
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
- 实现状态：**P0 + P1 最小持久垂直切片 Verified / P2 Pending**
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

- 已验证：获批架构计划、跨会话进度账本、Git 管理规则、冷启动 reconciliation 协议，
  以及一次真实新会话中的 branch/diff/ancestor/ledger/live-test reconciliation 演练。
- P0 已实现并验证：只覆盖最小 Marmousi/Deepwave FWI 链路的七类 Draft-07 JSON Schema、
  版本化 extensions、规范化 plan hash、批准失效与确定性 Gate 参考实现、执行指纹、状态/API/
  Adapter/Proto 规范、威胁模型和旧合同差异审计；现场审计补齐 draft 状态及 plan/draft task
  type、参数、资源一致性 Gate；P1.1a 时合同测试增至 27 项，P1.1b 后当前为 28 项。
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
- D-006/P1-006 已实现并验证：新 Guided/MCP/Worker 参数上限为 10000；当前
  `deepwave.acoustic_fwi@1.1.0` 与 Adapter `1.1.0` 承载扩大后的接受集合，旧 `1.0.0` Registry
  snapshot 保持不可变并可继续读取已有 dispatched task。Runtime 143/143 与既有数据库/HTTP
  兼容验收通过；默认次数、人工批准和 P2 边界不变。
- 尚未实现：P2 cancel/timeout/lease/heartbeat/retry/自动 reconciliation/SSE/任务列表与页面
  刷新恢复、P3 DAG、Agent Planner、通用 Algorithm SDK 和去噪链路。按用户要求当前停在
  P2 之前。
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
- 读取兼容强制 Algorithm/Adapter/fingerprint 只能是 `1.0↔1.0` 或 `1.1↔1.1`；新 dispatch
  仅允许当前 `1.1.0`。MCP runner 的公开合同版本也同步为 `1.1.0`。
- 不执行真实 10000 次回归，以免无必要地长期占用 GPU/CPU。Worker 每轮会原子重写并
  `fsync` 累计 `loss.csv`，高次数存在 O(N²) 写放大；7200 秒资源值不是 runtime timeout，
  P1 仍无运行中取消、timeout、checkpoint、retry 或 reconciliation。

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
