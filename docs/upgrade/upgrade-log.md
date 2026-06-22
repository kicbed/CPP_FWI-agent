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

## 2026-06-11: Add Dry-Run Experiment Specs

Scope:
- Added `ExperimentSpec`, `JobSpec`, and `DryRunBackend` to the research
  library.
- Added validation for missing algorithms, missing job command/working
  directory, invalid GPU counts, and invalid MPI process counts.
- Added dry-run rendering with an explicit `dry_run: true` marker.

Files changed:
- `research/CMakeLists.txt`
- `research/include/agent_rpc/research/experiment_spec.h`
- `research/include/agent_rpc/research/job_spec.h`
- `research/include/agent_rpc/research/job_backend.h`
- `research/src/experiment_spec.cpp`
- `research/src/job_spec.cpp`
- `research/src/dry_run_backend.cpp`
- `tests/test_experiment_spec.cpp`
- `tests/CMakeLists.txt`
- `docs/upgrade/milestones.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/upgrade-log.md`
- `docs/superpowers/plans/2026-06-11-lab-agent-v0.2.md`

Behavior changed:
- Research code can now validate experiment specs and render dry-run job
  previews.
- No command execution, real CUDA/MPI, SSH, Slurm, PBS, or remote execution was
  added.

Tests run:
- `cmake --build build -j2` before implementation, expected RED failure:
  missing `agent_rpc/research/experiment_spec.h`.
- `cmake --build build -j2`
- `ctest --test-dir build -R ExperimentSpec --output-on-failure`
- `ctest --test-dir build --output-on-failure`
- `git diff --check`

Result:
- PASS. RED build failed for the expected missing `ExperimentSpec` header.
- PASS. `cmake --build build -j2` exited 0 after implementation.
- PASS. `ExperimentSpecTest` passed 1/1.
- PASS. Full `ctest` passed 18/18 tests.
- PASS. `git diff --check` produced no output.

Commit:
- This dry-run experiment specs commit.

Next task:
- Add Experiment Planner Agent skeleton.

## 2026-06-11: Add Experiment Planner Agent Skeleton

Scope:
- Added `ai_experiment_planner_agent` executable.
- Registered the planner with `experiment`, `planning`, `research-computing`,
  and `fwi` tags.
- Wired the planner into local and deploy startup scripts on port `5011`.
- Added registration and executable-target tests.

Files changed:
- `README.md`
- `examples/ai_orchestrator/experiment_planner_agent_main.cpp`
- `examples/ai_orchestrator/CMakeLists.txt`
- `examples/ai_orchestrator/start_system.sh`
- `examples/ai_orchestrator/stop_system.sh`
- `deploy/scripts/start.sh`
- `tests/test_experiment_planner_registration.cpp`
- `tests/CMakeLists.txt`
- `docs/upgrade/milestones.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/upgrade-log.md`
- `docs/superpowers/plans/2026-06-11-lab-agent-v0.2.md`

Behavior changed:
- Startup scripts now launch an Experiment Planner Agent on local port `5011`.
- Planner prompts include local AlgorithmCard summaries and require dry-run-only
  JobSpec output when execution is requested.
- No real CUDA/MPI execution, SSH, Slurm, PBS, or remote execution was added.

Tests run:
- `cmake --build build -j2` before implementation, expected RED failure:
  missing `ai_experiment_planner_agent` target.
- `cmake --build build -j2`
- `ctest --test-dir build -R ExperimentPlanner --output-on-failure`
- `bash -n examples/ai_orchestrator/start_system.sh`
- `bash -n examples/ai_orchestrator/stop_system.sh`
- `bash -n deploy/scripts/start.sh`
- `ctest --test-dir build --output-on-failure`
- `git diff --check`

Result:
- PASS. RED build failed for the expected missing planner executable target.
- PASS. `cmake --build build -j2` exited 0 after implementation.
- PASS. Experiment Planner targeted tests passed 2/2.
- PASS. Startup and stop scripts passed shell syntax checks.
- PASS. Full `ctest` passed 20/20 tests.
- PASS. `git diff --check` produced no output.

Commit:
- This Experiment Planner Agent skeleton commit.

Next task:
- Add v0.2 demo script and final docs.

## 2026-06-11: Add v0.2 Demo Script And Test Report

Scope:
- Added a v0.2 demo script for FWI knowledge Q&A, Code Agent routing, and
  direct Experiment Planner Agent dry-run planning.
- Added a v0.2 test report with coverage summary, safety boundaries, and
  knowledge-point summary.
