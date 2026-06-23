# v1.0 Internal Preview Test Report

日期：2026-06-23

## Scope

本次完成 v1.0 internal preview 收口。范围是 release/documentation consolidation：

- v0.11-v0.15 completion audit。
- internal user guide。
- operator runbook。
- demo script。
- consolidated test report。
- Chinese learning summary。
- README、milestones、version-roadmap、career-notes、upgrade-log 状态更新。

本次没有新增 C++ runtime code，也没有启用新的执行能力。

## Safety Gate Summary

| Gate | Status | Test |
| --- | --- | --- |
| v0.11 Safe Operations | complete | `SafeOperationsTest` |
| v0.12 Fake Lifecycle | complete | `SingleServerLifecycleTest` |
| v0.13 Workspace Planner | complete | `WorkspacePlannerTest` |
| v0.14 Approved Template Run Packet | complete | `ApprovedTemplateRunPacketTest` |
| v0.15 Internal Sanity Runner Gate | complete | `InternalSanityRunnerTest` |

The full suite also covers the earlier research platform foundations, including
Code Agent, AlgorithmCard/Registry, ExperimentSpec, ResearchKnowledge,
PlannerContext/Answer, Lab Code Adapter, ServerJob preflight, Web branding,
A2A, MCP, routing, registry, and communication integration tests.

## Verification Commands

Commands run before marking v1.0 internal preview:

```bash
git diff --check
cmake --build build -j2
ctest --test-dir build --output-on-failure
```

Observed result:

- PASS. `git diff --check` produced no output.
- PASS. `cmake --build build -j2` exited 0.
- PASS. `ctest --test-dir build --output-on-failure` passed 32/32 tests.

Focused internal preview gate command:

```bash
ctest --test-dir build -R "(SingleServerBackendTest|SafeOperationsTest|SingleServerLifecycleTest|WorkspacePlannerTest|ApprovedTemplateRunPacketTest|InternalSanityRunnerTest)" --output-on-failure
```

These tests prove the internal preview review-only workflow is intact. They do
not prove real backend execution, because real backend execution remains out of
scope and disabled.

## What The Internal Preview Can Do

- Show single-server profile/template/review request metadata.
- Render dry-run review packets without secrets or execution.
- Enforce simple internal roles: `lab_root`, `lab_user`, `readonly`.
- Render delete dry-run review packets without deletion or trash movement.
- Simulate lifecycle states in memory.
- Preview workspace, run, log, and artifact paths.
- Combine approved templates, structured parameters, workspace plans, and
  lifecycle ids into non-executing run packets.
- Review fixed sanity runner metadata with timeout, capture plan, artifact path,
  and audit event planning.

## What The Internal Preview Cannot Do

- It cannot execute CUDA/MPI or run lab code.
- It cannot execute shell commands from user input.
- It cannot load credentials or connect to a server.
- It cannot submit SSH, Slurm, PBS, local wrapper, or remote jobs.
- It cannot create workspaces or directories.
- It cannot delete files or move trash.
- It cannot collect real stdout/stderr, logs, or artifacts.
- It cannot persist production audit logs.
- It cannot automatically apply Code Agent patches.

## Test Coverage Notes

The most important safety assertions are:

- `SingleServerBackendTest` rejects runtime-enabled profiles, inline secret-like
  credential references, unknown parameters, and non-dry-run requests.
- `SafeOperationsTest` proves even `lab_root` cannot request real deletion and
  that delete preview remains blocked for traversal, protected paths, symlinks,
  workspace root deletion, and missing confirmation.
- `SingleServerLifecycleTest` keeps lifecycle transitions in memory and blocks
  invalid terminal-state transitions.
- `WorkspacePlannerTest` rejects traversal, absolute escape, protected roots,
  and protected labels while keeping directory/file/server flags false.
- `ApprovedTemplateRunPacketTest` rejects free-form commands and unapproved
  parameters and keeps execution/credentials/server/workspace flags false.
- `InternalSanityRunnerTest` rejects unknown runner ids, free-form commands,
  deletion, credential reads, SSH, Slurm, PBS, remote access, artifact escapes,
  traversal, and missing timeout/capture metadata.

## Release Decision

The v0.11-v0.15 safety gates are complete and the full test suite passes.
Therefore the repository is marked as `v1.0 internal preview` for internal lab
review workflows.

Real backend work remains blocked until the later M11-T1 to M11-T7 controls are
approved, implemented, tested, and intentionally wired into the runtime guard.
