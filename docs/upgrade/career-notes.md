# Career Notes

This file records architecture and technical talking points for resumes,
interviews, project reports, and lab demos. Keep it factual. Do not claim
features that are only planned.

Update this file when an upgrade changes architecture, adds a major component,
adds tests, improves deployment, or changes the product story.

## One-Line Project Pitch

FWI-first research computing multi-agent workbench built with C++, gRPC, A2A,
MCP, RAG, Redis memory, and a Web/CLI interface.

Current status:

- Multi-agent communication framework and FWI research assistant prototype.
- Supports gRPC/A2A communication, agent registry, MCP tool integration,
  Agent-RAG routing, Tool-RAG, Redis-backed conversation memory, local knowledge
  retrieval, and Web UI.
- Includes a Code Agent MVP executable for read-only code Q&A, error diagnosis,
  project inspection, and patch proposal prompts; automatic patch application
  is not enabled.
- Includes an initial `AlgorithmCard` C++ research model for JSON-backed
  algorithm metadata, registry loading, seed cards, and dry-run backend
  validation.
- Includes `ExperimentSpec`, `JobSpec`, and `DryRunBackend` models for safe
  experiment planning without submitting jobs.
- Includes a v0.3 `ResearchKnowledgeNote` and `ResearchKnowledgeBase` for
  JSON-backed paper, algorithm, experiment, and failure-case notes with
  deterministic local retrieval by method, failure mode, parameter advice, and
  dataset.
- Includes an initial v0.4 `PlannerContext` layer that deterministically
  combines AlgorithmCards, ResearchKnowledge notes, failure-mode evidence, and
  parameter advice before the Experiment Planner calls an LLM.
- Includes a v0.4 `PlannerAnswer` layer that turns PlannerContext into
  structured algorithm recommendations, assumptions, parameter tables, risk
  analysis, ExperimentSpec JSON, dry-run JobSpec text, and reproducible
  experiment records.
- Includes a v0.5 Lab Agent Workbench UI that exposes sessions, AlgorithmCards,
  experiment history, route trace, tool calls, parameter tables, ExperimentSpec,
  JobSpec, dry-run state, and service status panels in the browser.
- Includes a v0.6 Lab Code Adapter for local config-template loading, dry-run
  config previews, supplied log parsing, loss curve extraction, common failure
  recognition, and Planner-facing diagnostic summaries.
- Includes a v0.7 `JobBackend` reservation layer so future execution backends
  share the same validate/render/explain/type contract while the only enabled
  backend remains `DryRunBackend`.
- Includes a v0.8 server-backend safety design that defines the threat model,
  approved-template boundary, workspace isolation, lifecycle records, artifact
  collection, and audit requirements before real execution is enabled.
- Includes v0.8 C++ server-job safety models and tests for dry-run default
  requests, approved template validation, workspace traversal rejection, and
  in-memory lifecycle records.
- Includes a Milestone 11 preflight approval gate for future real backend
  selection; the gate requires lab approval, approval reference, workspace root,
  credential reference, authorization policy, audit retention, and operator
  contact, rejects placeholder approval values, validates concrete authorized
  submitters, defines metadata-only job audit events and in-memory audit log
  validation, and exposes a unified preflight readiness report, but still does
  not enable real execution.
- Includes a v0.9 backend readiness review renderer that turns
  `BackendPreflightReport` into stable operator-facing text with metadata
  readiness, runtime enablement state, validation errors, runtime blockers, and
  safety boundaries.
- Includes v0.9 non-executing review previews for dry-run submission packets,
  audit logs, workspace paths, and artifact paths without enabling real
  execution.
- 新增中文 M11 实验室后端决策包和流程指南，说明 M11-T1 选择真实后端前
  必须具备的批准、凭据、workspace、授权、配额/operator、审计、artifact、
  回滚和实现顺序信息。
- 新增单服务器账号接入学习和设计文档，把当前实验室初步阶段收敛为一个服务器账号、
  固定 workspace、固定 approved template、dry-run review packet 和 fake lifecycle。
- Includes v0.10 single-server account preparation models and tests for
  `SingleServerProfile`, `SingleServerJobTemplate`, `SingleServerReviewRequest`,
  validation, and dry-run review packet rendering without connecting to a server.
- Includes v0.11 safe operation policy models and tests for `lab_root`,
  `lab_user`, `readonly`, role-based operation allowlists,
  `DeleteReviewRequest`, `DeleteReviewPacket`, and deletion dry-run review
  rendering without real deletion.
- Includes v0.12 single-server fake lifecycle models and tests for requested,
  reviewed, approved, rejected, queued, running, succeeded, failed, and
  cancelled state flow without connecting to a server, executing commands, or
  creating workspaces.
- Includes v0.13 workspace planner models and tests for preview-only
  workspace, run directory, log, and artifact paths with path traversal,
  absolute escape, dangerous root, and protected-label validation.
