# v0.8 Study Pack: Server Backend Safety Foundation

Date: 2026-06-22

Audience: future self, interview review, lab demo preparation, and future
upgrade agents.

Status: v0.8 is complete. Real execution is still disabled.

## How To Use This Pack

This document is the main entrance for learning v0.8. It does not replace the
design, test report, or implementation plan. It tells you which file to read
first, what to look for in the code, and how to explain the version later.

Recommended reading order:

1. Read this study pack once from top to bottom.
2. Read `docs/upgrade/server-backend-safety-v0.8.md` for the safety design.
3. Read `research/include/agent_rpc/research/server_job.h` for the API shape.
4. Read `research/src/server_job.cpp` for validation behavior.
5. Read `tests/test_server_job.cpp` and map every test to a risk.
6. Read `docs/upgrade/learning-summary-v0.8.md` for the long Chinese review.
7. Read `docs/upgrade/test-report-v0.8.md` for verification evidence.
8. Read `docs/upgrade/v0.8-completion-audit.md` for the final acceptance
   checklist.

Fast path if you only have 30 minutes:

1. Read the "Core Story" section below.
2. Read the "Data Flow" section below.
3. Read the "Test Risk Map" section below.
4. Read the "Interview Script" section below.

Deep path if you have half a day:

1. Read this pack.
2. Open every code file named in "Code Reading Route".
3. Re-run the verification commands in "Verification".
4. Explain the data flow aloud without looking.
5. Answer the questions in "Self Check".

## Core Story

v0.8 solves one specific problem: before the platform can ever submit real lab
jobs, the repository must have a safe server-job boundary.

Earlier versions already had useful research-agent capability:

- v0.2 built the Lab Agent MVP.
- v0.3 added structured local research knowledge.
- v0.4 built deterministic experiment planning.
- v0.5 added the browser workbench.
- v0.6 added config and log adapters.
- v0.7 reserved the `JobBackend` interface and rejected all non-`dry_run`
  backends.

v0.8 builds on v0.7. It keeps the dry-run guard but adds the missing server job
model:

- A future submission request has a stable structure.
- A future job has a lifecycle record.
- A future executable shape must come from an approved template.
- A future workspace must stay under a configured root.
- A future failure can be recorded before any external job is submitted.

The important point is that v0.8 does not run jobs. It creates the safety
foundation that must exist before running jobs is even considered.

## What v0.8 Added

Code:

- `JobSubmissionRequest`
- `JobRecord`
- `JobLifecycleState`
- `ApprovedJobTemplate`
- `validate_submission_boundary`
- `validate_approved_template`
- `validate_workspace_path`
- `make_rejected_job_record`
- `append_lifecycle_event`

Tests:

- Default requests stay dry-run.
- Lifecycle state strings are stable.
- Non-dry-run backends are rejected.
- Unknown templates are rejected.
- Matching dry-run templates are accepted.
- Workspace traversal is rejected.
- Generated workspace names are accepted.
- Rejected records keep validation messages.
- Lifecycle events are in-memory only.

Docs:

- Safety design.
- Implementation plan.
- Test report.
- Long Chinese learning summary.
- This study pack.

## What v0.8 Did Not Add

v0.8 intentionally did not add:

- CUDA execution.
- MPI execution.
- `mpirun`.
- SSH.
- Slurm.
- PBS.
- Remote server execution.
- Local wrapper script execution.
- Credentials or private keys.
- Arbitrary shell execution from user input.
- Automatic Code Agent patch application.
- Real job submission, polling, cancellation, or monitoring.

If you explain v0.8, say this clearly. The version is valuable because it does
not jump into execution before the safety boundary exists.

## Data Flow

The safe future data flow is:

```text
User request
  -> Experiment Planner
  -> ExperimentSpec
  -> dry-run JobSpec
  -> user explicitly asks to submit in a future version
  -> JobSubmissionRequest
  -> validate_submission_boundary
  -> validate_approved_template
  -> validate_workspace_path
  -> JobRecord
  -> future backend adapter
```

In v0.8, the flow stops before the future backend adapter.

The most important separation is:

```text
DryRunBackend::render()
  = preview text, no side effects

Future ServerJobBackend::submit()
  = real side effect, must be separate and guarded
```

Do not blur these two concepts. Rendering a command preview and submitting a
real job are different product actions with different risk.

