# Repository continuity instructions

These instructions apply to the whole repository. They exist so a new Codex
session can continue the project without relying on an earlier chat window.

## Required startup reading

Before planning or changing this repository:

1. Prefer starting a new local session with `./scripts/codex-project.sh`; see
   `docs/CODEX_WORKFLOW.md`. If Codex is already open, continue with the same
   checks manually.
2. Read `docs/PROJECT_CONTINUITY.md` completely.
3. Inspect the current branch, `git status`, and relevant diffs. Existing local
   changes belong to the user and must not be overwritten.
4. Verify the implementation and tests instead of assuming that an accepted
   direction has already been implemented.

Direct user instructions in the current conversation take precedence over this
file and the continuity document.

The launcher snapshot is only a bounded hint captured at session start. All
Codex sessions share the live worktree, so re-read files, Git state, tests, and
service/job status before decisions that depend on them. Do not add an unsafe
background watcher to simulate real-time context.

## Durable decision protocol

- The user owns product and workflow decisions.
- Treat entries marked **Accepted** in `docs/PROJECT_CONTINUITY.md` as the
  current direction unless the user explicitly changes them.
- A technically promising new recommendation is not automatically accepted.
  Explain its evidence, benefit, cost, risk, and compatibility, then ask the
  user whether to adopt it.
- Add a new durable recommendation only after the user explicitly approves it.
  Record the approval date, status, rationale, implementation state, and
  verification evidence.
- Never silently replace an accepted decision. Mark the old entry superseded
  and link it to the replacement so later sessions can reconstruct why it
  changed.
- Keep **Accepted direction**, **Implemented**, and **Verified** separate. A
  design can be accepted but still pending implementation.
- Update the continuity document in the same change when an approved decision,
  major workflow, safety boundary, or known limitation changes.
- Do not store API keys, `.env` contents, credentials, private model data, or
  raw sensitive prompts in continuity files.

## FWI workflow guardrail

Do not implement a watcher that treats files manually placed in
`FWI_RUN_ROOT` as executable jobs. That directory is controlled output/state.
Follow the accepted task-entry direction in `docs/PROJECT_CONTINUITY.md` and
preserve the fixed MCP whitelist and structured validation boundary.