- Includes v0.14 approved-template run packet models and tests for combining
  single-server profiles, approved templates, structured parameters, workspace
  plans, lifecycle ids, resource limits, and explicit non-execution flags while
  rejecting free-form commands and unapproved parameters.
- Includes v0.15 internal sanity runner gate models and tests for fixed
  allowlisted runner review packets, timeout metadata, stdout/stderr capture
  plans, artifact path validation, audit event metadata, and rejection of
  free-form commands, deletion, credential reads, SSH, Slurm, PBS, and remote
  access.
- v1.0 internal preview is complete for the review-only single-server workflow:
  completion audit, user guide, operator runbook, demo script, consolidated
  test report, and learning summary are available.
- 新增 v0.10 单服务器账号接入准备设计和实现计划，把下一步落到
  `SingleServerProfile`、`SingleServerJobTemplate`、`SingleServerReviewRequest`
  和 dry-run review packet，仍不连接服务器、不读取凭据、不执行命令。
- 新增 v1.0 internal preview 路线图，把后续工作拆成 safe operations、
  fake lifecycle、workspace planner、approved template run packet、sanity-check
  runner gate 和内部预览收口，同时把个人提示词和 agent 执行计划改为本地忽略资料。
- Includes v0.2 demo and test-report documentation for FWI Q&A, Code Agent
  routing, and dry-run Experiment Planner smoke testing.
- Real CUDA/MPI or cluster execution is not enabled yet.

## Architecture Talking Points

Current architecture:

- Client layer: CLI client, Web UI, gRPC client, HTTP bridge.
- Service layer: gRPC server and AIQueryService.
- Protocol adapter layer: A2A adapter converts RPC requests into A2A JSON-RPC
  messages.
- Orchestration layer: AI Orchestrator routes requests to specialized agents.
- Agent layer: Math, FWI Theory, FWI Teaching, General Research, Code Agent,
  and Experiment Planner Agent.
- Tool layer: MCP integrated server and plugins such as calculator and FWI
  metadata tools.
- Retrieval layer: Agent-RAG for dynamic agent selection, Tool-RAG for tool
  selection, local FWI knowledge retrieval, and structured v0.3 research
  knowledge retrieval by note type, method, failure mode, parameter advice, and
  dataset.
- Planner context layer: v0.4 deterministic request inference and context
  construction select AlgorithmCards and local research notes for the Planner
  prompt before LLM generation.
- Memory layer: Redis-backed session history, agent memory, and task state.

Current v0.2 state:

- Lab Agent MVP scope is complete: Code Agent, AlgorithmCard registry,
  ExperimentSpec, JobSpec, DryRunBackend, Experiment Planner skeleton, demo
  script, and test report.

Current v0.3 state:

- Research Knowledge Base is complete: typed local JSON notes under
  `resources/research_knowledge`, deterministic C++ loading, validation, and
  tests for method, failure-mode, parameter-advice, and dataset retrieval.

Current v0.4 state:

- Experiment Planner is complete for the v0.4 scope: `PlannerContext` infers
  FWI planning signals from a request, retrieves matching AlgorithmCards and
  knowledge notes, and `PlannerAnswer` creates a structured dry-run plan and
  reproducible experiment record.

Current v0.5 state:

- Lab Workbench UI is complete for the v0.5 scope: the browser surface keeps
  chat interaction but adds research-workbench panels for AlgorithmCards,
  experiment history, route/tool inspection, parameter planning,
  ExperimentSpec/JobSpec rendering, dry-run boundaries, and local service
  status.

Current v0.6 state:

- Lab Code Adapter is complete for the v0.6 scope: deterministic local parsing
  covers config templates, dry-run config previews, supplied log text, loss
  curves, common failure recognizers, and Planner-facing summaries. No real
  execution backend was added.

Current v0.7 state:

- JobBackend Reservation is complete for the v0.7 scope: `JobBackendType`
  names `dry_run`, `local`, `ssh`, `slurm`, and `pbs`; shared backend parsing
  and validation reject all non-`dry_run` or unknown values at runtime; and
  `AlgorithmCard` validation uses the same guard. `DryRunBackend` remains the
  only concrete and enabled backend.

Current v0.8 state:

- Server Backend Safety Foundation is complete for the v0.8 scope: the design
  separates dry-run previews from future submission APIs, and the C++ research
  library now has server-job request and record models, approved template
  validation, workspace traversal rejection, and in-memory lifecycle helpers.
  No real execution backend is enabled yet.

Current Milestone 11 preflight state:

- Backend approval decision validation exists as a metadata-only safety gate.
  It records the prerequisites needed before selecting a real backend, while
  the shared runtime backend guard continues to reject `local`, `ssh`, `slurm`,
  and `pbs`. The preflight gate also rejects placeholder values such as `TBD`,
  `pending`, `unknown`, `n/a`, or `none` so an incomplete approval packet cannot
  look valid. It now also requires a concrete authorized submitter list and can
  reject a `JobSubmissionRequest.user_id` that is not named in the approval
  decision. The preflight layer also has a metadata-only `JobAuditEvent` model
  for future submission, rejection, lifecycle, artifact, and operator-note
  records, plus an in-memory `JobAuditLog` helper that validates event batches
  before a future persistence layer exists. `BackendPreflightReport` now
  aggregates these checks and separates metadata readiness from runtime backend
  enablement.

