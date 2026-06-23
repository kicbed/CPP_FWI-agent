# Lab Research Agent Upgrade Milestones

This file is the long-running task board. Keep it updated after every upgrade
session.

Status markers:

- `[ ]` not started
- `[~]` in progress
- `[x]` complete

Version status:

- v0.2 Lab Agent MVP is complete as of 2026-06-11 according to
  `docs/upgrade/version-roadmap.md`.
- v0.3 Research Knowledge Base started on 2026-06-12 with structured local
  knowledge notes, deterministic file loading, and retrieval tests.
- v0.3 Research Knowledge Base is complete as of 2026-06-12 according to
  `docs/upgrade/version-roadmap.md` and `docs/upgrade/test-report-v0.3.md`.
- v0.4 Experiment Planner started on 2026-06-12 with deterministic
  PlannerContext retrieval that combines AlgorithmCards, research knowledge
  notes, and parameter advice before LLM prompting.
- v0.4 Experiment Planner is complete as of 2026-06-12 according to
  `docs/upgrade/version-roadmap.md` and `docs/upgrade/test-report-v0.4.md`.
- v0.5 Lab Workbench UI started on 2026-06-12 with Web UI branding renamed from
  a generic orchestrator chat page to Lab Agent Workbench.
- v0.5 Lab Workbench UI is complete as of 2026-06-12 according to
  `docs/upgrade/version-roadmap.md` and `docs/upgrade/test-report-v0.5.md`.
- v0.6 Lab Code Adapter started on 2026-06-22.
- v0.6 Lab Code Adapter is complete as of 2026-06-22 according to
  `docs/upgrade/version-roadmap.md` and `docs/upgrade/test-report-v0.6.md`.
- v0.7 JobBackend Interface Reservation is complete as of 2026-06-22
  according to `docs/upgrade/version-roadmap.md` and
  `docs/upgrade/test-report-v0.7.md`.
- v0.8 Server Backend Safety Design started on 2026-06-22 with
  `docs/upgrade/server-backend-safety-v0.8.md`.
- v0.8 Server Backend Safety Foundation is complete as of 2026-06-22
  according to `docs/upgrade/version-roadmap.md` and
  `docs/upgrade/test-report-v0.8.md`.
- Milestone 11 preflight is complete as of 2026-06-22 with a metadata-only backend
  approval decision gate, submitter authorization validation, and audit-event
  metadata plus in-memory audit log validation and a unified preflight readiness
  report. This does not select or enable a real backend.
- v0.9 Backend Readiness Review is complete as of 2026-06-22 with
  non-executing readiness report rendering, dry-run submission packet preview,
  audit log preview, workspace/artifact path preview, and v0.9 report docs.
- M11 实验室后端决策包模板已在 2026-06-23 创建。这只是评审材料；
  M11-T1 仍未完成，必须等实验室提供具体批准后端、凭据策略、
  workspace root、授权策略、审计保留、配额/operator 规则和 operator
  联系人后才能继续。
- 单服务器账号初步接入交接文档已在 2026-06-23 创建。它把当前实验室场景
  收敛为一个服务器账号、固定 workspace、固定 approved template、dry-run
  review packet 和 fake lifecycle，供下一窗口继续做设计文档和实现计划。
- M11-S1 单服务器账号接入准备第一批实现已在 2026-06-23 完成。当前范围
  只到 metadata/profile/template 和 dry-run review packet；没有真实命令执行、
  凭据读取、SSH、Slurm/PBS 或服务器连接。
- v0.11 实验室内部安全操作策略第一批实现已在 2026-06-23 完成。它新增
  lab_root/lab_user/readonly、safe operation policy、删除 dry-run review packet
  metadata、validation 和 renderer，不实现真实删除。
- v1.0 internal preview 分步路线已在 2026-06-23 创建。公开路线图保存在
  `docs/upgrade/v1.0-internal-preview-roadmap.md`；新窗口提示词和详细 agent
  执行计划保存在本地忽略文件中，不提交到 GitHub。

## Milestone 0: Baseline And Project Story

Goal: make the current repository understandable and safe to upgrade.

Tasks:

- [x] M0-T1: Run `ctest --test-dir build --output-on-failure` and record the result in `docs/upgrade/upgrade-log.md`.
- [x] M0-T2: Rewrite the top of `README.md` so the first screen says this is a lab research agent platform, not only an RPC framework.
- [x] M0-T3: Add a short architecture section that names the product layers: Client, Orchestrator, Agents, MCP Tools, Knowledge, Experiment Planning.
- [x] M0-T4: Document current limitations: no real CUDA/MPI execution, no cluster backend, no automatic code patch application.
- [x] M0-T5: Add quick demo commands for HTTP, gRPC bridge, Web UI, and local embedding.

Acceptance:

- `README.md` tells a recruiter and a lab user what the project is within one minute.
- Existing tests pass.
- No runtime behavior changes.

## Milestone 1: Lightweight Product Structure

Goal: keep existing code working while creating a cleaner product path.

Tasks:

- [ ] M1-T1: Add `apps/README.md` describing official app entry points.
- [ ] M1-T2: Add `agents/README.md` describing agent responsibilities and registration tags.
- [ ] M1-T3: Add `research/README.md` describing AlgorithmCard, ExperimentSpec, and JobBackend concepts.
- [ ] M1-T4: Keep `examples/ai_orchestrator` runnable, but mark it as legacy/demo in docs.
- [ ] M1-T5: Identify repeated agent runtime code in FWITheory, FWITeaching, GeneralResearch, and Math agents.
- [ ] M1-T6: Create a follow-up refactor plan for shared `AgentRuntime`.

Acceptance:

- No executable needs to move in this milestone.
- New directories clarify future ownership.
- Existing start scripts still work.

## Milestone 2: Code Agent MVP

Goal: make the existing `code` intent route to a real agent instead of falling
back to general chat.

Tasks:

- [x] M2-T1: Add `ai_code_agent` executable to `examples/ai_orchestrator/CMakeLists.txt`.
- [x] M2-T2: Implement a Code Agent that registers with tag `code`.
- [x] M2-T3: Add read-only project inspection functions: list files, read file, search text.
- [x] M2-T4: Add prompt behavior for code explanation, error diagnosis, and patch proposal.
- [x] M2-T5: Update `examples/ai_orchestrator/start_system.sh` and `deploy/scripts/start.sh` to start Code Agent.
- [x] M2-T6: Add a test that verifies Code Agent registration data uses tag `code` and code-oriented skills.
- [x] M2-T7: Add a smoke-test command to docs for asking where Orchestrator routing lives.

Acceptance:

- Asking a coding question no longer falls back to the general handler when Code Agent is running.
- Code Agent does not write files or execute arbitrary shell commands.
- Tests pass.

## Milestone 3: AlgorithmCard Registry

Goal: represent lab algorithms as data so FWI, frequency extrapolation,
post-stack algorithms, and future group methods can be added without changing
the Orchestrator.

Tasks:

- [x] M3-T1: Add C++ `AlgorithmCard` model with JSON serialization and validation.
- [x] M3-T2: Add `AlgorithmRegistry` that loads cards from `resources/algorithms/*.json`.
- [x] M3-T3: Add seed cards for CUDA-MPI FWI, frequency extrapolation, and post-stack inversion.
- [x] M3-T4: Add tests for valid card loading, invalid card rejection, and domain/tag filtering.
- [x] M3-T5: Add MCP or local tool entry for listing algorithms.

Acceptance:

- A new algorithm can be added by creating one JSON file.
- Invalid cards fail validation with a clear message.
- Tests pass.

## Milestone 4: ExperimentSpec, JobSpec, And DryRunBackend

Goal: plan experiments without executing real jobs.

Tasks:

- [x] M4-T1: Add `ExperimentSpec` model for algorithm, dataset, parameters, resources, and expected outputs.
- [x] M4-T2: Add `JobSpec` model for command, working directory, environment, MPI process count, GPU count, time limit, and artifact paths.
- [x] M4-T3: Add `DryRunBackend` with `validate`, `render`, and `explain` methods.
- [x] M4-T4: Add tests for valid specs, missing algorithm ID, invalid GPU count, and command rendering.
- [x] M4-T5: Ensure rendered jobs include a clear `dry_run: true` marker.

Acceptance:

- The system can render an experiment command or Slurm-style script draft.
- No command is executed.
- Tests pass.

## Milestone 5: Experiment Planner Agent

Goal: turn research questions into structured experiment plans.

v0.2 scope note: the MVP completed the agent skeleton, AlgorithmCard prompt
context, dry-run boundary, startup integration, and tests for registration and
target availability. The unchecked items are the next planner-quality upgrades.