- Updated upgrade docs to mark the v0.2 Lab Agent MVP scope complete and point
  the next upgrade session to v0.3 Research Knowledge Base.

Files changed:
- `README.md`
- `docs/upgrade/README.md`
- `docs/upgrade/demo-script-v0.2.md`
- `docs/upgrade/test-report-v0.2.md`
- `docs/upgrade/version-roadmap.md`
- `docs/upgrade/milestones.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/upgrade-log.md`
- `docs/superpowers/plans/2026-06-11-lab-agent-v0.2.md`

Behavior changed:
- No runtime behavior changed.
- Demo docs now explicitly separate Orchestrator demos from direct
  Experiment Planner Agent smoke testing on `localhost:5011`.

Tests run:
- `cmake --build build -j2`
- `ctest --test-dir build --output-on-failure`
- `bash -n examples/ai_orchestrator/start_system.sh`
- `bash -n examples/ai_orchestrator/stop_system.sh`
- `bash -n deploy/scripts/start.sh`
- `git diff --check`

Result:
- PASS. `cmake --build build -j2` exited 0.
- PASS. Full `ctest` passed 20/20 tests.
- PASS. Startup and stop scripts passed shell syntax checks.
- PASS. `git diff --check` produced no output.

Commit:
- This v0.2 demo script and test report commit.

Next task:
- Start v0.3 Research Knowledge Base.

## 2026-06-11: Rewrite v0.2 Knowledge Summary In Chinese

Scope:
- Rewrote the v0.2 test report knowledge summary in Chinese with detailed
  learning and interview-prep notes.
- Added a standing upgrade-guide rule that future version completions, test
  reports, and major architecture changes should include detailed Chinese
  knowledge summaries.
- Updated the local ignored prompt file with the same knowledge-summary
  requirement for future copy-paste upgrade sessions.

Files changed:
- `docs/upgrade/test-report-v0.2.md`
- `docs/upgrade/README.md`
- `docs/upgrade/local-prompts.md` (ignored local prompt file)
- `docs/upgrade/upgrade-log.md`

Behavior changed:
- No runtime behavior changed.

Tests run:
- `git diff --check`

Result:
- PASS. `git diff --check` produced no output.

Commit:
- This Chinese knowledge summary rewrite commit.

Next task:
- Start v0.3 Research Knowledge Base.

## 2026-06-12: Start Research Knowledge Base

Scope:
- Started v0.3 with structured local research knowledge notes and a C++ loader.
- Added seed paper, algorithm, experiment, and failure-case notes for
  multi-scale FWI planning and cycle-skipping diagnosis.

Files changed:
- `research/include/agent_rpc/research/research_knowledge.h`
- `research/src/research_knowledge.cpp`
- `research/CMakeLists.txt`
- `resources/research_knowledge/papers/multiscale_fwi_practice.json`
- `resources/research_knowledge/algorithms/multiscale_fwi.json`
- `resources/research_knowledge/experiments/marmousi_multiscale_fwi_dry_run.json`
- `resources/research_knowledge/failure_cases/cycle_skipping_low_frequency.json`
- `tests/test_research_knowledge.cpp`
- `tests/CMakeLists.txt`
- `docs/upgrade/README.md`
- `docs/upgrade/milestones.md`
- `docs/upgrade/version-roadmap.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/upgrade-log.md`

Behavior changed:
- Local code can load typed research knowledge notes from
  `resources/research_knowledge`.
- Retrieval now supports note type, method, failure mode, and parameter-advice
  lookup for deterministic non-LLM planner grounding.
- No real CUDA/MPI execution, SSH, Slurm, PBS, or remote execution was added.

Tests run:
- `cmake --build build -j2` before implementation, expected RED failure:
  missing `agent_rpc/research/research_knowledge.h`.
- `cmake --build build -j2`
- `ctest --test-dir build -R ResearchKnowledge --output-on-failure`
- `ctest --test-dir build --output-on-failure`
- `git diff --check`

Result:
- PASS. RED build failed for the expected missing `ResearchKnowledge` header.
- PASS. Targeted `ResearchKnowledgeTest` passed 1/1 after implementation.
- PASS. `cmake --build build -j2` exited 0 after implementation.
- PASS. Full `ctest` passed 21/21 tests.
- PASS. `git diff --check` produced no output.

Commit:
- This Research Knowledge Base skeleton commit.

Next task:
- Add the remaining structured v0.3 notes for AWI, adjoint-state gradient, and
  broader parameter/failure guidance, then wire retrieval into the Experiment
  Planner path.