Current v0.9 state:

- Backend Readiness Review is complete for the v0.9 scope. The review helpers
  consume structured M11 preflight metadata and render readiness status,
  dry-run submission packets, audit log previews, and workspace/artifact plans
  without submitting jobs, persisting audit records, creating directories, or
  enabling any reserved backend.

Current M11 decision package state:

- M11 决策包和实验室流程指南现在是中文优先的学习与评审文档。它们把
  local wrapper、SSH、Slurm、PBS 作为仅供评审的候选项对比，并列出选择
  真实后端前必须确认的批准、凭据、workspace、授权、配额/operator、
  审计、artifact、operator 联系人、回滚和实现顺序。
- 针对当前实验室只是一个服务器账号、自己或小组内部先跑的现实情况，新增了
  下一窗口计划。后续不需要一开始实现复杂多租户平台，而是先做
  `SingleServerProfile`、`SingleServerJobTemplate`、dry-run review packet
  和 fake lifecycle。
- v0.10 第一批实现已经把单服务器准备工作收敛为 metadata-only 模型、
  validation helpers 和 review packet renderer。真实服务器连接、凭据加载、
  workspace 创建和 fake lifecycle 仍然不在本批实现中。
- v0.11 第一批实现把实验室内部账号模型简化为 `lab_root`、`lab_user`、`readonly`，
  并用 tested policy/validation 证明 root 角色也不能绕过删除 dry-run preview、路径保护、
  symlink 风险和确认边界。
- v0.12 第一批实现新增单服务器 fake lifecycle 状态机，用内存 metadata 展示
  requested/reviewed/approved/rejected/queued/running/succeeded/failed/cancelled
  状态流和 allowed next states，但不连接服务器、不执行命令、不创建目录。
- v0.13 第一批实现新增 workspace planner，用字符串级校验生成 workspace/run/log/
  artifact preview，拒绝空 root、路径穿越、绝对逃逸、危险 root 和保护标签，同时不创建
  目录、不删除目录、不移动文件、不连接服务器。
- v0.14 第一批实现新增 approved template run packet，把 profile/template/
  structured request/workspace plan/lifecycle id 合成 non-executing review
  packet，拒绝自由 command、未批准参数和缺失必填参数，同时不读取凭据、不连接服务器、
  不创建 workspace。
- v0.15 第一批实现新增 internal sanity runner gate，把未来 fixed runner 的最小边界
  收敛为 allowlisted runner id、timeout、stdout/stderr capture、artifact path 和
  audit metadata，同时拒绝自由命令、删除、凭据读取、SSH、Slurm、PBS 和 remote access。
- v1.0 internal preview 收口完成后，用户和 operator 有审计、用户手册、runbook、演示
  脚本、总测试报告和学习总结。它可以用于内部学习和演示，但仍不是完整真实后端。

## Technical Highlights

- C++17/C++20 multi-module project with CMake.
- gRPC and Protocol Buffers for service APIs.
- A2A-style HTTP JSON-RPC for agent-to-agent messaging.
- MCP client/server integration with tool discovery, tool schema, sync/async
  calls, and RAG-based tool retrieval.
- Redis-backed task and conversation memory.
- Local and API-based embedding support.
- JSON-backed local research knowledge notes for paper, algorithm, experiment,
  and failure-case guidance.
- Deterministic planner grounding that turns a user request into selected
  AlgorithmCards, local knowledge notes, parameter advice, and explicit
  dry-run safety boundaries before LLM generation.
- Structured dry-run experiment planning that produces parameter tables, risk
  analysis, ExperimentSpec JSON, dry-run JobSpec previews, and versioned
  experiment records without executing jobs.
- Static browser workbench that renders planner artifacts into inspectable
  panels and keeps execution state visible as `dry_run: true`.
- Deterministic lab-code adapter that converts config templates and supplied
  FWI log text into structured dry-run diagnostics, loss curves, failure
  findings, and Planner-facing summaries.
- Reserved a C++ `JobBackend` abstraction and backend type enum for future
  execution backends while preserving dry-run-only behavior through runtime
  rejection of `local`, `ssh`, `slurm`, and `pbs`.
- Wrote a server-backend safety design for future controlled execution,
  covering command-injection prevention, approved job templates, workspace path
  boundaries, lifecycle states, artifact collection, and audit records before
  any scheduler or remote adapter is connected.
- Added a tested C++ server-job safety model for future controlled execution,
  including `JobSubmissionRequest`, `JobRecord`, `ApprovedJobTemplate`,
  workspace path validation, and lifecycle history helpers while keeping all
  real backends disabled.