Tasks:

- [x] M5-T1: Add `ExperimentPlannerAgent` executable and register it with tags `experiment`, `planning`, `research-computing`.
- [x] M5-T2: Retrieve relevant AlgorithmCards and knowledge documents for a user request.
- [x] M5-T3: Generate a structured answer with algorithm recommendation, parameter table, risk analysis, and next-step plan.
- [x] M5-T4: Generate an `ExperimentSpec` JSON block in the answer.
- [x] M5-T5: Generate a `JobSpec` dry-run block when the algorithm card declares job spec support.
- [x] M5-T6: Add tests for deterministic non-LLM pieces: card retrieval, spec validation, dry-run rendering.

Acceptance:

- A question about Marmousi multi-scale FWI produces a plan, parameters, risks, and dry-run job text.
- The answer states that real CUDA/MPI execution is not enabled yet.
- Tests pass.

## Milestone 6: Research Knowledge Upgrade

Goal: move beyond Markdown keyword search toward paper, algorithm, and
experiment guidance.

Tasks:

- [x] M6-T1: Add `resources/research_knowledge/papers`.
- [x] M6-T2: Add `resources/research_knowledge/algorithms`.
- [x] M6-T3: Add `resources/research_knowledge/experiments`.
- [x] M6-T4: Add `resources/research_knowledge/failure_cases`.
- [x] M6-T5: Add structured notes for multi-scale FWI, AWI, cycle skipping, and adjoint-state gradient.
- [x] M6-T6: Extend knowledge retrieval to include note type, method, assumptions, parameter advice, and failure modes.
- [x] M6-T7: Add tests for retrieving advice by failure mode and algorithm method.
- [x] M6-T8: Add dataset-based knowledge retrieval and tests.

Acceptance:

- The system can explain why a parameter recommendation was made.
- Answers cite local knowledge categories, not only generic LLM knowledge.
- Tests pass.

## Milestone 7: Lab Workbench UI

Goal: make the Web UI feel like a research workbench instead of a chat-only page.

Tasks:

- [x] M7-T1: Rename UI branding to Lab Agent Workbench.
- [x] M7-T2: Add a right-side inspector for selected agent, tool calls, and generated specs.
- [x] M7-T3: Add an algorithm panel that lists AlgorithmCards.
- [x] M7-T4: Render ExperimentSpec and JobSpec blocks as tables/cards.
- [x] M7-T5: Add status indicators for Orchestrator, Registry, MCP, Embedding, and Code Agent.
- [x] M7-T6: Add UI smoke-test notes and screenshots to the upgrade log.

Acceptance:

- A demo viewer can see routing, tools, parameter plans, and dry-run job output.
- UI still works on localhost with existing start scripts.

## Milestone 8: Lab Code Adapter

Goal: understand lab code configs, logs, loss curves, and common failure
signals without submitting jobs.

Tasks:

- [x] M8-T1: Create the v0.6 Lab Code Adapter implementation plan.
- [x] M8-T2: Add config template reader with validation against execution fields.
- [x] M8-T3: Add safe config generator with `dry_run: true` preview output.
- [x] M8-T4: Add log parser and loss curve parser for supplied text content.
- [x] M8-T5: Add common failure recognizers for loss stagnation, NaN/Inf,
  cycle-skipping hints, missing low-frequency content, and resource limits.
- [x] M8-T6: Add planner-facing diagnostic summary grounded in parsed evidence.
- [x] M8-T7: Add v0.6 test report and Chinese learning summary.

Acceptance:

- The adapter can inspect config templates and supplied logs without running
  CUDA/MPI code.
- No SSH, Slurm, PBS, remote server, or arbitrary shell execution is added.
- Tests cover successful parsing, invalid execution fields, loss extraction,
  and failure recognition.

## Milestone 9: JobBackend Interface Reservation

Goal: reserve the future server execution interface without connecting real
servers.

Tasks:

- [x] M9-T1: Define `JobBackend` interface.
- [x] M9-T2: Make `DryRunBackend` implement `JobBackend`.
- [x] M9-T3: Add backend type enum values: `dry_run`, `local`, `ssh`, `slurm`, `pbs`.
- [x] M9-T4: Reject non-`dry_run` backends at runtime with a clear message.
- [x] M9-T5: Document how Slurm/PBS can be added later.