## Code Reading Route

Start with `research/include/agent_rpc/research/server_job.h`.

Read it in this order:

1. `JobLifecycleState`
2. `JobSubmissionRequest`
3. `JobRecord`
4. `ApprovedJobTemplate`
5. validation helper declarations

While reading, ask:

- What information would a future server need before submitting a job?
- Which fields are identity, which fields are execution intent, and which
  fields are audit data?
- Why does `JobSubmissionRequest` contain both `ExperimentSpec` and `JobSpec`?

Then read `research/src/server_job.cpp`.

Read it in this order:

1. `to_string(JobLifecycleState)`
2. `parse_job_lifecycle_state`
3. `validate_submission_boundary`
4. `validate_approved_template`
5. `validate_workspace_path`
6. `make_rejected_job_record`
7. `append_lifecycle_event`

While reading, ask:

- Which functions only validate data?
- Which functions mutate only an in-memory record?
- Which function keeps non-`dry_run` backends rejected?
- Where would real execution have to be added in a future version?

Then read `tests/test_server_job.cpp`.

Read every test name as a sentence about risk. The test file is not just a
correctness check. It is the executable safety spec for v0.8.

## Test Risk Map

`SubmissionRequestDefaultsToDryRun`

Risk protected: a future caller forgets to set backend fields and accidentally
creates an executable request.

Why it matters: safe defaults reduce the chance that missing fields turn into
real infrastructure side effects.

`ParsesLifecycleStateNames`

Risk protected: UI, API, storage, or audit logs disagree on job state names.

Why it matters: job records must be understandable after the fact. Stable names
make debugging and audit review possible.

`RejectsNonDryRunSubmissionBeforeBackendsAreEnabled`

Risk protected: someone asks for `slurm`, `pbs`, `ssh`, or `local` before the
backend is approved.

Why it matters: backend enum values exist for future design, but existing names
must not become enabled capabilities by accident.

`RequiresApprovedTemplateForSubmission`

Risk protected: user text or LLM output becomes a raw command.

Why it matters: approved templates are the boundary between planning text and
execution shape.

`AcceptsMatchingDryRunTemplate`

Risk protected: the validation layer becomes only a rejection layer and cannot
represent safe known templates.

Why it matters: future APIs need a positive path for controlled metadata.

`RejectsWorkspaceTraversal`

Risk protected: a job writes outside the configured workspace.

Why it matters: experiments generate configs, logs, checkpoints, and models.
Path traversal could leak or overwrite data.

`AcceptsGeneratedWorkspaceName`

Risk protected: the path guard becomes too strict to use.

Why it matters: safe generated leaf names are the expected future job directory
shape.

`CreatesRejectedRecordFromValidationErrors`

Risk protected: failed submissions disappear without an audit trail.

Why it matters: rejected jobs still matter. You need to know who asked for
what and why it was rejected.

`AppendsLifecycleEventWithoutExecutingCommands`

Risk protected: lifecycle state changes get confused with real queue actions.

Why it matters: `Queued` in an in-memory record is not the same as submitting to
Slurm, PBS, SSH, or any local wrapper.

## Design Tradeoffs

Why a structured model instead of shell filtering?

Shell filtering is fragile. It is hard to correctly handle metacharacters,
redirection, pipes, command substitution, environment changes, quoting, and path
escape. It also does not model user identity, resource limits, template version,
workspace, artifacts, or audit history.

Why approved templates?

Approved templates let the platform say: this exact class of job is allowed,
with these arguments, these input roots, these resources, and this backend type.
That is much stronger than letting a model generate a command string.

Why keep `dry_run` as the only enabled backend?

Because real backend execution depends on external facts that are not in the
repo: lab approval, credential storage, workspace root, user authorization,
quota policy, audit retention, and operator responsibility.

Why no fake scheduler in production code?

The v0.8 helpers are enough to test the safety model. A fake backend can come
later if it supports a specific interface test. Adding a fake scheduler too
early can blur what is real and what is only a simulation.

Why keep `render` and future `submit` separate?

Preview and execution have different permissions. A render method can show a
dry-run command. A submit method changes external state. Combining them would
make it easier to accidentally execute work while generating a preview.

## Security Boundaries To Memorize

CUDA/MPI:

v0.8 never launches CUDA or MPI. Resource fields are metadata only.

SSH:

