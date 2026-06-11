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

## 2026-06-11: Add Read-Only Code Agent Executable

Scope:
- Added the `ai_code_agent` executable with Code Agent registration metadata,
  code explanation, error diagnosis, and patch proposal prompt behavior.
- Added startup entries for Code Agent in local and deploy start scripts.
- Added a CTest contract that verifies the Code Agent executable target exists
  and can enter its usage path.

Files changed:
- `examples/ai_orchestrator/code_agent_main.cpp`
- `examples/ai_orchestrator/CMakeLists.txt`
- `examples/ai_orchestrator/start_system.sh`
- `deploy/scripts/start.sh`
- `tests/check_executable.cmake`
- `tests/CMakeLists.txt`
- `docs/upgrade/milestones.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/upgrade-log.md`
- `docs/superpowers/plans/2026-06-11-lab-agent-v0.2.md`

Behavior changed:
- Code Agent can now be built and started locally on port `5010`.
- Startup scripts launch Code Agent before Orchestrator and record
  `code_agent.pid`.
- Deploy startup now passes the resolved `$API_KEY` consistently instead of
  `$QWEN_API_KEY` directly.

Tests run:
- `cmake --build build -j2` before implementation, expected RED failure:
  missing `ai_code_agent` target.
- `cmake --build build -j2`
- `ctest --test-dir build -R "CodeAgent.*Test" --output-on-failure`
- `bash -n examples/ai_orchestrator/start_system.sh`
- `bash -n deploy/scripts/start.sh`
- `ctest --test-dir build --output-on-failure`
- `git diff --check`

Result:
- PASS. RED build failed for the expected missing `ai_code_agent` target.
- PASS. `cmake --build build -j2` exited 0 after implementation.
- PASS. Code Agent targeted tests passed 2/2.
- PASS. Full `ctest` passed after the new executable target was added.
- NOTE. Full `ctest` initially failed inside the restricted sandbox because the
  gRPC integration test could not create a local socket; the same command
  passed when rerun with approved non-sandbox execution.
- PASS. Both start scripts passed `bash -n`.
- PASS. `git diff --check` produced no output.

Commit:
- This read-only Code Agent executable commit.

Next task:
- Add read-only Code Agent project inspection functions: list files, read file,
  and search text.

## 2026-06-11: Add Code Agent Read-Only Inspection Tools

Scope:
- Added a `ai_code_agent_tools` C++ helper library for read-only file listing,
  safe file reading, and text search inside the configured project root.
- Wired Code Agent startup to pass `--project-root` and include deterministic
  project inspection context in the LLM system prompt.
- Added unit tests for safe relative paths, path escape rejection, and sorted
  search matches.

Files changed:
- `examples/ai_orchestrator/code_agent_tools.hpp`
- `examples/ai_orchestrator/code_agent_tools.cpp`
- `examples/ai_orchestrator/code_agent_main.cpp`
- `examples/ai_orchestrator/CMakeLists.txt`
- `examples/ai_orchestrator/start_system.sh`
- `deploy/scripts/start.sh`
- `tests/test_code_agent_tools.cpp`
- `tests/CMakeLists.txt`
- `docs/upgrade/milestones.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/upgrade-log.md`
- `docs/superpowers/plans/2026-06-11-lab-agent-v0.2.md`

Behavior changed:
- Code Agent now has deterministic, read-only list/read/search project
  inspection helpers.
- Code Agent prompt context includes project file paths and search hints derived
  from the user query.
- Absolute paths and `../` path escapes are rejected; no shell commands are
  executed.

Tests run:
- `cmake --build build -j2` before implementation, expected RED failure:
  missing `code_agent_tools.hpp`.
- `cmake --build build -j2`
- `ctest --test-dir build -R CodeAgentToolsTest --output-on-failure`
- `bash -n examples/ai_orchestrator/start_system.sh`
- `bash -n deploy/scripts/start.sh`
- `ctest --test-dir build --output-on-failure`
- `git diff --check`

Result:
- PASS. RED build failed for the expected missing `code_agent_tools.hpp`.
- PASS. `cmake --build build -j2` exited 0 after implementation.
- PASS. `CodeAgentToolsTest` passed.
- PASS. Full `ctest` passed after the new helper library was added.
- PASS. Both start scripts passed `bash -n`.
- PASS. `git diff --check` produced no output.

Commit:
- This Code Agent read-only inspection tools commit.

Next task:
- Add a smoke-test command to docs for asking where Orchestrator routing lives.

## 2026-06-11: Add Code Agent Smoke-Test Docs

Scope:
- Added a README smoke-test command for verifying that a code-routing question
  reaches the Code Agent.
- Marked the final Code Agent MVP documentation task complete.

Files changed:
- `README.md`
- `docs/upgrade/milestones.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/upgrade-log.md`
- `docs/superpowers/plans/2026-06-11-lab-agent-v0.2.md`

Behavior changed:
- No runtime behavior changed.

Tests run:
- `cmake --build build -j2`
- `ctest --test-dir build --output-on-failure`
- `git diff --check`

Result:
- PASS. `cmake --build build -j2` exited 0.
- PASS. Full `ctest` passed 15/15 tests.
- PASS. `git diff --check` produced no output.

Commit:
- This Code Agent smoke-test docs commit.

Next task:
- Backfill Milestone 0 quick demo commands, then continue Task 3 Research Library Skeleton.

## 2026-06-11: Add Quick Demo Commands

Scope:
- Added README quick demo commands for HTTP terminal, gRPC bridge, Web UI, and
  local embedding paths.
- Marked the remaining Milestone 0 quick demo docs task complete.

Files changed:
- `README.md`
- `docs/upgrade/milestones.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/upgrade-log.md`
- `docs/superpowers/plans/2026-06-11-lab-agent-v0.2.md`

