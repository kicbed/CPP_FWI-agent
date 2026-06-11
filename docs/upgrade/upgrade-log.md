# Upgrade Log

Record every upgrade session here. Keep entries short and factual.

## Entry Format

```markdown
## YYYY-MM-DD: Short Title

Scope:
- Files changed:
- Behavior changed:
- Tests run:
- Result:
- Commit:
- Next task:
```

## 2026-06-11: Add Upgrade Operating Plan

Scope:
- Created the upgrade operating manual, milestone board, and v0.2 implementation plan.

Files changed:
- `docs/upgrade/README.md`
- `docs/upgrade/milestones.md`
- `docs/upgrade/upgrade-log.md`
- `docs/superpowers/plans/2026-06-11-lab-agent-v0.2.md`

Behavior changed:
- No runtime behavior changed.

Tests run:
- `git diff --check`
- `ctest --test-dir build --output-on-failure`

Result:
- PASS. `git diff --check` produced no output. `ctest` passed 12/12 tests.

Commit:
- `50ec4eb`

Next task:
- Start Milestone 0 or Milestone 2 from `docs/superpowers/plans/2026-06-11-lab-agent-v0.2.md`.

## 2026-06-11: Keep Copy-Paste Upgrade Prompts Local

Scope:
- Removed committed new-session prompt file from project docs.
- Added ignored local prompt paths so personal upgrade prompts stay out of git.

Files changed:
- `.gitignore`
- `docs/upgrade/README.md`
- `docs/upgrade/new-session-prompts.md`
- `docs/upgrade/upgrade-log.md`

Behavior changed:
- No runtime behavior changed.

Tests run:
- `git diff --check`

Result:
- PASS. `git diff --check` produced no output.

Commit:
- This cleanup commit.

Next task:
- Continue with v0.2 implementation, starting from Code Agent MVP.

## 2026-06-11: Add Version Roadmap

Scope:
- Added a committed version roadmap from v0.2 through v1.0.
- Created an ignored local prompt file at `docs/upgrade/local-prompts.md` for copy-paste session prompts.

Files changed:
- `docs/upgrade/README.md`
- `docs/upgrade/version-roadmap.md`
- `docs/upgrade/upgrade-log.md`

Behavior changed:
- No runtime behavior changed.

Tests run:
- `git diff --check`
- `git status --short --ignored docs/upgrade/local-prompts.md docs/upgrade/version-roadmap.md docs/upgrade/README.md docs/upgrade/upgrade-log.md`

Result:
- PASS. `git diff --check` produced no output. Local prompt file is ignored by git.

Commit:
- This version roadmap commit.

Next task:
- Continue v0.2 Code Agent MVP.

## 2026-06-11: Add Career Notes Requirement

Scope:
- Added career notes for architecture, technical highlights, resume bullets, and
  interview talking points.
- Updated the upgrade workflow so meaningful architecture or technical changes
  also update career notes.

Files changed:
- `docs/upgrade/README.md`
- `docs/upgrade/version-roadmap.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/upgrade-log.md`

Behavior changed:
- No runtime behavior changed.

Tests run:
- `git diff --check`
- `git status --short --ignored docs/upgrade/local-prompts.md`

Result:
- PASS. `git diff --check` produced no output. Local prompt file remains ignored by git.

Commit:
- This career notes commit.

Next task:
- Start v0.2 Code Agent MVP in a new conversation.

## 2026-06-11: Baseline README Positioning

Scope:
- Updated README first-screen positioning for the Lab Research Agent Platform.
- Recorded completed Milestone 0 baseline positioning items and career notes.

Files changed:
- `README.md`
- `docs/upgrade/milestones.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/upgrade-log.md`
- `docs/superpowers/plans/2026-06-11-lab-agent-v0.2.md`

Behavior changed:
- No runtime behavior changed.

Tests run:
- `ctest --test-dir build --output-on-failure`
- `cmake --build build -j2`
- `git diff --check`

Result:
- PASS. `ctest` passed 12/12 tests before and after the docs update.
- PASS. `cmake --build build -j2` exited 0.
- PASS. `git diff --check` produced no output.

Commit:
- This baseline README positioning commit.

Next task:
- Add the Code Agent registration contract test.

## 2026-06-11: CodeGraph Setup And Code Agent Registration Test

Scope:
- Installed CodeGraph CLI globally and enabled the CodeGraph MCP server for
  Codex global configuration on this machine.
- Initialized the current repository's local `.codegraph/` index and ignored
  that generated index directory.
- Added a Code Agent registration contract test for the planned v0.2 Code
  Agent.

Files changed:
- `.gitignore`
- `tests/test_code_agent_registration.cpp`
- `tests/CMakeLists.txt`
- `docs/upgrade/milestones.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/upgrade-log.md`
- `docs/superpowers/plans/2026-06-11-lab-agent-v0.2.md`

Behavior changed:
- No runtime agent behavior changed.
- Local developer tooling changed: CodeGraph is installed globally and the
  repository has a local ignored CodeGraph index.

Tests run:
- `npm view @colbymchenry/codegraph version`
- `npm install -g @colbymchenry/codegraph@0.9.9`
- `codegraph install --target=codex --location=global --yes`
- `codegraph init -i`
- `codegraph sync`
- `codegraph status`
- `cmake --build build -j2`
- `ctest --test-dir build -R CodeAgentRegistrationTest --output-on-failure`
- `ctest --test-dir build --output-on-failure`
- `git diff --check`

Result:
- PASS. CodeGraph CLI version `0.9.9` installed globally; `codegraph status`
  reports an up-to-date index with 178 files, 7,528 nodes, and 16,692 edges.
- PASS. `cmake --build build -j2` exited 0.
- PASS. `CodeAgentRegistrationTest` passed 1/1.
- PASS. Full `ctest` passed after the new test was added.
- PASS. `git diff --check` produced no output.

Commit:
- This CodeGraph setup and Code Agent registration test commit.

Next task:
- Add the read-only Code Agent executable.