No SSH client, host, key, remote command, or remote copy exists.

Slurm/PBS:

No `sbatch`, `squeue`, `scancel`, `qsub`, `qstat`, or `qdel` exists.

Remote execution:

No remote server adapter exists.

Shell execution:

User text does not become shell input.

Code Agent writes:

Code Agent remains read-only by default. It may propose patches as text, but it
does not apply them automatically.

M11 preflight:

The later `BackendApprovalDecision` gate records prerequisites for future real
backend selection. It still does not enable `local`, `ssh`, `slurm`, or `pbs`.

## Verification

The final verification commands for the v0.8 learning-doc closeout were:

```bash
cmake --build build -j2
ctest --test-dir build --output-on-failure
git diff --check
```

Expected result:

- build exits 0
- full CTest passes
- `git diff --check` produces no output

The v0.8 code report recorded 26/26 CTest passing.

## Interview Script

Short version:

I built the safety foundation for future server-side research job execution.
The system can plan dry-run FWI jobs, but real execution remains disabled. v0.8
adds structured submission records, approved-template validation, workspace
guards, lifecycle records, and tests proving non-dry-run backends are still
rejected.

Technical version:

The main risk is not how to call Slurm or SSH. The main risk is accidentally
turning natural language into shell execution. I separated planning from
submission. The Planner can produce `ExperimentSpec` and dry-run `JobSpec`, but
future submission must become `JobSubmissionRequest`, reference an
`ApprovedJobTemplate`, pass backend and workspace validation, and produce a
`JobRecord`. Runtime backend validation still accepts only `dry_run`, so enum
values like `slurm` and `pbs` are reserved but not enabled.

Tradeoff version:

I avoided both extremes. I did not rely on simple command filtering, because
that is not a reliable security boundary. I also did not implement a real
Slurm/PBS/SSH adapter, because the lab approval, credential, workspace, auth,
and audit requirements are not known yet. The version builds the foundation
that makes a future adapter safer.

STAR version:

Situation: the project could plan dry-run research experiments but had no
server job safety model.

Task: prepare for future controlled execution without enabling real execution.

Action: I added server job models, approved template validation, workspace path
guards, lifecycle helpers, tests, and documentation. I kept the existing
dry-run-only backend guard.

Result: the repo now has a tested safety boundary for future execution, while
real CUDA/MPI, SSH, Slurm/PBS, remote execution, and shell execution remain
disabled.

## Self Check

Question: What is the difference between `JobSpec` and `JobSubmissionRequest`?

Answer: `JobSpec` describes a planned job preview: command text, working
directory, environment, resources, and artifacts. `JobSubmissionRequest` is a
future server-side request that wraps identity, experiment ID, backend type,
template ID, template version, `ExperimentSpec`, `JobSpec`, and `dry_run`.

Question: Why is `ApprovedJobTemplate` important?

Answer: It prevents user text or LLM output from becoming the execution shape.
Future execution must pick a reviewed template with known arguments, inputs,
resources, and backend type.

Question: Why does v0.8 still reject `slurm`?

Answer: Because `slurm` is only a reserved backend value. Runtime enablement
requires a later milestone with lab approval, credentials, workspace root,
authorization policy, audit retention, and operator ownership.

Question: Why is workspace validation part of server backend safety?

Answer: Real jobs create and read files. Without workspace validation, a job
could escape its directory, overwrite other experiment data, or leak artifacts.

Question: What proves v0.8 did not accidentally enable execution?

Answer: The runtime guard test rejects non-`dry_run` submissions, and the code
contains no SSH, Slurm/PBS, remote execution, local wrapper execution, or shell
execution adapter.

Question: What should happen before M11-T1 continues?

Answer: The lab must choose a backend and provide approval, credential policy,
workspace root, authorization policy, audit retention, quota or operator rules,
and a clear owner for failures and cleanup.

## What To Do Next

Do not implement real execution yet.

The next engineering step should be one of these:

1. Review the v0.8 docs and code until the safety model is clear.
2. Get the lab decision package for M11-T1.
3. If no lab decision exists, improve documentation, tests, or UI explanation
   around the dry-run boundary.

The next code step must not connect CUDA/MPI, SSH, Slurm, PBS, remote servers,
local wrappers, credentials, or arbitrary shell execution unless M11-T1 is
explicitly approved with operational details.