Behavior changed:
- No runtime behavior changed.

Tests run:
- `cmake --build build -j2`
- `ctest --test-dir build --output-on-failure`
- `git diff --check`

Result:
- PASS. `cmake --build build -j2` exited 0.
- PASS. Full `ctest` passed 15/15 tests.
- PASS. `git diff --check` produced no output.

Commit:
- This quick demo commands commit.

Next task:
- Continue Task 3 Research Library Skeleton.

## 2026-06-11: Add Research AlgorithmCard Model

Scope:
- Added the initial `agent_rpc_research` C++ library.
- Added an `AlgorithmCard` model with JSON serialization, parsing, and
  validation for required fields and dry-run-only backend safety.
- Added focused GoogleTest coverage for JSON round-tripping and validation.

Files changed:
- `CMakeLists.txt`
- `research/CMakeLists.txt`
- `research/include/agent_rpc/research/algorithm_card.h`
- `research/src/algorithm_card.cpp`
- `tests/test_algorithm_card.cpp`
- `tests/CMakeLists.txt`
- `docs/upgrade/milestones.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/upgrade-log.md`
- `docs/superpowers/plans/2026-06-11-lab-agent-v0.2.md`

Behavior changed:
- New `agent_rpc_research` static library exposes `AlgorithmCard`.
- No runtime agent behavior changed and no job execution was added.

Tests run:
- `cmake --build build -j2` before implementation, expected RED failure:
  missing `agent_rpc/research/algorithm_card.h`.
- `cmake --build build -j2`
- `ctest --test-dir build -R AlgorithmCardTest --output-on-failure`
- `ctest --test-dir build --output-on-failure`
- `git diff --check`

Result:
- PASS. RED build failed for the expected missing `AlgorithmCard` header.
- PASS. `cmake --build build -j2` exited 0 after implementation.
- PASS. `AlgorithmCardTest` passed 1/1.
- PASS. Full `ctest` passed 16/16 tests.
- PASS. `git diff --check` produced no output.

Commit:
- This research AlgorithmCard model commit.

Next task:
- Add AlgorithmRegistry loading for `resources/algorithms/*.json`.

## 2026-06-11: Add AlgorithmRegistry And Seed Cards

Scope:
- Added `AlgorithmRegistry` for loading AlgorithmCards from
  `resources/algorithms/*.json`.
- Added seed cards for CUDA-MPI FWI metadata, frequency extrapolation, and
  post-stack inversion, all constrained to `dry_run`.
- Added tests for seed loading, ID lookup, domain/tag filtering, and invalid
  backend rejection.

Files changed:
- `research/CMakeLists.txt`
- `research/include/agent_rpc/research/algorithm_registry.h`
- `research/src/algorithm_registry.cpp`
- `resources/algorithms/fwi_cuda_mpi.json`
- `resources/algorithms/frequency_extrapolation.json`
- `resources/algorithms/poststack_inversion.json`
- `tests/test_algorithm_registry.cpp`
- `tests/CMakeLists.txt`
- `docs/upgrade/milestones.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/upgrade-log.md`
- `docs/superpowers/plans/2026-06-11-lab-agent-v0.2.md`

Behavior changed:
- Algorithm metadata can now be extended by adding JSON files under
  `resources/algorithms`.
- No job execution backend was added; seed cards use `dry_run` only.

Tests run:
- `cmake --build build -j2` before implementation, expected RED failure:
  missing `agent_rpc/research/algorithm_registry.h`.
- `cmake --build build -j2`
- `ctest --test-dir build -R AlgorithmRegistryTest --output-on-failure`
- `ctest --test-dir build --output-on-failure`
- `git diff --check`

Result:
- PASS. RED build failed for the expected missing `AlgorithmRegistry` header.
- PASS. `cmake --build build -j2` exited 0 after implementation.
- PASS. `AlgorithmRegistryTest` passed 1/1.
- PASS. Full `ctest` passed 17/17 tests.
- PASS. `git diff --check` produced no output.

Commit:
- This AlgorithmRegistry and seed cards commit.

Next task:
- Add a local listing entry for AlgorithmRegistry contents.

## 2026-06-11: Add Algorithm Listing Tool Entry

Scope:
- Added a local read-only listing helper for AlgorithmRegistry contents.
- Extended registry tests to verify the tool-facing JSON summary shape.
- Marked Milestone 3 complete.

Files changed:
- `research/CMakeLists.txt`
- `research/include/agent_rpc/research/algorithm_listing_tool.h`
- `research/src/algorithm_listing_tool.cpp`
- `tests/test_algorithm_registry.cpp`
- `docs/upgrade/milestones.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/upgrade-log.md`
- `docs/superpowers/plans/2026-06-11-lab-agent-v0.2.md`

Behavior changed:
- Local code can expose AlgorithmRegistry contents through a stable JSON
  summary.
- No runtime agent behavior changed and no job execution was added.

Tests run:
- `cmake --build build -j2` before implementation, expected RED failure:
  missing `agent_rpc/research/algorithm_listing_tool.h`.
- `cmake --build build -j2`
- `ctest --test-dir build -R AlgorithmRegistryTest --output-on-failure`
- `ctest --test-dir build --output-on-failure`
- `git diff --check`

Result:
- PASS. RED build failed for the expected missing listing helper header.
- PASS. `cmake --build build -j2` exited 0 after implementation.
- PASS. `AlgorithmRegistryTest` passed 1/1.
- PASS. Full `ctest` passed 17/17 tests.
- PASS. `git diff --check` produced no output.

Commit:
- This algorithm listing tool entry commit.

Next task:
- Add ExperimentSpec, JobSpec, and DryRunBackend.
