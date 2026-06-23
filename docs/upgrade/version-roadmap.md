# Version Roadmap

This file records the long-term version direction. It is safe to commit because
it contains product goals and handoff rules, not personal copy-paste prompts.

Personal prompts should stay in ignored local files such as
`docs/upgrade/local-prompts.md`.

## Version Summary

| Version | Name | Main Outcome |
| --- | --- | --- |
| v0.2 | Lab Agent MVP | Real Code Agent, AlgorithmCard, ExperimentSpec, JobSpec, DryRunBackend, truthful demo docs |
| v0.3 | Research Knowledge Base | PaperNote, AlgorithmNote, ExperimentNote, FailureCase, parameter-advice retrieval |
| v0.4 | Experiment Planner | Structured experiment planning, risk analysis, dry-run jobs, reproducible experiment records |
| v0.5 | Lab Workbench UI | Web workbench with routing, tool calls, specs, parameter tables, dry-run jobs, and status panels |
| v0.6 | Lab Code Adapter | Integrate with lab code shape without submitting jobs: config templates, log parsing, loss analysis |
| v0.7 | JobBackend Reservation | Reserve backend interface and reject all non-dry-run execution choices |
| v0.8 | Server Backend | Add the safety foundation for controlled execution: approved templates, workspaces, lifecycle records, and audit boundaries |
| v0.9 | Backend Readiness Review | Turn preflight metadata into non-executing review, packet preview, audit preview, and operator checklist flows |
| v0.10 | Single Server Runner Preparation | Prepare metadata-only single-server profiles, approved templates, and dry-run review packets before any real server connection |
| v0.11 | Safe Operations Policy | Plan internal lab roles, safe operation allowlists, and deletion dry-run review packets before any destructive operation exists |
| v0.12 | Fake Lifecycle | Simulate single-server job lifecycle states without connecting to a server |
| v0.13 | Workspace Planner | Preview workspace, log, and artifact paths with path-safety validation |
| v0.14 | Approved Template Run Packet | Render approved-template run packets from structured parameters without executing commands |
| v0.15 | Internal Sanity-Check Runner Gate | Define the fixed-runner gate before any limited execution is enabled |
| v1.0 | Internal Preview | Let lab members try the single-server workflow with review packets, lifecycle, docs, and safety gates |

## v0.2: Lab Agent MVP

Status: Completed on 2026-06-11 for the MVP scope listed below.

Purpose:

- Turn the project from a rough FWI/multi-agent demo into a usable research
  computing agent prototype.

Must have:

- Code Agent MVP.
- AlgorithmCard model and seed cards.
- ExperimentSpec and JobSpec.
- DryRunBackend that never executes real jobs.
- Experiment Planner Agent skeleton.
- README and demo docs that clearly state current limits.

Not included:

- Real CUDA/MPI execution.
- SSH, Slurm, PBS, or remote server execution.
- Automatic code patch application.

Historical first task:

- Start with Code Agent MVP because the Orchestrator already has a `code`
  intent branch, but no real Code Agent implementation.

Next target after v0.2:

- Start v0.3 Research Knowledge Base so planner advice can be grounded in
  structured paper, algorithm, experiment, and failure-case notes.

## v0.3: Research Knowledge Base

Status: Completed on 2026-06-12 with JSON-backed local research knowledge
notes, typed directories, deterministic loading, and retrieval tests.

Purpose:

- Make parameter advice grounded in local paper notes, algorithm notes,
  experiment notes, and failure cases.

Must have:

- `PaperNote` for papers and method claims.
- `AlgorithmNote` for assumptions, parameters, and applicable scenarios.
- `ExperimentNote` for historical experiments and results.
- `FailureCase` for common symptoms and diagnosis.
- Retrieval by method, failure mode, parameter, and dataset.

Example user value:

- "Low frequency is missing. Should I use multi-scale FWI, AWI, envelope
  inversion, or frequency extrapolation?"

Next target after v0.3:

- Start v0.4 Experiment Planner so user requests can produce structured
  algorithm recommendations, parameter tables, risk analysis, dry-run JobSpecs,
  and reproducible experiment records grounded in v0.3 knowledge.

## v0.4: Experiment Planner

Status: Completed on 2026-06-12 with deterministic PlannerContext retrieval and
PlannerAnswer generation for structured dry-run experiment plans.

Purpose:

- Make the planner useful enough for junior lab members to draft experiments.

Must have:

- Algorithm recommendation.
- Parameter table.
- Assumption list.
- Risk analysis.
- Dry-run JobSpec.
- Next-round tuning suggestions.
- Reproducible experiment record.

Example user value:

