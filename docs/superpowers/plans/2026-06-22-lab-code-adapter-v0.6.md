# Lab Code Adapter v0.6 Implementation Plan

> **For agentic workers:** implement this plan task-by-task. Each task should
> start with the smallest failing test that proves the intended behavior, then
> add only enough code to pass it.

**Goal:** Add a safe Lab Code Adapter layer that understands lab-style FWI
configuration templates, generated configs, training logs, loss curves, and
common failure signals without submitting jobs or touching real CUDA/MPI
execution.

**Architecture:** Keep the adapter as deterministic C++ model/parsing code
under `research/`, backed by small sample resources and GoogleTest coverage.
The adapter should produce structured summaries that the Experiment Planner and
Workbench can later display, but v0.6 must not add SSH, Slurm, PBS, remote
execution, arbitrary shell execution, or automatic Code Agent patch
application.

**Tech Stack:** C++17/C++20 as required by existing targets, CMake, GoogleTest,
nlohmann/json, static resource fixtures, and the existing `agent_rpc_research`
library.

---

## Scope

In scope:

- Lab config template metadata and placeholder validation.
- Safe config generation from an `ExperimentSpec`-like parameter map.
- Parser for local text log content supplied as data, not as a command.
- Loss curve extraction from logs or fixture text.
- Common failure recognizers for cycle skipping, non-decreasing loss, NaN/Inf
  instability, missing low-frequency content, and resource-limit symptoms.
- Parameter tuning suggestions grounded in parsed evidence and existing
  research knowledge categories.
- Tests and docs that preserve dry-run and read-only boundaries.

Out of scope:

- Running CUDA/MPI binaries.
- Connecting to SSH, Slurm, PBS, or remote lab servers.
- Reading arbitrary host paths from user input.
- Executing shell commands from config, log, or user text.
- Code Agent automatic patch application.
- Multi-user auth, job submission, job monitoring, or artifact collection.

## Proposed File Structure

Create or modify these areas as tasks require them:

```text
research/
  include/agent_rpc/research/lab_code_adapter.h
  src/lab_code_adapter.cpp

resources/lab_code_adapter/
  config_templates/fwi_marmousi_multiscale.json
  logs/fwi_loss_stagnation.log
  logs/fwi_nan_instability.log

tests/
  test_lab_code_adapter.cpp

docs/upgrade/
  milestones.md
  career-notes.md
  upgrade-log.md
  test-report-v0.6.md
```

## Task 0: Start v0.6 Plan And Milestone Board

**Files:**
- Create: `docs/superpowers/plans/2026-06-22-lab-code-adapter-v0.6.md`
- Modify: `docs/upgrade/README.md`
- Modify: `docs/upgrade/milestones.md`
- Modify: `docs/upgrade/version-roadmap.md`
- Modify: `docs/upgrade/career-notes.md`
- Modify: `docs/upgrade/upgrade-log.md`

- [x] **Step 1: Create the v0.6 implementation plan**

Record the adapter scope, file structure, safety boundaries, and next
test-first implementation tasks.

- [x] **Step 2: Update upgrade docs**

Mark v0.6 as the active target, add Lab Code Adapter tasks to the milestone
board, and keep career notes honest by listing v0.6 as planned rather than
implemented.

- [x] **Step 3: Validate docs and baseline build**

Run:

```bash
git diff --check
cmake --build build -j2
ctest --test-dir build --output-on-failure
```

Expected:

```text
git diff --check produces no output
build exits 0
ctest passes
```

## Task 1: Config Template Reader

**Files:**
- Create: `research/include/agent_rpc/research/lab_code_adapter.h`
- Create: `research/src/lab_code_adapter.cpp`
- Modify: `research/CMakeLists.txt`
- Create: `resources/lab_code_adapter/config_templates/fwi_marmousi_multiscale.json`
- Create: `tests/test_lab_code_adapter.cpp`
- Modify: `tests/CMakeLists.txt`
- Modify: `docs/upgrade/upgrade-log.md`
- Modify: `docs/upgrade/career-notes.md`

- [x] **Step 1: Write failing template-loading test**

Add a test that loads a JSON template fixture and asserts:

- template id, algorithm id, and description are present;
- placeholders such as `dataset`, `start_frequency_hz`, `max_frequency_hz`,
  `grid_spacing_m`, and `iteration_count` are parsed deterministically;
- every placeholder declares type, required flag, and safe description;
- no command execution fields are accepted.

Run:

```bash
cmake --build build -j2
```

Expected RED:

```text
missing lab_code_adapter header or symbols
```

- [x] **Step 2: Implement minimal reader and validation**

Add C++ types for `ConfigTemplate` and `ConfigPlaceholder`. Load JSON from a
known fixture path and reject unknown execution fields such as `submit_command`,
`ssh_host`, `slurm_partition`, or `pbs_queue`.