Acceptance:

- Future backends have a clear interface.
- v0.2 cannot accidentally submit real jobs.
- Tests pass.

## Milestone 10: Server Backend Safety Design

Goal: design and test the controlled server backend boundary before any real
execution adapter is connected.

Tasks:

- [x] M10-T1: Write the v0.8 server-backend safety design and implementation plan.
- [x] M10-T2: Add server job submission and lifecycle record models.
- [x] M10-T3: Add approved job template validation.
- [x] M10-T4: Add workspace path isolation and traversal rejection.
- [x] M10-T5: Add fake backend or lifecycle test helpers that never execute commands.
- [x] M10-T6: Add v0.8 test report and Chinese learning summary.

Acceptance:

- Runtime still rejects all non-`dry_run` backend values.
- User text cannot become a shell command.
- Server-job models, approved templates, workspace guards, lifecycle records,
  and audit boundaries are tested before any real backend work starts.
- No CUDA/MPI, SSH, Slurm, PBS, remote server, local wrapper, or arbitrary shell
  execution is added.

## Milestone 11: Controlled Real Backend Integration

Goal: connect lab execution only after v0.8 safety models and tests are
complete and a lab-approved backend is selected.

Tasks:

- [x] M11-T0: Add a metadata-only backend approval decision gate that requires
  lab approval, workspace root, credential reference, authorization policy,
  audit retention, and operator contact before any real backend can be
  considered selected.
- [x] M11-T0A: Add a metadata-only authorized submitter list and request-user
  validation for future real backend approvals.
- [x] M11-T0B: Add a metadata-only job audit event model and validation for
  future submission, rejection, lifecycle, artifact, and operator-note records.
- [x] M11-T0C: Add metadata-only in-memory audit log validation and append
  helpers for future audit persistence boundaries.
- [x] M11-T0D: Add a unified metadata-only backend preflight readiness report
  that separates metadata readiness from runtime backend enablement.
- [ ] M11-T1: 在实验室批准后决定第一个真实后端：local wrapper、SSH、Slurm 或 PBS。
  - 2026-06-23 记录：`docs/upgrade/m11-lab-backend-decision-package.md`
    是必须填写的决策包模板；这不代表 M11-T1 已完成，也不启用运行时执行。
  - 2026-06-23 记录：`docs/upgrade/m11-lab-process-guide.md` 是中文实验室
    流程指南，覆盖批准、凭据、workspace、授权、模板、配额、监控、审计和回滚。
  - 2026-06-23 记录：`docs/upgrade/single-server-backend-v0.10.md` 和
    `docs/upgrade/v1.0-internal-preview-roadmap.md` 适用于“一个服务器账号、
    自己或小组内部先跑”的初步实验室场景。
- [x] M11-S1: 单服务器账号受控运行准备，作为 M11-T1 未完成前的非执行收敛路径。
  - [x] 新增设计文档：`docs/upgrade/single-server-backend-v0.10.md`。
  - [x] 实现 `SingleServerProfile` metadata，拒绝空凭据引用、疑似内联秘密、
    空 workspace 引用、空模板列表和 runtime enabled 状态。
  - [x] 实现 `SingleServerJobTemplate` metadata，拒绝未知 template、profile 不匹配、
    版本不匹配和未允许参数。
  - [x] 渲染 dry-run review packet，明确不执行命令、不读取凭据、不连接服务器、
    不创建 workspace。
  - [x] 新增 `docs/upgrade/test-report-v0.10.md` 和
    `docs/upgrade/learning-summary-v0.10.md`。
- [x] M11-S2: 实验室内部安全操作策略，作为真实运行前的防误伤边界。
  - [x] 新增设计文档和学习总结：
    `docs/upgrade/safe-operations-v0.11.md`、
    `docs/upgrade/learning-summary-v0.11-safe-operations.md`。
  - [x] 实现 `LabAccountRole`、`SafeOperationType`、`SafeOperationRequest`
    和 `SafeOperationPolicy`。
  - [x] 实现删除 dry-run review request/packet 校验和 renderer。
  - [x] 测试证明 root 角色也不能绕过 dry-run、路径保护和删除确认。
  - [x] 新增 `docs/upgrade/test-report-v0.11.md`。