- Added a tested backend approval decision gate for future M11 backend
  selection, separating prerequisite validation from runtime backend
  enablement.
- Hardened the M11 approval decision gate so blank or placeholder approval
  metadata is rejected before any future backend selection can be considered.
- Added metadata-only submitter authorization checks for future backend
  approvals, linking `JobSubmissionRequest.user_id` to an approved submitter
  list and rejecting placeholder submitter entries without introducing
  credentials, remote calls, or execution.
- Added a metadata-only `JobAuditEvent` schema for future controlled execution
  records, covering job, request, user, event type, message, timestamp, and
  backend type while keeping real backends disabled.
- Added metadata-only in-memory audit log validation and append helpers so
  future audit persistence starts from validated same-job event batches.
- Added a unified metadata-only backend preflight report that aggregates
  approval, authorization, dry-run submission boundary, approved template,
  workspace, and audit-log checks while preserving the runtime backend guard.
- Added an operator-facing backend readiness report renderer so v0.9 can expose
  M11 preflight status in stable text while keeping runtime blockers visible.
- Added non-executing v0.9 preview renderers for dry-run submission packets,
  audit logs, and workspace/artifact plans so operators can review a future
  backend package before runtime enablement changes.
- 新增中文优先的 M11 实验室后端决策与流程文档，把真实后端选择和代码实现
  分开，并列出认证、workspace 生命周期、调度器提交、artifact 收集、可视化
  或审计持久化开始前必须具备的控制条件。
- 新增单服务器账号初步接入计划，为下一阶段 profile/template/review packet
  和 fake lifecycle 设计提供中文交接说明。
- 新增 v0.10 单服务器账号 metadata 实现与测试，覆盖 profile/template/review
  request/review packet 的数据边界，并把真实执行、凭据读取和服务器连接排除在
  第一批实现之外。
- 新增 v0.11 `safe_operations` C++ 模块和测试，覆盖内部角色、操作 allowlist、
  删除 dry-run review request/packet、protected path/symlink/confirmation 校验，
  并保持真实删除、trash move 和 shell 执行关闭。
- 新增 v0.12 `single_server_lifecycle` C++ 模块和测试，覆盖 fake lifecycle
  状态解析、内存状态转换、终态拒绝、取消路径和 preview renderer，并保持服务器连接、
  命令执行、workspace 创建关闭。
- 新增 v0.13 `workspace_planner` C++ 模块和测试，覆盖 workspace/run/log/artifact
  preview、路径穿越拒绝、绝对路径逃逸拒绝、危险 root 拒绝和保护标签拒绝，并保持目录
  创建、删除、文件移动和服务器连接关闭。
- 新增 v0.14 `approved_template_run_packet` C++ 模块和测试，覆盖 approved template
  run packet rendering、批准参数筛选、自由 command 拒绝、必填参数校验、workspace plan
  error 汇总和显式非执行 flags。
- 新增 v0.15 `internal_sanity_runner` C++ 模块和测试，覆盖 fixed runner review packet、
  allowlisted runner id、timeout/capture metadata、artifact path workspace-root 校验、
  audit event metadata 和危险请求拒绝。
- 完成 v1.0 internal preview 收口，把 v0.11-v0.15 的 metadata gates 映射到审计、
  user guide、operator runbook、demo script 和 consolidated test report。
- Property and integration tests with GoogleTest and RapidCheck.
- Web UI with HTTP and gRPC bridge modes.

## Resume Bullets

Use only bullets that match the completed implementation.

- Built a C++ multi-agent communication framework using gRPC, Protocol Buffers,
  A2A-style JSON-RPC, and Redis-backed task memory.
- Implemented agent registry and dynamic routing with AgentCard metadata,
  skills, tags, and embedding-based Agent-RAG retrieval.
- Integrated MCP tool calling with schema-based tool discovery, retry handling,
  and RAG-based tool selection.
- Developed an FWI research assistant prototype with local knowledge retrieval,
  specialized FWI agents, and metadata tools for velocity models and datasets.
- Added automated test coverage across RPC serialization, A2A adapters,
  registry behavior, routing, MCP integration, and RAG properties.
- Added a read-only Code Agent MVP executable for code Q&A, error diagnosis,
  repository list/read/search context, and patch proposal prompts, with local
  startup script integration.
- Added the first research-domain C++ model, `AlgorithmCard`, with JSON
  serialization and validation that rejects non-dry-run backends in v0.2.
- Added an `AlgorithmRegistry` that loads algorithm metadata from JSON seed
  cards and supports deterministic lookup/filtering without Orchestrator
  changes.
- Added a local algorithm listing helper that exposes registry contents as a
  read-only JSON summary for future agent or MCP tool use.
- Added `ExperimentSpec`, `JobSpec`, and `DryRunBackend` abstractions with tests
  for dry-run rendering and validation.
- Added an Experiment Planner Agent skeleton that registers as a planning
  specialist and grounds prompts in local AlgorithmCards while preserving
  dry-run-only execution boundaries.