## 2026-06-12: Add AWI And Gradient Knowledge Notes

Scope:
- Added structured v0.3 knowledge notes for AWI and adjoint-state gradient
  sanity checks.
- Extended ResearchKnowledge tests to require AWI, adjoint-state-gradient,
  cycle-skipping, and finite-difference gradient-check retrieval coverage.

Files changed:
- `resources/research_knowledge/algorithms/awi.json`
- `resources/research_knowledge/papers/adjoint_state_gradient.json`
- `tests/test_research_knowledge.cpp`
- `docs/upgrade/milestones.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/upgrade-log.md`

Behavior changed:
- Local research knowledge retrieval now includes AWI and adjoint-state
  gradient guidance for planner grounding.
- No real CUDA/MPI execution, SSH, Slurm, PBS, or remote execution was added.

Tests run:
- `cmake --build build -j2`
- `ctest --test-dir build -R ResearchKnowledge --output-on-failure`
- `ctest --test-dir build --output-on-failure`
- `git diff --check`

Result:
- PASS. RED target test failed for the expected missing `algorithm.awi` note.
- PASS. Targeted `ResearchKnowledgeTest` passed 1/1 after adding the notes.
- PASS. `cmake --build build -j2` exited 0.
- PASS. Full `ctest` passed 21/21 tests.
- PASS. `git diff --check` produced no output.

Commit:
- This AWI and gradient knowledge notes commit.

Next task:
- Add dataset-based knowledge retrieval and tests for v0.3 roadmap completion.

## 2026-06-12: Complete v0.3 Research Knowledge Base

Scope:
- Added dataset-based retrieval to `ResearchKnowledgeBase`.
- Added v0.3 completion docs and a v0.3 test report with Chinese learning and
  interview-prep notes.
- Updated upgrade docs to move the next target to v0.4 Experiment Planner.

Files changed:
- `README.md`
- `research/include/agent_rpc/research/research_knowledge.h`
- `research/src/research_knowledge.cpp`
- `tests/test_research_knowledge.cpp`
- `docs/upgrade/README.md`
- `docs/upgrade/milestones.md`
- `docs/upgrade/version-roadmap.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/test-report-v0.3.md`
- `docs/upgrade/upgrade-log.md`

Behavior changed:
- Research knowledge retrieval now supports dataset lookup in addition to note
  type, method, failure mode, and parameter-advice lookup.
- v0.3 Research Knowledge Base is documented as complete.
- No real CUDA/MPI execution, SSH, Slurm, PBS, or remote execution was added.

Tests run:
- `cmake --build build -j2` before implementation, expected RED failure:
  undefined `ResearchKnowledgeBase::filter_by_dataset`.
- `cmake --build build -j2`
- `ctest --test-dir build -R ResearchKnowledge --output-on-failure`
- `ctest --test-dir build --output-on-failure`
- `git diff --check`

Result:
- PASS. RED build failed for the expected missing
  `ResearchKnowledgeBase::filter_by_dataset` implementation.
- PASS. Targeted `ResearchKnowledgeTest` passed 1/1 after implementation.
- PASS. `cmake --build build -j2` exited 0.
- PASS. Full `ctest` passed 21/21 tests.
- PASS. `git diff --check` produced no output.

Commit:
- This v0.3 Research Knowledge completion commit.

Next task:
- Start v0.4 Experiment Planner by wiring AlgorithmCard and
  ResearchKnowledgeBase retrieval into deterministic planner context.

## 2026-06-12: Start v0.4 PlannerContext Retrieval

Scope:
- Added deterministic PlannerContext retrieval for the Experiment Planner.
- Wired Planner Agent prompts to include request-specific AlgorithmCards,
  research knowledge notes, failure-mode evidence, parameter advice, and
  dry-run safety boundaries.

Files changed:
- `research/include/agent_rpc/research/planner_context.h`
- `research/src/planner_context.cpp`
- `research/CMakeLists.txt`
- `examples/ai_orchestrator/experiment_planner_agent_main.cpp`
- `tests/test_planner_context.cpp`
- `tests/CMakeLists.txt`
- `docs/upgrade/milestones.md`
- `docs/upgrade/version-roadmap.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/upgrade-log.md`

Behavior changed:
- Experiment Planner now builds a deterministic context from the user request
  before calling the LLM instead of sending only a static algorithm list.
- No real CUDA/MPI execution, SSH, Slurm, PBS, remote execution, arbitrary
  shell execution, or automatic code patch application was added.

