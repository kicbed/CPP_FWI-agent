# v0.2 Test Report

Date: 2026-06-11

Scope:

- Code Agent MVP.
- AlgorithmCard model, AlgorithmRegistry, and seed cards.
- Local read-only algorithm listing helper.
- ExperimentSpec, JobSpec, and DryRunBackend.
- Experiment Planner Agent skeleton.
- v0.2 demo and upgrade documentation.

Safety boundaries verified by implementation and docs:

- No real CUDA/MPI execution is enabled.
- No SSH, Slurm, PBS, or remote-server backend is connected.
- No arbitrary shell command execution is wired from user input.
- Code Agent is read-only and only proposes patches.
- DryRunBackend renders previews and includes `dry_run: true`; it does not
  submit jobs.

## Commands

Final verification commands:

```bash
cmake --build build -j2
ctest --test-dir build --output-on-failure
bash -n examples/ai_orchestrator/start_system.sh
bash -n examples/ai_orchestrator/stop_system.sh
bash -n deploy/scripts/start.sh
git diff --check
```

Results will be recorded in `docs/upgrade/upgrade-log.md` for the final v0.2
documentation commit.

Final result:

- PASS. `cmake --build build -j2` exited 0.
- PASS. `ctest --test-dir build --output-on-failure` passed 20/20 tests.
- PASS. `bash -n examples/ai_orchestrator/start_system.sh` exited 0.
- PASS. `bash -n examples/ai_orchestrator/stop_system.sh` exited 0.
- PASS. `bash -n deploy/scripts/start.sh` exited 0.
- PASS. `git diff --check` produced no output before the final result log was
  written; it is rerun before commit.

## Coverage Summary

- `CodeAgentRegistrationTest`: verifies Code Agent tag, skills, tool-calling
  capability, and AgentCard serialization contract.
- `CodeAgentToolsTest`: verifies read-only list/read/search helpers reject
  unsafe paths and do not execute shell commands.
- `AlgorithmCardTest`: verifies JSON parsing, required fields, and rejection of
  non-`dry_run` backends.
- `AlgorithmRegistryTest`: verifies seed-card loading, filtering, invalid-card
  rejection, and the read-only listing helper shape.
- `ExperimentSpecTest` and `DryRunBackendTest`: verify spec validation and
  dry-run rendering.
- `ExperimentPlannerRegistrationTest` and `ExperimentPlannerExecutableTargetTest`:
  verify planner registration metadata and build target availability.

## Knowledge Summary

- Agent routing should be treated as a contract: tags, skills, capabilities,
  and AgentCard serialization are tested before relying on runtime routing.
- Research-domain concepts are now modeled as data. AlgorithmCards make FWI,
  frequency extrapolation, and post-stack inversion extensible without editing
  Orchestrator logic.
- Planning and execution are separated. ExperimentSpec and JobSpec describe the
  intended work, while DryRunBackend renders a preview and keeps execution out
  of v0.2.
- Code Agent safety depends on narrow read-only tools: list, read, and search
  are useful enough for repository Q&A while avoiding arbitrary shell access.
- The Experiment Planner Agent is intentionally a skeleton in v0.2. It can be
  smoke-tested directly through its A2A endpoint and will need structured
  knowledge retrieval and deterministic plan shaping in later versions.
- Verification discipline matters for this repo: every upgrade step records the
  build, focused CTest where relevant, full CTest, shell syntax checks for
  scripts, and `git diff --check`.