- Added a structured research knowledge base with typed JSON notes and tested
  retrieval by method, failure mode, parameter advice, and dataset for FWI
  planning.
- Added a deterministic PlannerContext layer that grounds Experiment Planner
  prompts in AlgorithmCards, local research knowledge, failure-case notes, and
  parameter advice while preserving dry-run-only execution boundaries.
- Added a PlannerAnswer layer that converts grounded planner context into
  structured dry-run experiment plans, including ExperimentSpec, JobSpec, risk
  analysis, and reproducible records.
- Renamed the Web UI brand to Lab Agent Workbench and added a CTest guard for
  the static UI and server branding text.
- Upgraded the Web UI into a Lab Agent Workbench that renders route traces,
  tool calls, AlgorithmCards, parameter tables, ExperimentSpec, JobSpec,
  dry-run state, experiment history, and service status panels.
- Added a Lab Code Adapter for reading lab-style config templates, rendering
  dry-run config previews, parsing supplied logs, extracting loss curves, and
  recognizing common FWI failure patterns without job submission.
- Reserved the future `JobBackend` interface, backend type enum, and shared
  runtime guard; made `DryRunBackend` polymorphic while rejecting `local`,
  `ssh`, `slurm`, and `pbs` until server execution has a safety design.
- Wrote the v0.8 server-backend safety design and implementation plan for
  approved templates, workspace isolation, lifecycle records, artifact
  collection, and audit logging before enabling real CUDA/MPI or cluster
  execution.
- Added v0.8 server-job safety models and tests for dry-run default
  submissions, approved template validation, workspace path traversal
  rejection, and in-memory lifecycle event tracking without enabling real
  execution.
- Added a metadata-only backend approval decision gate for future real backend
  selection, proving that complete approval records still do not bypass the
  dry-run-only runtime guard.
- Hardened the backend approval preflight validator to reject placeholder
  approval metadata such as `TBD`, `pending`, `unknown`, `n/a`, or `none`.
- Added a tested submitter authorization preflight check so future backend
  approvals must name concrete users who may submit jobs before runtime
  execution is ever enabled.
- Added tested job audit event metadata for future real backend work, including
  validation that keeps audit records aligned with the dry-run-only backend
  guard.
- Added tested in-memory job audit log helpers that reject empty logs,
  cross-job events, and invalid audit events before appending metadata.
- Added a tested backend preflight readiness report, making it possible to
  explain why a future backend package is metadata-ready while real execution
  remains disabled.
- Added a tested operator-facing backend readiness report renderer for v0.9
  review workflows without enabling any real execution path.
- Completed v0.9 backend readiness review with tested non-executing previews
  for submission packets, audit logs, and workspace/artifact plans.
- Added v0.11 safe operation metadata and tests for internal lab roles,
  operation allowlists, and deletion dry-run review packets while keeping real
  deletion, trash moves, shell execution, credential reads, and server
  connections disabled.
- Added v0.12 fake lifecycle metadata and tests for single-server review state
  flow, allowed next states, terminal-state blocking, and non-executing preview
  rendering.
- Added v0.13 workspace planner metadata and tests for preview-only
  workspace/run/log/artifact paths, with path traversal, absolute escape,
  dangerous root, and protected-label rejection while keeping directory
  creation, deletion, file movement, and server connections disabled.
- Added v0.14 approved-template run packet metadata and tests for
  non-executing future-run review packets, free-form command rejection,
  unapproved parameter rejection, required-parameter checks, workspace plan
  validation propagation, and explicit execution-disabled flags.
- Added v0.15 internal sanity runner gate metadata and tests for fixed
  allowlisted runner review packets, timeout/capture planning, artifact path
  validation, audit event metadata, and rejection of dangerous requests.
- Completed v1.0 internal preview documentation with a safety audit, internal
  user guide, operator runbook, demo script, consolidated test report, and
  learning summary for the review-only single-server workflow.
- 新增中文 M11 后端决策与流程文档，用于讨论后端批准、凭据、workspace、
  授权、配额、监控、审计、回滚和实现顺序。

Future real backend expansion:

- Controlled real backend integration only after a lab-approved backend,
  credential model, workspace root, authorization policy, audit retention, and
  operator responsibilities are known.

Move planned bullets into completed bullets only after implementation and tests
are committed.

## Interview Explanation: Why This Is Not Just A Chatbot

This project separates communication, orchestration, tools, knowledge, and
experiment planning:

- Chat is only one interface.
- Agents are registered with skills and tags.
- The Orchestrator can route by fixed intent or Agent-RAG.
- Tools are discovered through MCP and selected through Tool-RAG.
- Research algorithms will be represented as AlgorithmCards instead of hardcoded
  prompts.
- Real execution is intentionally behind a backend interface so CUDA/MPI and
  cluster jobs can be added safely later.

## Upgrade Notes

Add one short entry whenever a meaningful technical change lands.

### 2026-06-11: Upgrade Planning

- Added upgrade workflow, milestone board, v0.2 implementation plan, and version
  roadmap.