- "Plan a Marmousi multi-scale FWI experiment and explain what to adjust if
  loss does not decrease."

Completed scope:

- Retrieve request-specific AlgorithmCards and ResearchKnowledge notes.
- Generate algorithm recommendation, assumptions, parameter table, risk
  analysis, and next-step plan.
- Generate ExperimentSpec JSON.
- Generate dry-run JobSpec text.
- Generate reproducible experiment records.

Next target after v0.4:

- Start v0.5 Lab Workbench UI so users can inspect routing, tool calls,
  AlgorithmCards, ExperimentSpec, JobSpec, parameter tables, dry-run jobs, and
  service status from the browser.

## v0.5: Lab Workbench UI

Status: Completed on 2026-06-12 with static Lab Agent Workbench panels,
inspector rendering, status indicators, smoke notes, and v0.5 test report.

Purpose:

- Make the system look and feel like a research workbench, not a generic chat
  page.

Must have:

- Left panel: sessions, algorithms, experiment history.
- Center panel: conversation and plan.
- Right panel: route trace, tool calls, AlgorithmCard, ExperimentSpec, JobSpec,
  parameter table, and artifacts.
- Status: Orchestrator, Registry, MCP, Embedding, Code Agent, Planner Agent.

Example user value:

- A demo viewer can see how agents reason, which tools were used, and what dry
  run would be executed.

Completed scope:

- Left panel includes sessions, AlgorithmCards, and experiment-history entry
  points.
- Center panel keeps the HTTP/gRPC conversation workflow.
- Right inspector shows route trace, tool calls, selected AlgorithmCard,
  parameter table, ExperimentSpec, JobSpec, and dry-run state.
- Status indicators cover Orchestrator, Registry, MCP, Embedding, Code Agent,
  Planner Agent, and gRPC bridge.
- Static frontend helpers parse ExperimentSpec JSON blocks and dry-run JobSpec
  text blocks from planner answers without submitting jobs.

Next target after v0.5:

- Start v0.6 Lab Code Adapter so users can inspect lab-style config templates,
  logs, loss curves, and common failure patterns without running jobs.

## v0.6: Lab Code Adapter

Status: Completed on 2026-06-22 with deterministic config-template loading,
dry-run config previews, supplied log parsing, loss curve extraction, common
failure recognition, Planner-facing diagnostic summaries, and
`docs/upgrade/test-report-v0.6.md`.

Purpose:

- Adapt to the shape of the lab's CUDA/MPI FWI code without executing server
  jobs yet.

Must have:

- Config template reader.
- Config generator.
- Log parser.
- Loss curve parser.
- Common error/failure recognizer.
- Parameter tuning suggestion based on logs and knowledge.

Not included:

- Job submission to real servers.
- SSH, Slurm, PBS, remote execution, arbitrary shell execution, or automatic
  Code Agent patch application.

Example user value:

- "Here is my FWI log and config. Why is the loss not decreasing?"

Completed scope:

- Added a tested config template reader that rejects execution fields and keeps
  all outputs as local dry-run previews.
- Added supplied-text log parsing, loss curve extraction, warning/status
  extraction, and fixture-backed tests.
- Added deterministic failure recognizers for loss stagnation, NaN/Inf,
  cycle-skipping risk, and resource-limit symptoms.
- Added Planner-facing diagnostic summaries with explicit dry-run safety
  boundaries.

Next target after v0.6:

- Reserve the future JobBackend interface and keep non-`dry_run` backends
  rejected until server execution receives an explicit safety design.

## v0.7: JobBackend Reservation

Status: Completed on 2026-06-22 with a `JobBackend` interface, explicit
backend type enum values, shared runtime rejection for non-`dry_run` backends,
and `docs/upgrade/test-report-v0.7.md`.

Purpose:

- Make the future execution boundary explicit before any real backend exists.

Must have:

- `JobBackend` interface for validate/render/explain behavior.
- Backend type enum values for `dry_run`, `local`, `ssh`, `slurm`, and `pbs`.
- Shared parsing and validation helpers for backend values.
- Runtime rejection for all non-`dry_run` or unknown backend choices.
- Documentation for how Slurm/PBS can be added later without turning user
  input into shell execution.

Not included:

- Real CUDA/MPI execution.
- SSH, Slurm, PBS, local server execution, remote execution, or arbitrary shell
  execution.
- Automatic Code Agent patch application.

Example user value:

- "Show me the dry-run job boundary and explain why this cannot submit to the
  cluster yet."

Completed scope:

- Added `JobBackendType` values for `dry_run`, `local`, `ssh`, `slurm`, `pbs`,
  and `unknown`.
