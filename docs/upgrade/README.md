# Lab Research Agent Upgrade Guide

This directory is the operating manual for upgrading this project into a
FWI-first research computing agent workbench.

Use it when starting a new upgrade session. The goal is to make each session
small, testable, and committed to git.

## Current Target

Upgrade the project from a rough multi-agent/FWI demo into:

> Lab Research Agent Platform for Seismic Computing

`v0.2 Lab Agent MVP` is complete as of 2026-06-11:

- Real Code Agent MVP.
- AlgorithmCard registry.
- ExperimentSpec and JobSpec data models.
- Dry-run experiment planning.
- Better README and roadmap.
- No real CUDA/MPI or cluster execution yet.

`v0.3 Research Knowledge Base` is complete as of 2026-06-12:

- JSON-backed paper, algorithm, experiment, and failure-case notes.
- Deterministic local loading and validation.
- Retrieval by note type, method, failure mode, parameter advice, and dataset.
- v0.3 test report and Chinese learning summary.

`v0.4 Experiment Planner` is complete as of 2026-06-12:

- Deterministic PlannerContext retrieval from user requests.
- Structured PlannerAnswer with algorithm recommendation, assumptions,
  parameter table, risk analysis, and next-step plan.
- ExperimentSpec JSON, dry-run JobSpec text, and reproducible experiment
  records grounded in AlgorithmCards and the v0.3 knowledge base.
- v0.4 test report and Chinese learning summary.

`v0.5 Lab Workbench UI` is complete as of 2026-06-12:

- Browser surface renamed to Lab Agent Workbench.
- Left-side sessions, AlgorithmCards, and experiment-history entry points.
- Center conversation surface preserved for HTTP and gRPC modes.
- Right-side inspector for route trace, tool calls, selected AlgorithmCard,
  parameter table, ExperimentSpec, JobSpec, dry-run artifacts, and service
  status.
- Static parsing helpers for ExperimentSpec JSON and dry-run JobSpec text.
- v0.5 smoke notes, screenshot path, test report, and Chinese learning summary.

`v0.6 Lab Code Adapter` is complete as of 2026-06-22:

- Config template reader and safe config generation.
- Log parser and loss curve extraction for supplied local text fixtures.
- Common failure recognizers for cycle skipping, stagnant loss, NaN/Inf, and
  resource-limit symptoms.
- Planner-facing diagnostic summaries that keep all execution dry-run only.
- v0.6 test report and Chinese learning summary.

`v0.7 JobBackend Reservation` is complete as of 2026-06-22:

- `JobBackend` interface with `validate`, `render`, `explain`, and backend type
  identity.
- Backend type enum values for `dry_run`, `local`, `ssh`, `slurm`, and `pbs`.
- Runtime guard that allows only `dry_run` and rejects all reserved or unknown
  backend values with clear messages.
- `AlgorithmCard` backend validation now uses the shared backend guard.
- v0.7 test report and Chinese learning summary.

`v0.8 Server Backend Safety Foundation` is complete as of 2026-06-22:

- Written safety design for auth, approved templates, workspace isolation, job
  lifecycle state, artifact collection, and audit logging.
- Server-job request and record models for future controlled execution.
- Approved job template validation.
- Workspace path traversal rejection.
- In-memory lifecycle record helpers.
- v0.8 test report and Chinese learning summary.
- Runtime remains dry-run only; non-`dry_run` backend values are still
  rejected.

Real CUDA/MPI, Slurm, PBS, SSH, or lab server execution is reserved for a later
backend milestone after the product and safety boundaries are stable.

## New Conversation Workflow

Every new upgrade conversation should follow this sequence.

1. Keep any copy-paste prompts in a local ignored file, for example
   `docs/upgrade/local-prompts.md`.
2. Ask the agent to read these files first:
   - `docs/upgrade/README.md`
   - `docs/upgrade/milestones.md`
   - `docs/upgrade/career-notes.md`
   - `docs/upgrade/version-roadmap.md`
   - `docs/upgrade/upgrade-log.md`
   - the active implementation plan under `docs/superpowers/plans/`
