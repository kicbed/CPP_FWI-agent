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

The next target is `v0.4 Experiment Planner`: structured experiment planning,
risk analysis, dry-run jobs, and reproducible experiment records grounded in
AlgorithmCards and the v0.3 knowledge base.

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

Knowledge summaries should be written in Chinese and should be detailed enough
for later study and interview preparation. Prefer this structure: problem being
solved, implementation approach, key files/tests, safety or product boundary,
and a short interview-ready explanation.

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
- Do not add SSH, Slurm, PBS, or remote execution as part of v0.2.
- Use `DryRunBackend` first. It may render commands and scripts, but it must not
  submit or execute them.
- Code Agent MVP should be read-only by default. Patch generation is allowed as
  text or explicit diff output. Automatic patch application is a later opt-in
  feature.

## Active Plans

Start with:

- `docs/superpowers/plans/2026-06-11-lab-agent-v0.2.md`

When a milestone becomes too large, create a new plan in:

```text
docs/superpowers/plans/YYYY-MM-DD-<feature-name>.md
```

Each plan should produce working, testable software on its own.

## Local Prompts

Do not commit personal copy-paste upgrade prompts. If a prompt needs to be saved
locally, store it in `docs/upgrade/local-prompts.md`, which is ignored by git.
