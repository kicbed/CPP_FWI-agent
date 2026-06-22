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
| v1.0 | Lab-Usable Platform | New lab members can learn, plan, run, monitor, and analyze real research experiments safely |

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
  those prerequisites and an authorized submitter list without enabling
  `local`, `ssh`, `slurm`, or `pbs`.

## v1.0: Lab-Usable Platform

Purpose:

- Become a serious internal lab tool, not only a portfolio demo.

Must have:

- Newcomer learning workflow.
- Experiment planning workflow.
- Real job submission workflow.
- Monitoring and result analysis workflow.
- Algorithm extension workflow for new lab methods.
- Reproducible experiment records.
- Access control and audit logs.

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
8. If real execution is stable, harden toward v1.0.

## Handoff Rule For New Sessions

At the start of a new upgrade session, read these files:

- `docs/upgrade/README.md`
- `docs/upgrade/milestones.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/version-roadmap.md`
- `docs/upgrade/upgrade-log.md`
- the active plan in `docs/superpowers/plans/`

Then continue the first incomplete task for the current version. Validate the
change, update `upgrade-log.md`, update `career-notes.md` when the change adds
architecture or technical talking points, and commit.
