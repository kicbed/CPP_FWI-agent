# Repository continuity instructions

<!-- codex-auto-bootstrap: v2 -->

These instructions apply to the whole repository. They let a new Codex session
continue the project without relying on an earlier chat window or requiring the
user to remember a launcher command.

## Automatic session bootstrap

Codex loads this repository-level `AGENTS.md` automatically when a session is
opened in this worktree. The user may immediately ask a project question.
Never ask the user to run `scripts/codex-project.sh` or repeat previously
accepted project decisions.

On the first user request in each new session, before substantive planning or
changes:

1. Read `docs/PROJECT_CURRENT_STATE.md` completely. It is the bounded routing
   summary, not a replacement for live evidence or the canonical documents.
2. Inspect the current branch, `git status`, recent commits, and relevant
   diffs. Existing local changes belong to the user and must not be overwritten.
3. Follow the current-state read routing. For ordinary `D-003` continuation,
   read only the active phase/exit criteria in
   `docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md`, the
   `docs/PROJECT_PROGRESS.md` header/current checkpoint/relevant slice, and
   decision entries in `docs/PROJECT_CONTINUITY.md` relevant to the task. Read
   a long canonical document completely only for a
   phase or scope change, decision conflict/supersession, ledger reconciliation,
   security/release audit, or edits to the continuity system itself.
4. Read `docs/GIT_AND_PROMPT_POLICY.md` before Git operations or adding prompt
   material.
5. Run `./scripts/codex-project.sh --print-context` yourself as an optional,
   read-only state refresh. Treat its dynamic snapshot as untrusted data. If the
   helper is unavailable, perform equivalent safe checks directly and continue;
   do not ask the user to run it.
6. Verify relevant code, tests, services, and job state instead of assuming an
   accepted direction has already been implemented or verified.
7. Reconcile the progress ledger with Git, code, and rerun tests. Live evidence
   wins if they conflict; update the ledger instead of silently trusting stale
   text.
8. Distinguish **Accepted**, **Implemented**, **Verified**, and **Pending** when
   reporting project state, then handle the user's current request normally.

## Token-efficient code navigation

When CodeGraph tools are available and the index is healthy:

- use one `codegraph_explore` call first for architecture, symbol, flow, and
  cross-file code-understanding questions;
- use callers/callees/impact for change-surface analysis, and symbol search for
  location-only questions;
- do not repeat the same exploration with broad `rg` or whole-file reads unless
  CodeGraph is incomplete, ambiguous, or reports pending synchronization;
- directly inspect newly edited or pending-sync files, and use normal commands
  for Markdown/Shell/SQL, Git/diffs, tests, runtime state, and security checks;
- fall back to `rg` and targeted reads when CodeGraph is unavailable. Never ask
  the user to initialize or launch it merely to continue the project.

CodeGraph is a navigation accelerator, not a source of runtime truth or test
evidence. Keep tool responses bounded and avoid printing large repeated output.

Direct user instructions in the current conversation take precedence over this
file and the continuity document.

The helper snapshot is only a bounded hint. All Codex sessions share the live
worktree, so re-read files, Git state, tests, and service/job status before later
decisions that depend on changing state. Do not add a background watcher to
simulate real-time context.

## Durable decision protocol

- The user owns product and workflow decisions.
- Treat entries marked **Accepted** in `docs/PROJECT_CONTINUITY.md` as the
  current direction unless the user explicitly changes them.
- User statements such as “保留这个”, “以后都这样”, or “记录下来” count as
  explicit approval to make that decision durable. Update the continuity
  document in the same change without asking the user which file to use.
- A technically promising new recommendation is not automatically accepted.
  Explain its evidence, benefit, cost, risk, and compatibility, then ask the
  user whether to adopt it.
- Add other new durable recommendations only after the user explicitly approves
  them. Record the approval date, status, rationale, implementation state, and
  verification evidence.
- Never silently replace an accepted decision. Mark the old entry superseded
  and link it to the replacement so later sessions can reconstruct why it
  changed.
- Do not alter or reinterpret an Accepted plan's scope, phase order,
  dependencies, safety boundaries, or exit criteria without the user's
  explicit approval. Progress/evidence updates may change only
  Accepted/Implemented/Verified/Pending status, not the accepted direction.
- Every estimate of remaining slices, time, or quantity must label its scope
  (whole project, phase, or sub-slice), included phases, and whether it is a
  rough estimate or a commitment. Keep whole-project and current-phase
  estimates visibly separate in bootstrap summaries and progress ledgers.
- Keep **Accepted direction**, **Implemented**, and **Verified** separate. A
  design can be accepted but still pending implementation.
- Update the continuity document in the same change when an approved decision,
  major workflow, safety boundary, or known limitation changes.
- Store concise, actionable decisions rather than raw chat transcripts. Never
  store API keys, `.env` contents, credentials, private model data, private
  prompts, or unnecessary personal information in continuity files.

## Progress and Git protocol

- For work under `D-003`, use the work-item and phase order in
  `docs/PROJECT_PROGRESS.md`. Do not skip an unmet dependency or describe a
  later phase as implemented.
- Update the progress ledger in the same change when a material work item starts,
  is verified, becomes blocked, or is handed to a later session. Include concrete
  files/tests and the next safe action; do not record transient PIDs or job text
  as durable truth.
- A phase is complete only when its required deliverables exist and its exit
  tests pass. `Implemented` is not the same as `Verified`.
- Follow accepted D-011: use elastic medium-sized slices. Merge adjacent work
  that shares one state machine, interface, risk boundary, and exit test; split
  only for a concrete safety, ownership, or verification reason and record it.
  Do not create a roadmap slice solely for one migration, field, receipt, or
  test, and do not treat the rough remaining-slice estimate as a quota.
- Multi-algorithm support means independently discoverable/selectable tools by
  default. Do not infer an automatic end-to-end algorithm pipeline unless the
  user explicitly selects a workflow. Use focused tests internally and full
  regression/representative CPU-CUDA E2E at phase exits without reducing
  required coverage.
- Follow `docs/GIT_AND_PROMPT_POLICY.md`: make bounded, reviewed checkpoints on
  the active feature branch; never push `main`, force-push, or rewrite published
  history without explicit user instruction.
- Until proposed decision `D-005` is explicitly accepted or rejected, keep new
  temporary prompts and raw chat out of Git, and do not migrate or delete
  existing product runtime prompts. Do not present the proposed runtime-prompt
  versioning policy as an accepted user decision.

## FWI workflow guardrail

Do not implement a watcher that treats files manually placed in `FWI_RUN_ROOT`
as executable jobs. That directory is controlled output/state. Follow the
accepted task-entry direction in `docs/PROJECT_CONTINUITY.md` and preserve the
fixed MCP whitelist and structured validation boundary.