- Current next engineering target is Code Agent MVP.

### 2026-06-11: README Product Positioning

- Reframed the README first screen as a Lab Research Agent Platform rather than
  only an RPC framework.
- Documented the product layers and current safety boundaries for recruiter,
  lab user, and demo audiences.

### 2026-06-11: Code Agent Registration Contract

- Added a GoogleTest contract for Code Agent registration metadata, including
  the `code` tag, code-oriented skills, tool-calling capability, and AgentCard
  serialization expectations.

### 2026-06-11: Code Agent Executable

- Added the `ai_code_agent` executable, Code Agent startup integration, and a
  CTest executable-target contract.
- The Code Agent is prompt-only and read-only in this step; repository
  list/read/search tools remain the next Code Agent milestone.

### 2026-06-11: Code Agent Read-Only Inspection Tools

- Added C++ read-only project inspection helpers for file listing, safe file
  reading, and text search inside the project root.
- Wired Code Agent prompts to include deterministic project context while still
  preventing shell execution and automatic patch application.

### 2026-06-11: Code Agent Smoke Test Docs

- Added a documented smoke-test path for verifying that code intent routes to
  the read-only Code Agent and identifies the Orchestrator routing logic.

### 2026-06-11: Quick Demo Command Map

- Added recruiter- and demo-friendly README commands for HTTP, gRPC bridge, Web
  UI, and local embedding entry points while preserving localhost-only safety
  boundaries.

### 2026-06-11: AlgorithmCard Model

- Added the `agent_rpc_research` library and an `AlgorithmCard` model for
  JSON-backed lab algorithm metadata, including validation that keeps execution
  constrained to `dry_run` in v0.2.

### 2026-06-11: AlgorithmRegistry And Seed Cards

- Added file-based AlgorithmCard loading from `resources/algorithms/*.json`,
  seed cards for FWI, frequency extrapolation, and post-stack inversion, plus
  tests for loading, filtering, and invalid backend rejection.

### 2026-06-11: Algorithm Listing Tool Entry

- Added a deterministic local listing helper for AlgorithmRegistry summaries,
  preserving a read-only metadata boundary before any MCP exposure.

### 2026-06-11: ExperimentSpec, JobSpec, And DryRunBackend

- Added structured experiment and job models plus a dry-run backend that renders
  command previews with `dry_run: true` without executing anything.

### 2026-06-11: Experiment Planner Agent Skeleton

- Added an Experiment Planner Agent executable and startup integration with
  planning/research-computing registration tags and AlgorithmCard prompt
  context.

### 2026-06-11: v0.2 Demo And Test Report

- Added a v0.2 demo script that separates Orchestrator demos from the direct
  Experiment Planner Agent dry-run smoke test.
- Added a v0.2 test report and knowledge summary covering routing contracts,
  research metadata modeling, dry-run planning boundaries, and verification
  practice.

### 2026-06-12: Research Knowledge Base Skeleton

- Added JSON-backed `ResearchKnowledgeNote` and `ResearchKnowledgeBase` C++
  models for typed paper, algorithm, experiment, and failure-case notes.
- Added deterministic retrieval tests for note type, method, failure mode, and
  parameter advice without enabling any real execution backend.

### 2026-06-12: AWI And Gradient Knowledge Notes

- Added structured AWI and adjoint-state gradient notes for cycle-skipping
  diagnosis, misfit-function choice, and gradient-check advice.
- Extended deterministic knowledge tests so v0.3 content coverage is protected
  by method, failure-mode, and parameter-advice retrieval assertions.

### 2026-06-12: v0.3 Research Knowledge Completion

- Added dataset-based research knowledge retrieval and marked v0.3 complete.
- Added a v0.3 test report with Chinese learning and interview-prep summary.

### 2026-06-12: v0.4 PlannerContext Retrieval

- Added deterministic PlannerContext retrieval for the Experiment Planner,
  combining AlgorithmCards, structured research notes, failure-mode evidence,
  and parameter advice before LLM prompting.
- Preserved the dry-run-only boundary: the Planner context explicitly marks
  real execution disabled and forbids CUDA/MPI, SSH, Slurm/PBS, remote jobs, and
  shell execution.

### 2026-06-12: v0.4 Experiment Planner Completion

- Added PlannerAnswer generation for structured algorithm recommendations,
  assumptions, parameter tables, risk analysis, next steps, ExperimentSpec,
  dry-run JobSpec, and reproducible experiment records.
- Updated the Experiment Planner Agent prompt path so deterministic structured
  scaffolds are available before LLM generation.

### 2026-06-12: Lab Agent Workbench Branding

- Renamed the static Web UI title, sidebar, welcome state, footer, and local
  server banner to Lab Agent Workbench.
- Added a CTest branding guard so future UI work does not regress to generic
  orchestrator-chat wording.

### 2026-06-12: v0.5 Lab Workbench UI Completion