- [x] **Step 3: Validate**

Run:

```bash
cmake --build build -j2
ctest --test-dir build -R LabCodeAdapter --output-on-failure
ctest --test-dir build --output-on-failure
git diff --check
```

## Task 2: Safe Config Generator

**Files:**
- Modify: `research/include/agent_rpc/research/lab_code_adapter.h`
- Modify: `research/src/lab_code_adapter.cpp`
- Modify: `tests/test_lab_code_adapter.cpp`
- Modify: `docs/upgrade/upgrade-log.md`
- Modify: `docs/upgrade/career-notes.md`

- [x] **Step 1: Write failing generation test**

Test that valid parameter values render a config preview with all placeholders
filled and a `dry_run: true` marker.

- [x] **Step 2: Implement deterministic rendering**

Render config text from structured values only. Do not invoke shell expansion,
environment-variable expansion, or file writes outside fixture/test paths.

- [x] **Step 3: Validate**

Run the targeted Lab Code Adapter test, full CTest, build, and diff check.

## Task 3: Log And Loss Curve Parser

**Files:**
- Modify: `research/include/agent_rpc/research/lab_code_adapter.h`
- Modify: `research/src/lab_code_adapter.cpp`
- Create: `resources/lab_code_adapter/logs/fwi_loss_stagnation.log`
- Modify: `tests/test_lab_code_adapter.cpp`
- Modify: `docs/upgrade/upgrade-log.md`
- Modify: `docs/upgrade/career-notes.md`

- [x] **Step 1: Write failing log parser test**

Test parsing of iteration number, loss value, frequency band, warning lines,
and final status from fixture text.

- [x] **Step 2: Implement parser**

Parse supplied text content as data. The parser must not open arbitrary paths
from user input and must not execute commands embedded in logs.

- [x] **Step 3: Validate**

Run targeted and full validation.

## Task 4: Failure Recognizer

**Files:**
- Modify: `research/include/agent_rpc/research/lab_code_adapter.h`
- Modify: `research/src/lab_code_adapter.cpp`
- Modify: `tests/test_lab_code_adapter.cpp`
- Modify: `docs/upgrade/upgrade-log.md`
- Modify: `docs/upgrade/career-notes.md`

- [x] **Step 1: Write failing recognizer test**

Cover at least:

- loss stagnation;
- NaN/Inf instability;
- cycle-skipping hint from high starting frequency or missing low frequency;
- resource-limit symptoms from text logs.

- [x] **Step 2: Implement recognizer**

Return structured findings with severity, evidence snippets, and suggested next
checks. Keep recommendations diagnostic; do not submit jobs.

- [x] **Step 3: Validate**

Run targeted and full validation.

## Task 5: Planner-Facing Summary

**Files:**
- Modify: `research/include/agent_rpc/research/lab_code_adapter.h`
- Modify: `research/src/lab_code_adapter.cpp`
- Modify: `research/src/planner_context.cpp` if needed
- Modify: `tests/test_lab_code_adapter.cpp`
- Modify: `tests/test_planner_context.cpp` if needed
- Modify: `docs/upgrade/upgrade-log.md`
- Modify: `docs/upgrade/career-notes.md`

- [x] **Step 1: Write failing summary test**

Test that parsed config/log/loss findings can be summarized into planner
context fields: observed symptoms, likely causes, parameter tuning suggestions,
and explicit dry-run boundary.

- [x] **Step 2: Implement summary adapter**

Keep the adapter deterministic and local. If PlannerContext is touched, pass
structured findings rather than raw unbounded logs.

- [x] **Step 3: Validate**

Run targeted Lab Code Adapter and PlannerContext tests, then full validation.

## Task 6: v0.6 Test Report And Completion Docs

**Files:**
- Create: `docs/upgrade/test-report-v0.6.md`
- Modify: `docs/upgrade/README.md`
- Modify: `docs/upgrade/milestones.md`
- Modify: `docs/upgrade/version-roadmap.md`
- Modify: `docs/upgrade/career-notes.md`
- Modify: `docs/upgrade/upgrade-log.md`

- [x] **Step 1: Write v0.6 test report**

Document config template loading, config rendering, log parsing, loss curve
extraction, failure recognition, and safety boundaries.

- [x] **Step 2: Mark v0.6 complete only after tests pass**

Do not mark v0.6 complete until:

```bash
cmake --build build -j2
ctest --test-dir build --output-on-failure
git diff --check
```

all pass and the report records the results.

- [x] **Step 3: Add Chinese learning summary**

Include the detailed sections required by `docs/upgrade/README.md`: problem,
implementation, key tests/resources, safety boundaries, TDD evidence, interview
pitch, technical deep dive, likely follow-up questions, and STAR recap.