Tests run:
- `cmake --build build -j2` before implementation, expected RED failure:
  missing `agent_rpc/research/planner_context.h`.
- `cmake --build build -j2`
- `ctest --test-dir build -R PlannerContext --output-on-failure`
- `ctest --test-dir build --output-on-failure`
- `git diff --check`

Result:
- PASS. RED build failed for the expected missing `PlannerContext` header.
- PASS. `cmake --build build -j2` exited 0 after implementation.
- PASS. Targeted `PlannerContextTest` passed 1/1 after implementation.
- PASS. Full `ctest` passed 22/22 tests.
- PASS. `git diff --check` produced no output.

Commit:
- This v0.4 PlannerContext retrieval commit.

Next task:
- Generate a structured Planner answer with algorithm recommendation,
  parameter table, assumption list, risk analysis, and next-step plan from the
  deterministic PlannerContext.

## 2026-06-12: Complete v0.4 Experiment Planner

Scope:
- Added deterministic PlannerAnswer generation for structured dry-run
  experiment planning.
- Completed v0.4 with algorithm recommendation, assumptions, parameter table,
  risk analysis, next-step plan, ExperimentSpec JSON, dry-run JobSpec text, and
  reproducible experiment record output.
- Added a v0.4 test report with Chinese learning and interview-prep summary.

Files changed:
- `README.md`
- `research/include/agent_rpc/research/planner_answer.h`
- `research/src/planner_answer.cpp`
- `research/CMakeLists.txt`
- `examples/ai_orchestrator/experiment_planner_agent_main.cpp`
- `tests/test_planner_answer.cpp`
- `tests/CMakeLists.txt`
- `docs/upgrade/README.md`
- `docs/upgrade/milestones.md`
- `docs/upgrade/version-roadmap.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/test-report-v0.4.md`
- `docs/upgrade/upgrade-log.md`

Behavior changed:
- Experiment Planner now has a deterministic structured output scaffold before
  LLM generation.
- Generated plans include dry-run ExperimentSpec, dry-run JobSpec, and a
  versioned experiment record.
- No real CUDA/MPI execution, SSH, Slurm, PBS, remote execution, arbitrary
  shell execution, or automatic Code Agent patch application was added.

Tests run:
- `cmake --build build -j2` before implementation, expected RED failure:
  missing `agent_rpc/research/planner_answer.h`.
- `cmake --build build -j2`
- `ctest --test-dir build -R "Planner(Context|Answer)" --output-on-failure`
- `ctest --test-dir build --output-on-failure`
- `git diff --check`

Result:
- PASS. RED build failed for the expected missing `PlannerAnswer` header.
- PASS. `cmake --build build -j2` exited 0 after implementation.
- PASS. Planner targeted tests passed 2/2.
- PASS. Full `ctest` passed 23/23 tests.
- PASS. `git diff --check` produced no output.

Commit:
- This v0.4 Experiment Planner completion commit.

Next task:
- Start v0.5 Lab Workbench UI with visible routing, tool calls,
  AlgorithmCards, parameter tables, ExperimentSpec, JobSpec, dry-run job, and
  service status panels.

## 2026-06-12: Expand v0.3 Learning Summary Prompt

Scope:
- Rewrote the v0.3 knowledge summary with more detailed learning and
  interview-prep notes.
- Strengthened the upgrade-guide summary requirements so future reports include
  problem context, implementation details, test coverage, safety boundaries,
  TDD evidence, interview Q&A, and STAR-style explanations.
- Updated the ignored local prompt file with the same detailed-summary
  requirement for future copy-paste upgrade sessions.

Files changed:
- `docs/upgrade/test-report-v0.3.md`
- `docs/upgrade/README.md`
- `docs/upgrade/local-prompts.md` (ignored local prompt file)
- `docs/upgrade/upgrade-log.md`

Behavior changed:
- No runtime behavior changed.

Tests run:
- `git diff --check`

Result:
- PASS. `git diff --check` produced no output.

Commit:
- This expanded v0.3 learning summary prompt commit.

Next task:
- Start v0.4 Experiment Planner by wiring AlgorithmCard and
  ResearchKnowledgeBase retrieval into deterministic planner context.

## 2026-06-12: Start v0.5 Lab Agent Workbench Branding

Scope:
- Started v0.5 Lab Workbench UI with a focused branding rename.
- Added a static Web branding CTest guard.

Files changed:
- `web/index.html`
- `web/serve.py`
- `tests/check_web_branding.cmake`
- `tests/CMakeLists.txt`
- `docs/upgrade/README.md`
- `docs/upgrade/milestones.md`
- `docs/upgrade/version-roadmap.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/upgrade-log.md`