- Added a browser-side research workbench layout with AlgorithmCards,
  experiment history, route trace, tool calls, selected AlgorithmCard,
  parameter table, ExperimentSpec, JobSpec, and service status panels.
- Added static parsing helpers for ExperimentSpec JSON blocks and dry-run
  JobSpec text blocks, preserving a preview-only boundary.
- Added a v0.5 test report and Chinese learning summary for product story,
  implementation details, verification evidence, safety boundaries, and
  interview preparation.

### 2026-06-22: v0.6 Lab Code Adapter Plan

- Added a v0.6 implementation plan for config template reading, safe config
  previews, log parsing, loss curve parsing, and deterministic failure
  recognition.
- Kept the career story explicit that v0.6 is planned, not implemented: no real
  CUDA/MPI execution, SSH, Slurm/PBS, remote execution, shell execution, or
  automatic Code Agent patch application was added.

### 2026-06-22: v0.6 Lab Code Adapter Completion

- Added the deterministic `lab_code_adapter` research component for config
  templates, dry-run config previews, supplied log parsing, loss curve
  extraction, common failure findings, and Planner-facing diagnostic summaries.
- Added fixture-backed tests for execution-field rejection, loss parsing,
  stagnation, NaN/Inf, cycle-skipping risk, resource-limit recognition, and
  dry-run safety boundary summaries.
- Preserved the execution boundary: no real CUDA/MPI execution, SSH, Slurm/PBS,
  remote execution, arbitrary shell execution, or automatic Code Agent patch
  application was added.

### 2026-06-22: JobBackend Interface Reservation

- Added a C++ `JobBackend` abstraction with `validate`, `render`, and `explain`
  methods for future execution-backend substitution.
- Made `DryRunBackend` implement the interface and added a contract test that
  exercises dry-run rendering through `const JobBackend&`.
- Preserved the execution boundary: no real CUDA/MPI execution, SSH, Slurm/PBS,
  remote execution, arbitrary shell execution, or automatic Code Agent patch
  application was added.

### 2026-06-22: v0.7 JobBackend Reservation Completion

- Added `JobBackendType` values for `dry_run`, `local`, `ssh`, `slurm`, `pbs`,
  and `unknown`, plus stable string parsing and rendering helpers.
- Added shared runtime validation that accepts only `dry_run` and rejects
  reserved or unknown backends with clear messages.
- Reused the backend guard in `AlgorithmCard` validation so JSON metadata
  cannot accidentally enable real execution.
- Added a v0.7 test report with Chinese learning and interview-prep summary.

### 2026-06-22: v0.8 Server Backend Safety Design

- Started v0.8 with a written safety design and implementation plan before any
  real execution code.
- Defined the future server backend boundary around approved templates,
  structured submission records, workspace isolation, lifecycle state, artifact
  collection, and audit logging.
- Kept the product claim explicit: real CUDA/MPI, SSH, Slurm, PBS, remote
  execution, local wrapper execution, arbitrary shell execution, and automatic
  Code Agent patch application are still not enabled.

### 2026-06-22: v0.8 Server Backend Safety Completion

- Added the C++ `server_job` safety model with structured submission requests,
  job records, lifecycle states, approved templates, workspace guards, and
  lifecycle history helpers.
- Added `ServerJobTest` coverage for dry-run defaults, non-`dry_run` rejection,
  approved-template matching, workspace traversal rejection, rejected records,
  and lifecycle events.
- Added the v0.8 test report and Chinese learning summary for future study and
  interview preparation.

### 2026-06-22: v0.9 Backend Readiness Report Renderer

- Started v0.9 as non-executing readiness/review work by adding
  `render_backend_preflight_report`.
- The renderer turns structured preflight results into stable operator-facing
  text and keeps validation errors, runtime blockers, and safety boundaries
  visible.
- Preserved the execution boundary: no real CUDA/MPI execution, SSH, Slurm/PBS,
  remote execution, local wrapper execution, arbitrary shell execution,
  credential loading, production audit store, or automatic Code Agent patch
  application was added.

### 2026-06-22: v0.9 Backend Readiness Review Completion

- Added non-executing preview renderers for dry-run submission packets, audit
  logs, and workspace/artifact plans.
- Added v0.9 test report and Chinese learning summary for future study and
  interview preparation.
- Clarified that full real backend expansion should wait until M11 controlled
  backend integration has lab approval, auth, workspace lifecycle, submission
  controls, artifact collection, visualization, audit logging, and passing
  tests.

### 2026-06-23: v0.10 Single Server Preparation Design

- Added a Chinese v0.10 design and implementation plan for metadata-only
  single-server account preparation.
- Scoped the next implementation to `SingleServerProfile`,
  `SingleServerJobTemplate`, `SingleServerReviewRequest`, and dry-run review
  packet rendering.
- Preserved the safety boundary: no real CUDA/MPI, SSH, Slurm/PBS, server
  connection, credential loading, workspace creation, arbitrary shell
  execution, or automatic Code Agent patch application.