- [ ] M11-S3: 单服务器 fake lifecycle，作为 v0.12 目标。
  - [ ] 实现 requested、reviewed、approved、rejected、queued、running、
    succeeded、failed、cancelled 等 metadata 状态。
  - [ ] 只做内存状态流和 review packet，不连接服务器、不执行命令、不创建目录。
  - [ ] 新增 v0.12 测试报告和学习总结。
- [ ] M11-S4: workspace planner，作为 v0.13 目标。
  - [ ] 生成 workspace、artifact、log 和 run directory preview。
  - [ ] 校验路径不能逃逸 lab workspace root。
  - [ ] 不创建目录、不删除目录、不移动文件。
- [ ] M11-S5: approved template run packet，作为 v0.14 目标。
  - [ ] 把 approved template、结构化参数、profile 和 workspace plan 合成
    non-executing run packet。
  - [ ] 拒绝用户自由 command 和未批准参数。
- [ ] M11-S6: internal sanity-check runner gate，作为 v0.15 目标。
  - [ ] 先做固定 allowlisted runner id、timeout、stdout/stderr capture、
    artifact path 和审计边界设计。
  - [ ] 第一批实现仍以 metadata 和 review packet 为主，不接 SSH、Slurm、PBS、
    CUDA/MPI 或真实服务器。
- [ ] M11-S7: v1.0 internal preview closeout。
  - [ ] 汇总 v0.11-v0.15 的安全边界和测试结果。
  - [ ] 写内部用户手册、operator runbook、演示脚本、测试报告和学习总结。
  - [ ] 只有前置 gate 都满足，才标记 v1.0 internal preview。
- [ ] M11-T2: Add authentication and access control implementation.
- [ ] M11-T3: Add job workspace creation and cleanup.
- [ ] M11-T4: Add job submission, status polling, and cancellation.
- [ ] M11-T5: Add log collection and artifact indexing.
- [ ] M11-T6: Add loss curve and output model visualization.
- [ ] M11-T7: Add audit logging for submitted jobs.

Acceptance:

- Only approved users can submit jobs.
- Every job has a reproducible spec, logs, artifacts, and audit record.
- Failure handling is tested before lab users rely on it.

## Milestone 12: Backend Readiness Review

Goal: turn M11 preflight metadata into reviewable non-executing product flows
before any real backend adapter is connected.

Tasks:

- [x] M12-T1: Render an operator-facing backend readiness report from
  `BackendPreflightReport`.
- [x] M12-T2: Preview a dry-run submission packet for operator review.
- [x] M12-T3: Preview audit events and same-job audit logs without writing to a
  production audit store.
- [x] M12-T4: Show workspace and artifact path plans without creating remote
  directories.
- [x] M12-T5: Add v0.9 test report and Chinese learning summary.

Acceptance:

- v0.9 review output is generated from structured metadata, not free-form user
  shell commands.
- Reports keep `runtime_enabled` and runtime blockers visible.
- Runtime still rejects `local`, `ssh`, `slurm`, and `pbs` until M11-T1 has
  lab approval and operational details.
- No CUDA/MPI, SSH, Slurm, PBS, local wrapper, remote execution, credential
  loading, production audit store, arbitrary shell execution, or automatic Code
  Agent patch application is added.

## v1.0 Internal Preview Gate

The project can enter v1.0 internal preview after the single-server internal
path is implemented and tested. This is not public release and not a full
cluster platform.

Required before v1.0 internal preview:

- M11-S2 Safe Operations is implemented and tested.
- M11-S3 Fake Lifecycle is implemented and tested.
- M11-S4 Workspace Planner is implemented and tested.
- M11-S5 Approved Template Run Packet is implemented and tested.
- M11-S6 Internal Sanity-Check Runner Gate is documented and tested.
- User guide, operator runbook, demo script, test report, and learning summary
  exist for internal lab use.
- The system still rejects user free-form shell commands, real credential
  reading, SSH, Slurm/PBS, remote execution, and dangerous deletion.

Full real backend expansion still requires:

- Lab decision package for M11-T1 naming the first real backend and including
  credential policy, workspace root, authorization policy, audit retention,
  quota/operator rules, and operator contact.
- Authentication and access control implementation.
- Workspace lifecycle implementation.
- Job submission, status polling, and cancellation implementation.
- Log collection, artifact indexing, visualization, and audit logging.
- An intentional runtime backend guard change only after those controls pass
  review.
