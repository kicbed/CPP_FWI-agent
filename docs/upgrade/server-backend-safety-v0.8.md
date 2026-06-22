# v0.8 Server Backend Safety Design

Status: started on 2026-06-22. This document is the safety gate before any
real CUDA/MPI, SSH, Slurm, PBS, remote server, or local wrapper execution is
implemented.

## Purpose

v0.7 reserved the `JobBackend` interface and explicitly rejected every backend
except `dry_run`. v0.8 must keep that guard in place while designing the
controlled execution path that can be implemented later.

The design goal is to make job execution auditable, template-driven, and
bounded by authorization, workspace isolation, and artifact collection. The
platform must never turn free-form user text into a shell command.

## Non-Goals For This Design Step

- No real CUDA/MPI job execution.
- No SSH, Slurm, PBS, remote server, or local wrapper connection.
- No credentials, tokens, private keys, or cluster account handling.
- No arbitrary shell execution from user input.
- No automatic Code Agent patch application.
- No change to the v0.7 runtime guard that allows only `dry_run`.

## Threat Model

The first real backend will sit behind research-agent planning, so the main
risks are not only infrastructure failures. The main risks are unsafe
translation from language into execution.

Risk areas:

- Command injection: a user prompt or generated `JobSpec.command` could contain
  shell metacharacters, chained commands, redirection, or environment changes.
- Backend escalation: a JSON card or request could attempt to select `ssh`,
  `slurm`, `pbs`, or `local` before those adapters are explicitly enabled.
- Path traversal: dataset, config, log, or artifact paths could escape the
  approved workspace.
- Unauthorized submission: a user could submit jobs without lab approval,
  quota, or identity mapping.
- Workspace leakage: one experiment could read another experiment's configs,
  logs, checkpoints, or artifacts.
- Artifact confusion: a planner could cite stale logs or generated files from
  another run.
- Cancellation gaps: a running job could be untracked or uncancellable.
- Audit gaps: a failure could be impossible to reconstruct for lab review.

## Safety Principles

1. `dry_run` remains the only enabled backend until this design has been
   implemented and reviewed.
2. Real backends are selected by configuration, not by user text.
3. Users select approved job templates, not raw shell commands.
4. The backend receives structured arguments and renders commands internally.
5. Every job has a stable `ExperimentSpec`, `JobSpec`, template version,
   user identity, workspace path, status history, logs, artifacts, and audit
   record.
6. Workspaces are created under a configured backend workspace root and use
   generated job IDs. User-provided paths are treated as data references and
   validated before use.
7. Backend adapters own submission, polling, cancellation, and artifact
   collection. Planner and Code Agent code do not execute jobs.
8. Failure is explicit. Unsupported backend values, unsafe paths, missing auth,
   unknown templates, and command-shape violations are rejected before
   submission.

## Proposed API Shape

The current `JobBackend` contract supports:

- `type()`
- `validate(JobSpec)`
- `render(JobSpec)`
- `explain(JobSpec)`

v0.8 should add a server-execution layer above or beside this dry-run contract
rather than overloading `render` to submit work.

Proposed models:

```cpp
enum class JobLifecycleState {
    Draft,
    Rejected,
    Queued,
    Submitted,
    Running,
    Succeeded,
    Failed,
    Cancelled
};

struct JobSubmissionRequest {
    std::string request_id;
    std::string user_id;
    std::string experiment_id;
    JobBackendType backend_type;
    std::string template_id;
    std::string template_version;
    ExperimentSpec experiment;
    JobSpec job;
    bool dry_run;
};

struct JobRecord {
    std::string job_id;
    JobLifecycleState state;
    JobSubmissionRequest request;
    std::string workspace_path;
    std::vector<std::string> validation_messages;
    std::vector<std::string> status_events;
    std::vector<std::string> log_paths;
    std::vector<std::string> artifact_paths;
};
```

Future execution adapters should expose separate methods:

```cpp
class ServerJobBackend {
public:
    virtual ~ServerJobBackend() = default;
    virtual JobBackendType type() const = 0;
    virtual std::vector<std::string> validate_submission(
        const JobSubmissionRequest& request) const = 0;
    virtual JobRecord submit(const JobSubmissionRequest& request) = 0;
    virtual JobRecord poll(const std::string& job_id) = 0;
    virtual JobRecord cancel(const std::string& job_id) = 0;
};
```

The names are intentionally separate from `DryRunBackend::render`. Rendering a
preview and submitting work are different product actions and should remain
different APIs.

## Request Data Flow

1. User asks for an experiment plan.
2. Experiment Planner produces `ExperimentSpec` and dry-run `JobSpec`.
3. UI or API shows `dry_run: true` and requires explicit submission intent.
4. Submission request chooses a configured backend and approved template ID.
5. Server validates identity, authorization, backend enablement, template,
   workspace, argument shape, resource limits, and artifact policy.
6. Server creates a job record before any external submission occurs.
7. Backend adapter submits through the approved adapter only after validation.
8. Status polling appends state transitions to the job record.
9. Log and artifact collection stores paths in the record and exposes them to
   Planner diagnostics.
10. Audit records preserve request, template version, validation outcome,
    submitter, timestamps, backend, and final state.

## Backend Selection

The first implementation should still start with an execution-disabled server
backend skeleton:

- Keep `validate_backend_enabled` rejecting all non-`dry_run` values.
- Add models and tests for submission records, lifecycle states, and approved
  templates without connecting to a scheduler.
- Add a fake backend for tests only. It must not run commands.

After the skeleton is tested, the lab can decide which real adapter is least
risky:

- Slurm/PBS adapter: best when the lab already has scheduler policy, accounting,
  and queue limits.
- SSH adapter: higher credential and host-boundary risk; should require a
  stronger access-control design.
- Local wrapper adapter: useful for a single controlled server, but must still
  avoid raw user commands and must run only approved templates.

This repository should not choose or enable a real backend until credentials,
identity, workspace root, scheduler policy, and lab approval are known.

## Approved Template Boundary

An approved template is a versioned record maintained by developers or lab
operators. It defines:

- Template ID and version.
- Backend type it supports.
- Allowed executable or scheduler script name.
- Allowed arguments and their types.
- Resource limits for CPU, GPU, MPI ranks, memory, and wall time.
- Allowed input path roots.
- Expected log and artifact paths.
- Environment variables that may be set by the platform.

The user may choose a template and provide structured parameter values. The
user may not provide a shell command string.

## Workspace And Artifact Boundary

Each submitted job receives a generated workspace:

```text
<configured-workspace-root>/<job-id>/
  request.json
  experiment_spec.json
  job_spec.json
  rendered_preview.txt
  logs/
  artifacts/
  audit.jsonl
```

The exact root must be configured by the operator. User input must not control
the root. Paths stored in records should be normalized, checked against allowed
roots, and rejected if they escape the workspace.

## Product Boundary

The UI should keep dry-run previews and real submission as separate actions.
The user must be able to see:

- selected backend
- template ID and version
- resource request
- workspace ID
- current lifecycle state
- validation messages
- latest logs
- collected artifacts
- audit trail summary

The Planner may summarize logs and artifacts, but it should not submit,
cancel, or mutate jobs directly.

## Implementation Gate

Before enabling any real adapter, these checks must exist:

- Unit tests for backend enum parsing and rejection.
- Unit tests for approved-template validation.
- Unit tests for workspace path normalization and traversal rejection.
- Unit tests for lifecycle transitions.
- Tests proving user text cannot become a shell command.
- Tests proving non-enabled backend values are rejected.
- Manual operator checklist for credentials, workspace root, quotas, and audit
  retention.

Until all of these pass and a real backend is explicitly approved, the platform
must remain dry-run only.