- Added `parse_job_backend_type`, `to_string`,
  `supported_job_backend_names`, and `validate_backend_enabled`.
- Made `DryRunBackend` expose its backend type through the interface.
- Reused the shared backend guard in `AlgorithmCard` validation so JSON cards
  cannot silently enable reserved backends.

Next target after v0.7:

- Start v0.8 only after writing the server-backend safety design for auth,
  workspace isolation, approved templates, audit logging, job status, and
  artifact collection.

## v0.8: Server Backend Safety Foundation

Status: Completed on 2026-06-22 with server-job safety models, approved
template validation, workspace guards, lifecycle helpers, and
`docs/upgrade/test-report-v0.8.md`. No real execution backend is enabled yet.

Purpose:

- Build the safety foundation required before controlled real execution can be
  connected.

Must have:

- Written safety design.
- Server-job request and record models.
- Approved template validation.
- Workspace isolation and path traversal rejection.
- Job lifecycle state and in-memory lifecycle history helpers.
- Tests proving non-`dry_run` backend choices remain rejected.
- Documentation and learning summary for future backend work.

Example user value:

- "Submit this approved FWI dry-run plan to the lab queue and monitor it."

Completed scope:

- Keep `validate_backend_enabled` rejecting all non-`dry_run` backend values.
- Define server-job submission and lifecycle record models before real
  submission code exists.
- Require approved templates instead of raw user commands.
- Validate workspace paths so job files cannot escape the configured root.
- Add lifecycle helpers that mutate only in-memory job records and never
  execute commands.

Not included in v0.8:

- Real CUDA/MPI execution.
- SSH, Slurm, PBS, remote server, or local wrapper execution.
- Credentials or cluster account handling.
- Arbitrary shell execution from user input.
- Automatic Code Agent patch application.

Next target after v0.8:

- Start controlled real backend integration only after lab approval, a selected
  backend, credential handling, workspace root, authorization policy, audit
  retention, and operator responsibilities are known. As a preflight step, the
  code now has a metadata-only `BackendApprovalDecision` validator that records
  those prerequisites, an authorized submitter list, job audit event metadata,
  and in-memory audit log validation without enabling `local`, `ssh`, `slurm`,
  or `pbs`.

## v0.9: Backend Readiness Review

Status: Completed on 2026-06-22 with non-executing readiness report rendering,
dry-run submission packet preview, audit log preview, workspace/artifact path
preview, and `docs/upgrade/test-report-v0.9.md`.

Purpose:

- Turn the M11 preflight metadata into reviewable product flows before any real
  backend adapter is connected.

Must have:

- Display or generate a backend readiness report from `BackendPreflightReport`.
- Preview a dry-run submission packet for operator review.
- Preview audit events and same-job audit logs without writing to a production
  audit store.
- Show workspace and artifact path plans without creating remote directories.
- Keep the runtime guard visible: `local`, `ssh`, `slurm`, and `pbs` remain
  reserved until M11-T1 is approved.

Not included:

- Real CUDA/MPI execution.
- SSH, Slurm, PBS, local wrapper, or remote server execution.
- Credential loading or cluster account handling.
- Arbitrary shell execution from user text.
- Automatic Code Agent patch application.

Completed scope:

- Added `render_backend_preflight_report` so the structured M11 preflight result
  can be displayed as stable operator-facing text with metadata readiness,
  runtime enablement state, validation errors, runtime blockers, and safety
  boundaries.
- Added `render_dry_run_submission_packet` for operator review of request,
  experiment, template, resource, and command-preview metadata without
  submitting or executing a job.
- Added `render_job_audit_log_preview` for same-job audit event preview without
  writing to a production audit store.
- Added `render_workspace_artifact_plan` for workspace and artifact path review
  without creating local or remote directories.

v1.0 entry gate:

- v1.0 internal preview should start only after the v0.11-v0.15 single-server
  safety gates are implemented, documented, and tested.
- M11 实验室后端决策包模板位于
  `docs/upgrade/m11-lab-backend-decision-package.md`，但它不是批准记录，
  也不会选择或启用真实后端。
- 中文实验室流程指南位于 `docs/upgrade/m11-lab-process-guide.md`，说明
  M11-T1 完成前实验室必须确认哪些信息。
- 单服务器账号路线位于 `docs/upgrade/single-server-backend-v0.10.md` 和
  `docs/upgrade/v1.0-internal-preview-roadmap.md`，适用于当前“一个服务器账号、
  自己或小组内部先跑”的初步阶段。
- 随后 M11-T2 到 M11-T7 必须实现并测试身份认证/访问控制、workspace 生命周期、
  提交/状态/取消、日志和 artifact 收集、可视化以及审计日志。