Behavior changed:
- Browser title, sidebar brand, welcome state, footer, and local Web server
  startup banner now say Lab Agent Workbench instead of generic orchestrator
  chat branding.
- No real CUDA/MPI execution, SSH, Slurm, PBS, remote execution, arbitrary
  shell execution, or automatic Code Agent patch application was added.

Tests run:
- `cmake --build build -j2` before implementation, expected RED failure:
  `WebBrandingTest` missing `<title>Lab Agent Workbench</title>`.
- `cmake --build build -j2`
- `ctest --test-dir build -R WebBrandingTest --output-on-failure`
- `python3 web/serve.py 18080` plus `curl http://localhost:18080/`
- `ctest --test-dir build --output-on-failure`
- `git diff --check`

Result:
- PASS. RED `WebBrandingTest` failed for the expected missing Lab Agent
  Workbench title before implementation.
- PASS. `cmake --build build -j2` exited 0 after implementation.
- PASS. `WebBrandingTest` passed after the UI and server branding rename.
- PASS. `curl` against `http://localhost:18080/` found the new title,
  sidebar/welcome text, research-workbench copy, and footer branding. The
  container could not open a graphical browser through `xdg-open`, so the smoke
  check used curl.
- PASS. Full `ctest` passed 24/24 tests.
- PASS. `git diff --check` produced no output.

Commit:
- This Lab Agent Workbench branding commit.

Next task:
- Add the first Lab Workbench inspector panel for selected agent, tool calls,
  and generated specs.

## 2026-06-12: Complete v0.5 Lab Workbench UI

Scope:
- Completed v0.5 Lab Workbench UI as one tightly related frontend batch.
- Added left-side AlgorithmCard and experiment-history panels.
- Added right-side inspector for route trace, tool calls, selected
  AlgorithmCard, parameter table, ExperimentSpec, JobSpec, dry-run state, and
  service status.
- Added static parser/render helpers for ExperimentSpec JSON and dry-run
  JobSpec text blocks.
- Added v0.5 test report with detailed Chinese learning and interview-prep
  summary.

Files changed:
- `web/index.html`
- `tests/check_web_branding.cmake`
- `tests/CMakeLists.txt`
- `docs/upgrade/README.md`
- `docs/upgrade/milestones.md`
- `docs/upgrade/version-roadmap.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/test-report-v0.5.md`
- `docs/upgrade/upgrade-log.md`
- `docs/upgrade/screenshots/lab-workbench-v0.5-smoke.png`

Behavior changed:
- The browser UI now presents the system as a research workbench instead of a
  chat-only page.
- Demo viewers can inspect selected algorithms, route trace, read-only tool
  calls, parameter advice, dry-run ExperimentSpec, dry-run JobSpec, artifacts,
  and local service status from one screen.
- No real CUDA/MPI execution, SSH, Slurm, PBS, remote execution, arbitrary
  shell execution, or automatic Code Agent patch application was added.

Tests run:
- `cmake --build build -j2` before implementation, expected RED failure:
  `WebBrandingTest` missing `id="algorithmList"`.
- `cmake --build build -j2`
- `ctest --test-dir build -R WebBrandingTest --output-on-failure`
- `python3 web/serve.py 18080` plus `curl http://localhost:18080/`
- Playwright screenshot smoke for
  `docs/upgrade/screenshots/lab-workbench-v0.5-smoke.png`
- `ctest --test-dir build --output-on-failure`
- `git diff --check`

Result:
- PASS. RED `WebBrandingTest` failed for the expected missing
  `id="algorithmList"` before implementation.
- PASS. `cmake --build build -j2` exited 0 after implementation.
- PASS. Targeted `WebBrandingTest` passed after the workbench panels and v0.5
  report were added.
- PASS. `curl` against `http://localhost:18080/` found the Lab Agent
  Workbench title, algorithm panel, inspector, route trace, tool calls,
  ExperimentSpec, JobSpec, service status, dry-run marker, and render/parser
  helpers.
- PASS. Playwright generated
  `docs/upgrade/screenshots/lab-workbench-v0.5-smoke.png` as a 1440x1000 PNG.
- PASS. Full `ctest` passed 24/24 tests.
- PASS. `git diff --check` produced no output.

Commit:
- This v0.5 Lab Workbench UI completion commit.

Next task:
- Start v0.6 Lab Code Adapter for config templates, log parsing, loss curve
  parsing, and failure recognizers without submitting jobs.

