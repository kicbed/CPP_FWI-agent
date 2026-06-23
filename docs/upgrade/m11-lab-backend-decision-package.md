# M11 Lab Backend Decision Package

Date: 2026-06-23

Status: template created, not approved.

This package is the review worksheet required before M11-T1 can be marked
complete. It does not select a backend, load credentials, connect SSH, submit
Slurm or PBS jobs, run local wrappers, create workspaces, or change the runtime
backend guard.

Process guide:

- `docs/upgrade/m11-lab-process-guide.md`

## Decision State

Current decision:

- Selected backend: not selected.
- Approval reference: not supplied.
- Credential policy: not supplied.
- Workspace root: not supplied.
- Authorization policy: not supplied.
- Audit retention policy: not supplied.
- Quota or operator rules: not supplied.
- Operator contact: not supplied.

M11-T1 remains incomplete until the lab supplies a concrete decision package
covering every item above.

## Required Approval Record

A real backend decision must be recorded outside source code first, then copied
into project metadata without secrets.

Required fields:

- Selected backend: one of local wrapper, SSH, Slurm, or PBS.
- Lab approver and approval reference.
- Credential reference policy. Store only references, never passwords, tokens,
  private keys, or cluster account secrets in this repository.
- Workspace root and naming policy.
- Authorized submitters and access-control policy.
- Approved job templates and allowed structured parameters.
- Resource quotas, wall-time limits, GPU or MPI limits, and cancellation rules.
- Operator contact and escalation path.
- Audit retention policy and audit storage owner.
- Artifact collection policy for logs, loss curves, models, and diagnostics.
- Rollback plan for disabling the backend after an incident.

## Backend Comparison Worksheet

| Candidate | Fit | Main risks | Required controls before code |
| --- | --- | --- | --- |
| Local wrapper | Simple first integration for a controlled lab machine. | Shell injection, workspace escape, accidental heavy local jobs. | Approved templates only, no user command strings, per-user authorization, quota checks, workspace lifecycle, audit log, cancellation test. |
| SSH | Can target an existing remote lab host. | Credential handling, network failures, host trust, remote cleanup. | Credential reference policy, host allowlist, key rotation policy, workspace isolation, timeout/cancel behavior, audit trail, operator runbook. |
| Slurm | Best fit for shared HPC queues when the lab already uses Slurm. | Queue/account policy, scheduler errors, resource abuse, job cancellation edge cases. | Account and partition policy, sbatch template approval, sacct/squeue status mapping, quota limits, artifact collection, audit retention. |
| PBS | Best fit for labs with PBS/Torque-style clusters. | Queue syntax drift, account policy, status parsing, cancellation semantics. | qsub template approval, qstat/qdel status mapping, queue/account policy, quota limits, artifact collection, audit retention. |

This table is for review only. None of these candidates are enabled by this
document.

## Safety Gate Before Implementation

Before M11-T2 through M11-T7 begin, the selected package must pass these checks:

- `BackendApprovalDecision` can be filled with concrete, non-placeholder
  metadata.
- `validate_backend_approval_decision` accepts the approval record.
- `validate_submitter_authorization` accepts an authorized test submitter and
  rejects an unauthorized submitter.
- Approved job templates are versioned and reject unknown template IDs.
- Workspace names reject traversal, path separators, absolute paths, and user
  supplied directory roots.
- Audit events can be generated for requested, rejected, lifecycle, artifact,
  and operator-note events.
- `BackendPreflightReport.metadata_ready` can be true while `runtime_enabled`
  remains false.

The runtime guard must still reject `local`, `ssh`, `slurm`, and `pbs` until
authentication, workspace lifecycle, submission/status/cancellation, artifact
collection, visualization, and audit logging have tests.

## Handoff After Approval

After the lab supplies the decision package, continue in this order:

1. M11-T2: authentication and access-control implementation.
2. M11-T3: workspace creation and cleanup, scoped under the approved root.
3. M11-T4: submission, status polling, and cancellation for the selected
   backend only.
4. M11-T5: log collection and artifact indexing.
5. M11-T6: loss-curve and output-model visualization.
6. M11-T7: audit persistence and operator review.
7. Runtime backend guard change only after the above controls pass tests and
   review.

Do not start with scheduler or shell execution. Start with authorization,
workspace, template, and audit tests.

## Completion Rule For M11-T1

M11-T1 can be checked only when:

- The selected backend is named.
- The approval reference is concrete.
- The credential policy names where credentials are stored without exposing
  them.
- The workspace root and cleanup policy are concrete.
- The authorization policy and initial submitters are concrete.
- The audit retention policy and operator contact are concrete.
- The package has been reviewed by the lab owner.

Until then, this repository remains in dry-run and non-executing review mode.