3. The agent checks `git status --short`.
4. The agent chooses the next unchecked task from the active plan.
5. The agent implements only that task or one tightly related batch.
6. The agent runs the required validation commands.
7. The agent updates `docs/upgrade/upgrade-log.md`.
8. If the change adds architecture, a technical capability, tests, deployment,
   or product story, the agent updates `docs/upgrade/career-notes.md`.
9. The agent commits the completed work to git.
10. The final response includes:
   - what changed
   - which tests ran
   - commit hash
   - next recommended task
   - a detailed Chinese knowledge summary for learning and interview prep when
     the task finishes a version, adds major architecture, or produces a test
     report

Knowledge summaries should be written in Chinese and detailed enough for later
study and interview preparation. Do not write only a few generic bullets. Use
sectioned prose with concrete engineering details. For version completions, test
reports, major architecture work, or meaningful technical capability changes,
include at least:

- The problem being solved and why the previous version was insufficient.
- The implementation approach, including data flow, API shape, important
  design tradeoffs, and why simpler or more complex alternatives were not used.
- Key files, tests, resources, and what each test protects.
- Safety or product boundaries, especially around CUDA/MPI, SSH, Slurm/PBS,
  remote execution, shell execution, and Code Agent write permissions.
- Debugging or TDD evidence when relevant: what failed first, what changed, and
  what verification proved.
- Interview preparation material: a short project pitch, a technical deep dive,
  likely follow-up questions with answers, and a STAR-style explanation.

## Required Validation

Run the smallest useful test set for every change, then broaden when the change
touches shared behavior.

| Change type | Required validation |
| --- | --- |
| Docs only | `git diff --check` |
| CMake or core C++ | `cmake --build build -j2` and `ctest --test-dir build --output-on-failure` |
| Tests only | `cmake --build build -j2` and the changed test binary through `ctest` |
| Agent routing | `ctest --test-dir build --output-on-failure` plus a manual curl or client smoke test if services can run |
| MCP plugin | Build `mcp_server_integrated`, run plugin or MCP integration test, then full `ctest` |
| Web UI | Serve `web/index.html`, check browser or curl health, and run `git diff --check` |
| Deploy script | Shell syntax check with `bash -n <script>` plus dry-run/manual command review |

Do not claim a task is complete unless the relevant validation command has been
run and its result is recorded in `docs/upgrade/upgrade-log.md`.

## Git Rules

Use small commits. One milestone can have many commits.

Suggested branch names:

- `upgrade/v0.2-code-agent`
- `upgrade/v0.2-algorithm-card`
- `upgrade/v0.2-experiment-planner`
- `upgrade/v0.2-workbench-ui`

Commit message style:

```text
docs: add lab agent upgrade roadmap
feat: add algorithm card registry
test: cover dry-run job backend
fix: route code intent to code agent
refactor: extract shared agent runtime
```

Before every commit:

```bash
git status --short
git diff --check
```

For code changes, also run the required build/test commands from the validation
matrix.

## Safety Boundaries

These rules stay in effect until the real server backend milestone.

- Do not execute real CUDA/MPI jobs.
- Do not run arbitrary shell commands from user input.
- Do not write or delete files outside the repository from an agent tool.
- Do not add SSH, Slurm, PBS, or remote execution until a reviewed v0.8 safety
  implementation explicitly enables a backend.
- Use `DryRunBackend` first. It may render commands and scripts, but it must not
  submit or execute them.
- Code Agent MVP should be read-only by default. Patch generation is allowed as
  text or explicit diff output. Automatic patch application is a later opt-in
  feature.

## Active Plans

Historical starting plan:

- `docs/superpowers/plans/2026-06-11-lab-agent-v0.2.md`
- `docs/superpowers/plans/2026-06-22-lab-code-adapter-v0.6.md`

Active plan:

- `docs/superpowers/plans/2026-06-22-server-backend-v0.8.md` (complete)

Next session should not connect real execution by default. Start Milestone 11
only after the first real backend, credentials, workspace root, authorization
policy, and lab approval are known.

When a milestone becomes too large, create a new plan in:

```text
docs/superpowers/plans/YYYY-MM-DD-<feature-name>.md
```

Each plan should produce working, testable software on its own.

## Local Prompts

Do not commit personal copy-paste upgrade prompts. If a prompt needs to be saved
locally, store it in `docs/upgrade/local-prompts.md`, which is ignored by git.