## 2026-06-22: Start v0.6 Lab Code Adapter Plan

Scope:
- Created the v0.6 Lab Code Adapter implementation plan.
- Added Lab Code Adapter tasks to the milestone board.
- Marked v0.6 as the active planned target without claiming runtime adapter
  code exists.

Files changed:
- `docs/superpowers/plans/2026-06-22-lab-code-adapter-v0.6.md`
- `docs/upgrade/README.md`
- `docs/upgrade/milestones.md`
- `docs/upgrade/version-roadmap.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/upgrade-log.md`

Behavior changed:
- No runtime behavior changed.
- No real CUDA/MPI execution, SSH, Slurm, PBS, remote execution, arbitrary
  shell execution, or automatic Code Agent patch application was added.

Tests run:
- `git diff --check`
- `cmake --build build -j2`
- `ctest --test-dir build --output-on-failure`

Result:
- PASS. `git diff --check` produced no output.
- PASS. `cmake --build build -j2` exited 0.
- PASS. Full `ctest` passed 24/24 tests.

Commit:
- This v0.6 Lab Code Adapter plan commit.

Next task:
- Start v0.6 Task 1: add a failing `LabCodeAdapter` config-template reader
  test, then implement the minimal reader that rejects execution fields.

## 2026-06-22: Complete v0.6 Lab Code Adapter

Scope:
- Added deterministic Lab Code Adapter C++ models and parsing helpers.
- Added fixture-backed config template, log parsing, loss curve extraction,
  failure recognition, and Planner-facing summary tests.
- Added v0.6 test report with detailed Chinese learning and interview-prep
  summary.

Files changed:
- `research/include/agent_rpc/research/lab_code_adapter.h`
- `research/src/lab_code_adapter.cpp`
- `research/CMakeLists.txt`
- `resources/lab_code_adapter/config_templates/fwi_marmousi_multiscale.json`
- `resources/lab_code_adapter/logs/fwi_loss_stagnation.log`
- `resources/lab_code_adapter/logs/fwi_nan_instability.log`
- `tests/test_lab_code_adapter.cpp`
- `tests/CMakeLists.txt`
- `docs/superpowers/plans/2026-06-22-lab-code-adapter-v0.6.md`
- `docs/upgrade/README.md`
- `docs/upgrade/milestones.md`
- `docs/upgrade/version-roadmap.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/test-report-v0.6.md`
- `docs/upgrade/upgrade-log.md`

Behavior changed:
- The research library can now load lab config templates, reject execution
  fields, render dry-run config previews, parse supplied log text into loss
  curves and diagnostic lines, recognize common FWI failure signals, and build
  Planner-facing diagnostic summaries.
- No real CUDA/MPI execution, SSH, Slurm, PBS, remote execution, arbitrary
  shell execution, or automatic Code Agent patch application was added.

Tests run:
- `cmake --build build -j2` before Task 1 implementation, expected RED failure:
  missing `agent_rpc/research/lab_code_adapter.h`.
- `cmake --build build -j2` before Task 2 implementation, expected RED failure:
  missing `render_config_preview`.
- `cmake --build build -j2` before Task 3 implementation, expected RED failure:
  missing `parse_lab_log`.
- `cmake --build build -j2` before Task 4 implementation, expected RED failure:
  missing `FailureFinding` and `recognize_failure_modes`.
- `cmake --build build -j2` before Task 5 implementation, expected RED failure:
  missing `build_planner_diagnostic_summary`.
- `./build/tests/test_lab_code_adapter --gtest_filter=LabCodeAdapterTest.RejectsAbsoluteConfigTemplatePaths --gtest_break_on_failure`,
  expected RED failure: absolute config template paths were not rejected yet.
- `cmake --build build -j2`
- `ctest --test-dir build -R LabCodeAdapter --output-on-failure`
- `ctest --test-dir build --output-on-failure`
- `git diff --check`

Result:
- PASS. The expected RED failures occurred before each new API was implemented.
- PASS. The path safety RED test failed before absolute-path rejection was
  added, then passed after implementation.
- PASS. `cmake --build build -j2` exited 0 after implementation.
- PASS. `LabCodeAdapterTest` passed 1/1.
- PASS. Full `ctest` passed 25/25 tests.
- PASS. `git diff --check` produced no output.

Commit:
- This v0.6 Lab Code Adapter completion commit.

Next task:
- Start JobBackend interface reservation: define the future backend interface,
  make DryRunBackend implement it, and keep all non-`dry_run` backend choices
  rejected until the server execution milestone has a safety design.