- 在这些控制存在之前，项目可以做内部预览和非执行评审工作，但不能声称已经具备
  完整真实后端执行能力。

## v0.10: Single Server Runner Preparation

Status: Completed on 2026-06-23 for the metadata/profile/template and dry-run
review packet scope, with `docs/upgrade/test-report-v0.10.md` and
`docs/upgrade/learning-summary-v0.10.md`. No real server execution is enabled.

Purpose:

- Match the current lab reality: one server account used by the researcher or a
  small group before a full multi-user platform exists.
- Prepare a small, testable metadata boundary for profile, approved template,
  structured review request, and dry-run review packet.

Must have:

- `SingleServerProfile` metadata that stores only account, credential, and
  workspace references, not secrets or real credentials.
- `SingleServerJobTemplate` metadata for fixed approved entries and allowed
  structured parameters.
- Validation that rejects empty credential references, inline secret-looking
  values, unknown templates, profile/template mismatch, unapproved parameters,
  and `dry_run == false`.
- A dry-run review packet renderer that says execution, credential loading,
  server connection, and workspace creation are disabled.

Not included:

- Real CUDA/MPI execution.
- SSH, Slurm, PBS, local wrapper, or remote server connection.
- Credential loading or real server account handling.
- Workspace creation, deletion, cleanup, or artifact collection.
- Production audit persistence.
- Arbitrary shell execution from user input.
- Automatic Code Agent patch application.

Next target after v0.10:

- Start v0.11 safe operations so internal lab roles and dangerous operation
  boundaries are defined before fake lifecycle or real backend work.

## v0.11: Safe Operations Policy

Status: Started on 2026-06-23 with design and learning summary. No runtime
implementation is enabled yet. Detailed agent plans and copy-paste prompts are
kept in local ignored files, not in Git.

Purpose:

- Match the internal lab deployment model without building a complex public
  multi-tenant platform.
- Keep roles simple: `lab_root`, `lab_user`, and `readonly`.
- Prevent dangerous tool behavior, especially accidental deletion of code,
  environments, datasets, credentials, or other users' results.

Must have:

- `LabAccountRole` metadata for simple internal roles.
- `SafeOperationType` metadata for read, review, approved-template dry-run, and
  delete-preview operations.
- `SafeOperationPolicy` validation so role simplicity does not become
  unrestricted execution.
- `DeleteReviewRequest` and `DeleteReviewPacket` metadata for deletion preview
  only.
- Tests proving real deletion remains disabled even for `lab_root`.

Not included:

- Real deletion.
- Trash move.
- Filesystem remove.
- Shell execution.
- SSH, Slurm, PBS, local wrapper, or remote server connection.
- Credential loading.
- Workspace creation or cleanup.

Next target after the v0.11 plan:

- Implement the safe operation metadata, validation helpers, delete dry-run
  review packet renderer, and tests.

## v0.12: Fake Lifecycle

Status: Planned on 2026-06-23 as part of the v1.0 internal preview roadmap.

Purpose:

- Give the single-server account workflow visible job states before any real
  server connection exists.
- Let users and operators review lifecycle behavior without submitting jobs.

Must have:

- Lifecycle metadata states for requested, reviewed, approved, rejected,
  queued, running, succeeded, failed, and cancelled.
- Validation for allowed state transitions.
- Review packet rendering that explains current state, next allowed action, and
  safety blockers.
- Tests proving state changes stay in memory and do not execute commands.

Not included:

- Real server connection.
- Workspace creation.
- CUDA/MPI, SSH, Slurm, PBS, local wrapper, or remote execution.
- Credential loading.
- Shell execution.

Next target after v0.12:

- Add workspace planning so lifecycle records can point at safe preview paths.

## v0.13: Workspace Planner

Status: Planned on 2026-06-23 as part of the v1.0 internal preview roadmap.

Purpose:

- Show where a future run would place workspace, logs, and artifacts without
  creating directories or moving files.
- Catch path traversal and dangerous root mistakes before any runner exists.

Must have:

- Workspace, run directory, log path, and artifact path preview metadata.
- Validation that all paths remain under the configured lab workspace root.
- Rejection for empty roots, path traversal, absolute escape attempts, and
  protected locations.
- Tests proving no filesystem creation, deletion, or movement occurs.

Not included:

- Directory creation.
- Directory deletion.
- Cleanup jobs.
- Remote filesystem access.

Next target after v0.13:

- Add approved-template run packet rendering from profile, template,
  structured parameters, lifecycle, and workspace plan.

## v0.14: Approved Template Run Packet

