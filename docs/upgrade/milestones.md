# Lab Research Agent Upgrade Milestones

This file is the long-running task board. Keep it updated after every upgrade
session.

Status markers:

- `[ ]` not started
- `[~]` in progress
- `[x]` complete

Version status:

- v0.2 Lab Agent MVP is complete as of 2026-06-11 according to
  `docs/superpowers/plans/2026-06-11-lab-agent-v0.2.md` and
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
- v0.6 Lab Code Adapter started on 2026-06-22 with
  `docs/superpowers/plans/2026-06-22-lab-code-adapter-v0.6.md`.
- v0.6 Lab Code Adapter is complete as of 2026-06-22 according to
  `docs/upgrade/version-roadmap.md` and `docs/upgrade/test-report-v0.6.md`.
- v0.7 JobBackend Interface Reservation is complete as of 2026-06-22
  according to `docs/upgrade/version-roadmap.md` and
  `docs/upgrade/test-report-v0.7.md`.
- v0.8 Server Backend Safety Design started on 2026-06-22 with
  `docs/upgrade/server-backend-safety-v0.8.md` and
  `docs/superpowers/plans/2026-06-22-server-backend-v0.8.md`.
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
- M11 lab backend decision package template exists as of 2026-06-23. It is a
  review worksheet only; M11-T1 remains incomplete until the lab supplies a
  concrete approved backend, credential policy, workspace root, authorization
  policy, audit retention, quota/operator rules, and operator contact.

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
- [ ] M11-T1: Decide the first real backend with lab approval: local wrapper,
  SSH, Slurm, or PBS.
  - 2026-06-23 note: `docs/upgrade/m11-lab-backend-decision-package.md`
    records the required decision package template. This does not complete
    M11-T1 and does not enable runtime execution.
  - 2026-06-23 note: `docs/upgrade/m11-lab-process-guide.md` records the
    Chinese lab process guide for approval, credentials, workspace,
    authorization, templates, quotas, monitoring, audit, and rollback.
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

## v1.0 Entry Gate

The project can enter v1.0 implementation only after M11 controlled real backend
integration is approved and completed.

Required before v1.0 implementation starts:

- Lab decision package for M11-T1 names the first backend and includes
  credential policy, workspace root, authorization policy, audit retention,
  quota/operator rules, and operator contact.
- Authentication and access control are implemented and tested.
- Workspace creation and cleanup are implemented and tested.
- Job submission, status polling, and cancellation are implemented and tested.
- Log collection, artifact indexing, loss-curve/output visualization, and audit
  logging are implemented and tested.
- The runtime backend guard is intentionally changed only after the above
  controls exist and pass review.