## 2026-06-22: Reserve JobBackend Interface

Scope:
- Started the JobBackend interface reservation milestone with a small
  interface-focused batch.
- Added the abstract backend contract and made the existing dry-run backend
  implement it.

Files changed:
- `research/include/agent_rpc/research/job_backend.h`
- `tests/test_experiment_spec.cpp`
- `docs/upgrade/milestones.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/upgrade-log.md`

Behavior changed:
- Research code can now call `DryRunBackend` through a `JobBackend` interface
  with the same validate/render/explain behavior.
- No real CUDA/MPI execution, SSH, Slurm/PBS, remote execution, arbitrary shell
  execution, or automatic Code Agent patch application was added.

Tests run:
- `cmake --build build -j2` before implementation, expected RED failure:
  missing `JobBackend` type.
- `cmake --build build -j2`
- `ctest --test-dir build -R ExperimentSpecTest --output-on-failure`
- `ctest --test-dir build --output-on-failure`
- `git diff --check`

Result:
- PASS. The expected RED build failed because `JobBackend` did not exist yet.
- PASS. `cmake --build build -j2` exited 0 after implementation.
- PASS. `ExperimentSpecTest` passed after `DryRunBackend` implemented
  `JobBackend`.
- PASS. Full `ctest` passed 25/25 tests.
- PASS. `git diff --check` produced no output.

Commit:
- This JobBackend interface reservation commit.

Next task:
- Add backend type enum values for `dry_run`, `local`, `ssh`, `slurm`, and
  `pbs`, then reject non-`dry_run` choices at runtime with clear messages.

## 2026-06-22: Complete v0.7 JobBackend Reservation

Scope:
- Completed the JobBackend interface reservation batch.
- Added explicit backend type values and runtime rejection for non-`dry_run`
  backends.
- Added v0.7 documentation and a detailed Chinese learning summary.

Files changed:
- `research/include/agent_rpc/research/job_backend.h`
- `research/src/job_backend.cpp`
- `research/src/dry_run_backend.cpp`
- `research/src/algorithm_card.cpp`
- `research/CMakeLists.txt`
- `tests/test_experiment_spec.cpp`
- `tests/test_algorithm_card.cpp`
- `tests/test_algorithm_registry.cpp`
- `docs/upgrade/README.md`
- `docs/upgrade/milestones.md`
- `docs/upgrade/version-roadmap.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/test-report-v0.7.md`
- `docs/upgrade/upgrade-log.md`

Behavior changed:
- Research code now has `JobBackendType` values for `dry_run`, `local`, `ssh`,
  `slurm`, `pbs`, and `unknown`.
- Shared backend parsing and validation accepts only `dry_run`; reserved or
  unknown backends are rejected with clear messages.
- `AlgorithmCard` backend validation now uses the shared backend guard.
- No real CUDA/MPI execution, SSH, Slurm/PBS, remote execution, arbitrary shell
  execution, or automatic Code Agent patch application was added.

Tests run:
- `cmake --build build -j2` before implementation, expected RED failure:
  missing `JobBackendType`, `parse_job_backend_type`, and
  `validate_backend_enabled`.
- `cmake --build build -j2`
- `ctest --test-dir build -R "(ExperimentSpecTest|AlgorithmCardTest)" --output-on-failure`
- `cmake --build build -j2 && ctest --test-dir build --output-on-failure`
  initially exposed a stale `AlgorithmRegistryTest` assertion for the old v0.2
  backend error message.
- `cmake --build build -j2 && ctest --test-dir build -R AlgorithmRegistryTest --output-on-failure`
- `ctest --test-dir build --output-on-failure`
- `git diff --check`

Result:
- PASS. The expected RED build failed before the new backend enum and guard
  APIs existed.
- PASS. `cmake --build build -j2` exited 0 after implementation.
- PASS. Targeted `AlgorithmCardTest` and `ExperimentSpecTest` passed 2/2.
- PASS. The stale `AlgorithmRegistryTest` assertion was migrated to the shared
  guard message and then passed.
- PASS. Full `ctest` passed 25/25 tests.
- PASS. `git diff --check` produced no output.

Commit:
- This v0.7 JobBackend reservation completion commit.

Next task:
- Start v0.8 only after writing a server-backend safety design for auth,
  workspace isolation, approved templates, job lifecycle, artifact collection,
  and audit logging.

## 2026-06-22: Start v0.8 Server Backend Safety Design