Status: Planned on 2026-06-23 as part of the v1.0 internal preview roadmap.

Purpose:

- Convert approved templates and structured parameters into a reviewable run
  packet without executing anything.
- Keep user intent separate from shell commands.

Must have:

- Run packet metadata for profile id, template id, parameter values, workspace
  preview, artifact preview, resource limits, and lifecycle id.
- Validation for required parameters, unknown parameters, template/profile
  mismatch, and user free-form command rejection.
- Rendering that explicitly says `command_executed: false`.
- Tests for valid packets and rejection paths.

Not included:

- Real command execution.
- User free-form command strings.
- Credential loading.
- Server connection.

Next target after v0.14:

- Define the internal sanity-check runner gate before any limited execution is
  considered.

## v0.15: Internal Sanity-Check Runner Gate

Status: Planned on 2026-06-23 as part of the v1.0 internal preview roadmap.

Purpose:

- Decide the smallest safe shape of future execution before enabling it.
- Keep the first possible runner fixed, allowlisted, observable, and
  non-destructive.

Must have:

- Fixed runner id metadata.
- No user command string.
- Timeout metadata.
- stdout/stderr capture plan.
- Artifact path plan under workspace root.
- Audit event plan.
- Tests proving free-form commands, deletion requests, credential reads, SSH,
  Slurm, PBS, and remote server access remain rejected.

Not included in the first v0.15 batch:

- Real CUDA/MPI.
- SSH, Slurm, PBS, or remote execution.
- General shell execution.
- Destructive file operations.

## v1.0: Lab-Usable Platform

Status: Long-term product name. The current near-term target is
`v1.0 internal preview`, not a public release.

Purpose:

- Become a serious internal lab tool, not only a portfolio demo.

Must have:

- Newcomer learning workflow.
- Experiment planning workflow.
- Internal single-server review workflow.
- Lifecycle, logs, and artifact preview workflow.
- Algorithm extension workflow for new lab methods.
- Reproducible experiment records.
- Safety gates for roles, workspace paths, approved templates, lifecycle, and
  audit metadata.

Internal preview scope:

- Use the simple lab account model: `lab_root`, `lab_user`, and `readonly`.
- Let users choose approved templates and structured parameters.
- Generate review packets before execution.
- Show lifecycle and artifact/log preview information.
- Keep dangerous deletion disabled.
- Keep real credentials out of the repository.
- Keep SSH, Slurm, PBS, and general remote execution out of scope until a later
  lab-approved backend expansion.

Full backend expansion later adds:

- Real job submission workflow.
- Monitoring and result analysis from collected logs/artifacts.
- Authentication and access control beyond the simple internal role model.
- Persistent audit logging.

Product identity:

- Seismic Research Computing Multi-Agent Workbench.

FWI remains the first flagship use case. The platform should support frequency
extrapolation, post-stack algorithms, forward modeling, velocity-modeling tools,
and new lab algorithms through AlgorithmCards and backend adapters.

## How To Decide The Current Version

Use this order:

1. If Code Agent, AlgorithmCard, ExperimentSpec, JobSpec, and DryRunBackend are
   not complete, continue v0.2.
2. If v0.2 is complete but structured paper/algorithm/experiment/failure-case
   notes are missing, start v0.3.
3. If knowledge is structured but planner output is not yet reliable and
   reproducible, start v0.4.
4. If planner works but the UI still looks like chat, start v0.5.
5. If UI works but lab code configs/logs/loss curves are not integrated, start
   v0.6.
6. If lab code adapter works but the backend boundary is not reserved and
   hardened, start v0.7.
7. If v0.7 is complete, start v0.8 with a written safety design and tested
   server-job safety models before enabling controlled execution.
8. If v0.8 and v0.9 are complete, use v0.10-v0.15 to build the non-executing
   single-server internal preview path.
9. If v0.11-v0.15 safety gates are complete and tested, close out v1.0
   internal preview with user docs, operator docs, demo script, and test report.
10. If the lab later approves real backend expansion, continue M11-T1 through
    M11-T7 before enabling SSH, Slurm, PBS, remote execution, or CUDA/MPI jobs.

## Handoff Rule For New Sessions

At the start of a new upgrade session, read these files:

- `docs/upgrade/README.md`
- `docs/upgrade/milestones.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/version-roadmap.md`
- `docs/upgrade/upgrade-log.md`
- the active local plan in `docs/superpowers/plans/`, if present in the local
  ignored workspace

Then continue the first incomplete task for the current version. Validate the
change, update `upgrade-log.md`, update `career-notes.md` when the change adds
architecture or technical talking points, and commit.