### 2026-06-23: v0.10 Single Server Metadata Completion

- Added C++ `SingleServerProfile`, `SingleServerJobTemplate`, and
  `SingleServerReviewRequest` metadata for the first single-server account
  preparation batch.
- Added validation that rejects empty credential references, inline
  secret-looking values, runtime-enabled profiles, unknown templates,
  unapproved parameters, and non-dry-run review requests.
- Added a dry-run review packet renderer and `SingleServerBackendTest` coverage
  while preserving the non-execution boundary: no server connection, credential
  loading, workspace creation, CUDA/MPI, SSH, Slurm/PBS, or arbitrary shell
  execution.

### 2026-06-23: v0.11 Safe Operations Planning

- Added a Chinese v0.11 design and learning summary for internal lab safe
  operations.
- Scoped the next implementation to simple roles, operation allowlists, and
  deletion dry-run review packets.
- Preserved the safety boundary: no real deletion, trash move, filesystem
  remove, shell execution, credential loading, server connection, or workspace
  creation.

### 2026-06-23: v0.11 Safe Operations Metadata Completion

- Added `safe_operations` C++ metadata for internal lab roles, safe operation
  requests, role-based allowlists, delete review requests, and delete review
  packets.
- Added validation and `SafeOperationsTest` coverage proving `readonly` cannot
  request delete preview, `lab_user` can request workspace-scoped dry-run
  delete preview, and `lab_root` still cannot bypass dry-run, path protection,
  symlink risk, or confirmation checks.
- Preserved the safety boundary: no real deletion, trash move, filesystem
  remove, shell execution, credential loading, server connection, or workspace
  creation.

### 2026-06-23: v1.0 Internal Preview Roadmap

- Added a public roadmap for the internal lab preview path from v0.11 to v1.0.
- Split the remaining work into safe operations, fake lifecycle, workspace
  planning, approved-template run packets, a fixed sanity-check runner gate, and
  v1.0 closeout docs.
- Moved copy-paste prompts and detailed agent execution plans to local ignored
  files so the GitHub-facing project history stays focused on architecture,
  tests, learning reports, and product decisions.

### 2026-06-23: v0.12 Fake Lifecycle Completion

- Added `single_server_lifecycle` C++ metadata for single-server fake lifecycle
  states, events, records, transition validation, and preview rendering.
- Added tests for state parsing, requested-record defaults, success flow,
  cancellation, rejected terminal blocking, allowed next states, and explicit
  non-execution flags.
- Preserved the safety boundary: no server connection, command execution,
  workspace or directory creation, credential loading, real log collection, or
  artifact collection.

### 2026-06-23: v0.13 Workspace Planner Completion

- Added `workspace_planner` C++ metadata for preview-only workspace, run
  directory, log, and artifact paths.
- Added path safety validation and `WorkspacePlannerTest` coverage for empty
  roots, traversal, absolute escape attempts, dangerous roots, protected labels,
  and explicit non-execution flags.
- Preserved the safety boundary: no directory creation, deletion, file
  movement, server connection, remote filesystem access, credential loading, or
  command execution.

### 2026-06-23: v0.14 Approved Template Run Packet Completion

- Added `approved_template_run_packet` C++ metadata for combining
  single-server profile, approved template, structured review request,
  workspace plan, and lifecycle id into a reviewable future-run packet.
- Added validation and `ApprovedTemplateRunPacketTest` coverage for allowlisted
  parameters, rejected free-form commands, missing required parameters,
  template/profile mismatch, workspace-plan errors, and non-rendering of unsafe
  command text or credential references.
- Preserved the safety boundary: no command execution, credential loading,
  server connection, workspace creation, directory creation, deletion, or file
  movement.

### 2026-06-23: v0.15 Internal Sanity Runner Gate Completion

- Added `internal_sanity_runner` C++ metadata for fixed allowlisted runner
  definitions, runner requests, and non-executing review packets.
- Added `InternalSanityRunnerTest` coverage for allowlisted runner review,
  unknown runner rejection, free-form command rejection, deletion rejection,
  credential-read rejection, SSH/Slurm/PBS/remote access rejection, artifact path
  root checks, traversal checks, and required timeout/stdout/stderr capture
  metadata.
- Preserved the safety boundary: no command execution, credential loading,
  deletion, SSH, Slurm, PBS, remote server access, workspace creation, real
  stdout/stderr capture, or artifact collection.

### 2026-06-23: v1.0 Internal Preview Closeout

- Added the v1.0 internal preview audit, user guide, operator runbook, demo
  script, consolidated test report, and Chinese learning summary.
- Marked the review-only single-server internal preview complete only after
  v0.11-v0.15 safety gates and full CTest evidence were checked.
- Preserved the safety boundary: no real CUDA/MPI, SSH, Slurm, PBS, remote
  server connection, arbitrary shell execution, credential loading, workspace
  creation, deletion, trash move, file movement, real stdout/stderr capture,
  artifact collection, or automatic Code Agent patch application.