Scope:
- Started v0.8 with a server-backend safety design and implementation plan.
- Reframed the next milestone around safety models before any real execution
  adapter is connected.

Files changed:
- `docs/upgrade/server-backend-safety-v0.8.md`
- `docs/superpowers/plans/2026-06-22-server-backend-v0.8.md`
- `docs/upgrade/README.md`
- `docs/upgrade/milestones.md`
- `docs/upgrade/version-roadmap.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/upgrade-log.md`

Behavior changed:
- No runtime behavior changed.
- No real CUDA/MPI execution, SSH, Slurm, PBS, remote execution, local wrapper
  execution, arbitrary shell execution, or automatic Code Agent patch
  application was added.
- Runtime is still expected to reject all non-`dry_run` backend values.

Tests run:
- `git diff --check`
- `cmake --build build -j2`
- `ctest --test-dir build --output-on-failure`

Result:
- PASS. `git diff --check` produced no output.
- PASS. `cmake --build build -j2` exited 0.
- PASS. Full `ctest` passed 25/25 tests.

Commit:
- This v0.8 server backend safety design commit.

Next task:
- Continue v0.8 Task 2: add server job submission and lifecycle record models,
  with tests proving non-`dry_run` submissions remain rejected.

## 2026-06-22: Add Server Job Safety Model

Scope:
- Added the first v0.8 server job model contract.
- Added lifecycle state parsing and a submission-boundary validator that keeps
  non-`dry_run` backends rejected.

Files changed:
- `research/include/agent_rpc/research/server_job.h`
- `research/src/server_job.cpp`
- `research/CMakeLists.txt`
- `tests/test_server_job.cpp`
- `tests/CMakeLists.txt`
- `docs/superpowers/plans/2026-06-22-server-backend-v0.8.md`
- `docs/upgrade/upgrade-log.md`

Behavior changed:
- Research code now has `JobSubmissionRequest`, `JobRecord`, and
  `JobLifecycleState` models for future server job tracking.
- `validate_submission_boundary` reuses the existing backend guard, so
  reserved backend values such as `slurm` remain rejected.
- No real CUDA/MPI execution, SSH, Slurm, PBS, remote execution, local wrapper
  execution, arbitrary shell execution, or automatic Code Agent patch
  application was added.

Tests run:
- `cmake --build build -j2` before implementation, expected RED failure:
  missing `agent_rpc/research/server_job.h`.
- `cmake --build build -j2`
- `ctest --test-dir build -R ServerJobTest --output-on-failure`
- `ctest --test-dir build --output-on-failure`
- `git diff --check`

Result:
- PASS. The expected RED build failed before `server_job.h` existed.
- PASS. `cmake --build build -j2` exited 0 after implementation.
- PASS. `ServerJobTest` passed 1/1.
- PASS. Full `ctest` passed 26/26 tests.
- PASS. `git diff --check` produced no output.

Commit:
- This server job safety model commit.

Next task:
- Continue v0.8 Task 3: add approved job template validation.

## 2026-06-22: Validate Approved Job Templates

Scope:
- Added approved job template data and validation for the v0.8 server backend
  safety layer.

Files changed:
- `research/include/agent_rpc/research/server_job.h`
- `research/src/server_job.cpp`
- `tests/test_server_job.cpp`
- `docs/superpowers/plans/2026-06-22-server-backend-v0.8.md`
- `docs/upgrade/upgrade-log.md`

Behavior changed:
- Future job submission requests can now be checked against a versioned
  approved template list.
- Unknown templates are rejected, and matching templates validate backend type
  and optional template version.
- The validation remains metadata-only and does not submit jobs or render shell
  commands.
- No real CUDA/MPI execution, SSH, Slurm, PBS, remote execution, local wrapper
  execution, arbitrary shell execution, or automatic Code Agent patch
  application was added.

Tests run:
- `cmake --build build -j2` before implementation, expected RED failure:
  missing `ApprovedJobTemplate` and `validate_approved_template`.
- `cmake --build build -j2`
- `ctest --test-dir build -R ServerJobTest --output-on-failure`
- `ctest --test-dir build --output-on-failure`
- `git diff --check`

Result:
- PASS. The expected RED build failed before approved-template APIs existed.
- PASS. `cmake --build build -j2` exited 0 after implementation.
- PASS. `ServerJobTest` passed.
- PASS. Full `ctest` passed 26/26 tests.
- PASS. `git diff --check` produced no output.

Commit:
- This approved job template validation commit.

Next task:
- Continue v0.8 Task 4: add workspace path isolation and traversal rejection.
