# Server Backend v0.8 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the v0.8 server-backend safety foundation without enabling real CUDA/MPI, SSH, Slurm, PBS, remote server, local wrapper, or arbitrary shell execution.

**Architecture:** Keep `DryRunBackend` as the only enabled runtime backend while adding explicit server-job models, approved-template validation, workspace path checks, lifecycle records, and audit-oriented tests. Real scheduler or remote adapters remain behind a later reviewed gate.

**Tech Stack:** C++17/C++20, CMake, GoogleTest, nlohmann/json, existing `agent_rpc_research` library, Markdown upgrade docs.

---

## Scope

This plan starts v0.8. It is intentionally safety-first.

In scope:

- Server backend safety design.
- Job submission request and record data models.
- Job lifecycle state enum.
- Approved job template data model.
- Workspace path validation.
- Lifecycle test helpers or a fake server backend for tests only.
- Planner/UI documentation for dry-run versus explicit submission boundaries.

Out of scope:

- Real CUDA/MPI execution.
- SSH connection code.
- Slurm/PBS submission code.
- Remote server integration.
- Running local wrapper scripts.
- Credentials, private keys, or cluster account handling.
- Arbitrary shell execution from user text.
- Automatic Code Agent patch application.

## File Structure

Planned files:

```text
research/include/agent_rpc/research/server_job.h
research/src/server_job.cpp
tests/test_server_job.cpp
docs/upgrade/server-backend-safety-v0.8.md
docs/upgrade/test-report-v0.8.md
docs/upgrade/README.md
docs/upgrade/milestones.md
docs/upgrade/version-roadmap.md
docs/upgrade/career-notes.md
docs/upgrade/upgrade-log.md
```

`server_job.h` owns server-job data types and validation helpers. It should not
submit work. Future adapter code should live in a separate file after safety
tests exist.

## Task 1: Safety Design And Plan

**Files:**
- Create: `docs/upgrade/server-backend-safety-v0.8.md`
- Create: `docs/superpowers/plans/2026-06-22-server-backend-v0.8.md`
- Modify: `docs/upgrade/README.md`
- Modify: `docs/upgrade/milestones.md`
- Modify: `docs/upgrade/version-roadmap.md`
- Modify: `docs/upgrade/career-notes.md`
- Modify: `docs/upgrade/upgrade-log.md`

- [x] **Step 1: Write the safety design**

Document:

- threat model
- non-goals
- API shape
- request data flow
- approved-template boundary
- workspace and artifact boundary
- validation gate before real execution

- [x] **Step 2: Write this implementation plan**

Keep tasks small enough that each later session can implement one focused
batch and commit it.

- [x] **Step 3: Update upgrade docs**

Mark v0.8 as started in docs only. Do not claim any real execution capability.

- [x] **Step 4: Validate and commit**

Run:

```bash
git diff --check
cmake --build build -j2
ctest --test-dir build --output-on-failure
```

Expected:

```text
git diff --check produces no output
cmake build exits 0
ctest reports 100% tests passed
```

Commit:

```bash
git add docs/upgrade/server-backend-safety-v0.8.md \
  docs/superpowers/plans/2026-06-22-server-backend-v0.8.md \
  docs/upgrade/README.md \
  docs/upgrade/milestones.md \
  docs/upgrade/version-roadmap.md \
  docs/upgrade/career-notes.md \
  docs/upgrade/upgrade-log.md
git commit -m "docs: start v0.8 server backend safety design"
```

## Task 2: Server Job Model Contract

**Files:**
- Create: `research/include/agent_rpc/research/server_job.h`
- Create: `research/src/server_job.cpp`
- Create: `tests/test_server_job.cpp`
- Modify: `research/CMakeLists.txt`
- Modify: `tests/CMakeLists.txt`
- Modify: `docs/upgrade/upgrade-log.md`

- [ ] **Step 1: Write the failing test**

Create `tests/test_server_job.cpp`:

```cpp
#include <gtest/gtest.h>

#include <agent_rpc/research/server_job.h>

using namespace agent_rpc::research;

TEST(ServerJobTest, SubmissionRequestDefaultsToDryRun) {
    JobSubmissionRequest request;
    EXPECT_TRUE(request.dry_run);
    EXPECT_EQ(request.backend_type, JobBackendType::DryRun);
}

TEST(ServerJobTest, ParsesLifecycleStateNames) {
    EXPECT_EQ(parse_job_lifecycle_state("draft"), JobLifecycleState::Draft);
    EXPECT_EQ(parse_job_lifecycle_state("queued"), JobLifecycleState::Queued);
    EXPECT_EQ(parse_job_lifecycle_state("submitted"), JobLifecycleState::Submitted);
    EXPECT_EQ(parse_job_lifecycle_state("running"), JobLifecycleState::Running);
    EXPECT_EQ(parse_job_lifecycle_state("succeeded"), JobLifecycleState::Succeeded);
    EXPECT_EQ(parse_job_lifecycle_state("failed"), JobLifecycleState::Failed);
    EXPECT_EQ(parse_job_lifecycle_state("cancelled"), JobLifecycleState::Cancelled);
    EXPECT_EQ(parse_job_lifecycle_state("other"), JobLifecycleState::Rejected);
}

TEST(ServerJobTest, RejectsNonDryRunSubmissionBeforeBackendsAreEnabled) {
    JobSubmissionRequest request;
    request.backend_type = JobBackendType::Slurm;
    request.template_id = "fwi_multiscale_slurm";
    const auto errors = validate_submission_boundary(request);
    ASSERT_FALSE(errors.empty());
    EXPECT_NE(errors[0].find("only dry_run is enabled"), std::string::npos);
}
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cmake --build build -j2
```

Expected:

```text
fatal error: agent_rpc/research/server_job.h: No such file or directory
```

- [ ] **Step 3: Implement the model**

Create `research/include/agent_rpc/research/server_job.h`:

```cpp
#pragma once

#include <string>
#include <vector>

#include <agent_rpc/research/experiment_spec.h>
#include <agent_rpc/research/job_backend.h>

namespace agent_rpc::research {

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
    JobBackendType backend_type = JobBackendType::DryRun;
    std::string template_id;
    std::string template_version;
    ExperimentSpec experiment;
    JobSpec job;
    bool dry_run = true;
};

struct JobRecord {
    std::string job_id;
    JobLifecycleState state = JobLifecycleState::Draft;
    JobSubmissionRequest request;
    std::string workspace_path;
    std::vector<std::string> validation_messages;
    std::vector<std::string> status_events;
    std::vector<std::string> log_paths;
    std::vector<std::string> artifact_paths;
};

std::string to_string(JobLifecycleState state);
JobLifecycleState parse_job_lifecycle_state(const std::string& value);
std::vector<std::string> validate_submission_boundary(
    const JobSubmissionRequest& request);

}  // namespace agent_rpc::research
```

Create `research/src/server_job.cpp`:

```cpp
#include <agent_rpc/research/server_job.h>

namespace agent_rpc::research {

std::string to_string(JobLifecycleState state) {
    switch (state) {
        case JobLifecycleState::Draft:
            return "draft";
        case JobLifecycleState::Rejected:
            return "rejected";
        case JobLifecycleState::Queued:
            return "queued";
        case JobLifecycleState::Submitted:
            return "submitted";
        case JobLifecycleState::Running:
            return "running";
        case JobLifecycleState::Succeeded:
            return "succeeded";
        case JobLifecycleState::Failed:
            return "failed";
        case JobLifecycleState::Cancelled:
            return "cancelled";
    }
    return "rejected";
}

JobLifecycleState parse_job_lifecycle_state(const std::string& value) {
    if (value == "draft") return JobLifecycleState::Draft;
    if (value == "queued") return JobLifecycleState::Queued;
    if (value == "submitted") return JobLifecycleState::Submitted;
    if (value == "running") return JobLifecycleState::Running;
    if (value == "succeeded") return JobLifecycleState::Succeeded;
    if (value == "failed") return JobLifecycleState::Failed;
    if (value == "cancelled") return JobLifecycleState::Cancelled;
    return JobLifecycleState::Rejected;
}

std::vector<std::string> validate_submission_boundary(
    const JobSubmissionRequest& request) {
    std::vector<std::string> errors = validate_backend_enabled(request.backend_type);
    if (!request.dry_run) {
        errors.push_back("server execution is not enabled; submission must stay dry_run");
    }
    if (request.template_id.empty()) {
        errors.push_back("template_id is required for any future submission");
    }
    return errors;
}

}  // namespace agent_rpc::research
```

Update CMake files so `server_job.cpp` is part of `agent_rpc_research` and
`test_server_job` is registered as `ServerJobTest`.

- [ ] **Step 4: Run targeted and full tests**

Run:

```bash
cmake --build build -j2
ctest --test-dir build -R ServerJobTest --output-on-failure
ctest --test-dir build --output-on-failure
git diff --check
```

Expected:

```text
ServerJobTest passes
full ctest passes
git diff --check produces no output
```

- [ ] **Step 5: Commit**

```bash
git add research/include/agent_rpc/research/server_job.h \
  research/src/server_job.cpp \
  research/CMakeLists.txt \
  tests/test_server_job.cpp \
  tests/CMakeLists.txt \
  docs/upgrade/upgrade-log.md
git commit -m "feat: add server job safety model"
```

## Task 3: Approved Template Validation

**Files:**
- Modify: `research/include/agent_rpc/research/server_job.h`
- Modify: `research/src/server_job.cpp`
- Modify: `tests/test_server_job.cpp`
- Modify: `docs/upgrade/upgrade-log.md`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server_job.cpp`:

```cpp
TEST(ServerJobTest, RequiresApprovedTemplateForSubmission) {
    JobSubmissionRequest request;
    request.template_id = "unknown_template";

    ApprovedJobTemplate approved;
    approved.template_id = "fwi_multiscale_dry_run";
    approved.version = "1";
    approved.backend_type = JobBackendType::DryRun;

    const auto errors = validate_approved_template(request, {approved});
    ASSERT_FALSE(errors.empty());
    EXPECT_NE(errors[0].find("unknown approved template"), std::string::npos);
}

TEST(ServerJobTest, AcceptsMatchingDryRunTemplate) {
    JobSubmissionRequest request;
    request.template_id = "fwi_multiscale_dry_run";
    request.template_version = "1";

    ApprovedJobTemplate approved;
    approved.template_id = "fwi_multiscale_dry_run";
    approved.version = "1";
    approved.backend_type = JobBackendType::DryRun;
    approved.allowed_arguments = {"model", "dataset", "max_iter"};

    EXPECT_TRUE(validate_approved_template(request, {approved}).empty());
}
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cmake --build build -j2
```

Expected:

```text
error: unknown type name 'ApprovedJobTemplate'
```

- [ ] **Step 3: Add approved-template model and validation**

Add to `server_job.h`:

```cpp
struct ApprovedJobTemplate {
    std::string template_id;
    std::string version;
    JobBackendType backend_type = JobBackendType::DryRun;
    std::vector<std::string> allowed_arguments;
    std::vector<std::string> allowed_input_roots;
    int max_gpus = 0;
    int max_mpi_ranks = 1;
};

std::vector<std::string> validate_approved_template(
    const JobSubmissionRequest& request,
    const std::vector<ApprovedJobTemplate>& approved_templates);
```

Add to `server_job.cpp`:

```cpp
std::vector<std::string> validate_approved_template(
    const JobSubmissionRequest& request,
    const std::vector<ApprovedJobTemplate>& approved_templates) {
    for (const auto& approved : approved_templates) {
        if (approved.template_id != request.template_id) {
            continue;
        }
        std::vector<std::string> errors;
        if (!request.template_version.empty() &&
            approved.version != request.template_version) {
            errors.push_back("template version mismatch for '" + request.template_id + "'");
        }
        if (approved.backend_type != request.backend_type) {
            errors.push_back("template backend does not match requested backend");
        }
        return errors;
    }
    return {"unknown approved template '" + request.template_id + "'"};
}
```

- [ ] **Step 4: Run targeted and full tests**

Run:

```bash
cmake --build build -j2
ctest --test-dir build -R ServerJobTest --output-on-failure
ctest --test-dir build --output-on-failure
git diff --check
```

Expected:

```text
ServerJobTest passes
full ctest passes
git diff --check produces no output
```

- [ ] **Step 5: Commit**

```bash
git add research/include/agent_rpc/research/server_job.h \
  research/src/server_job.cpp \
  tests/test_server_job.cpp \
  docs/upgrade/upgrade-log.md
git commit -m "feat: validate approved job templates"
```

## Task 4: Workspace Path Guard

**Files:**
- Modify: `research/include/agent_rpc/research/server_job.h`
- Modify: `research/src/server_job.cpp`
- Modify: `tests/test_server_job.cpp`
- Modify: `docs/upgrade/upgrade-log.md`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server_job.cpp`:

```cpp
TEST(ServerJobTest, RejectsWorkspaceTraversal) {
    const auto errors = validate_workspace_path(
        "/tmp/lab-agent/jobs",
        "../outside");
    ASSERT_FALSE(errors.empty());
    EXPECT_NE(errors[0].find("workspace path escapes"), std::string::npos);
}

TEST(ServerJobTest, AcceptsGeneratedWorkspaceName) {
    EXPECT_TRUE(validate_workspace_path(
        "/tmp/lab-agent/jobs",
        "job-20260622-0001").empty());
}
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cmake --build build -j2
```

Expected:

```text
error: use of undeclared identifier 'validate_workspace_path'
```

- [ ] **Step 3: Implement workspace path validation**

Add to `server_job.h`:

```cpp
std::vector<std::string> validate_workspace_path(
    const std::string& workspace_root,
    const std::string& job_directory_name);
```

Add to `server_job.cpp`:

```cpp
std::vector<std::string> validate_workspace_path(
    const std::string& workspace_root,
    const std::string& job_directory_name) {
    std::vector<std::string> errors;
    if (workspace_root.empty()) {
        errors.push_back("workspace root is required");
    }
    if (job_directory_name.empty()) {
        errors.push_back("job directory name is required");
    }
    if (job_directory_name.find("..") != std::string::npos ||
        job_directory_name.find('/') != std::string::npos ||
        job_directory_name.find('\\') != std::string::npos) {
        errors.push_back("workspace path escapes the configured workspace root");
    }
    return errors;
}
```

- [ ] **Step 4: Run targeted and full tests**

Run:

```bash
cmake --build build -j2
ctest --test-dir build -R ServerJobTest --output-on-failure
ctest --test-dir build --output-on-failure
git diff --check
```

Expected:

```text
ServerJobTest passes
full ctest passes
git diff --check produces no output
```

- [ ] **Step 5: Commit**

```bash
git add research/include/agent_rpc/research/server_job.h \
  research/src/server_job.cpp \
  tests/test_server_job.cpp \
  docs/upgrade/upgrade-log.md
git commit -m "feat: guard server job workspaces"
```

## Task 5: Lifecycle Record Helpers

**Files:**
- Modify: `research/include/agent_rpc/research/server_job.h`
- Modify: `research/src/server_job.cpp`
- Modify: `tests/test_server_job.cpp`
- Modify: `docs/upgrade/upgrade-log.md`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server_job.cpp`:

```cpp
TEST(ServerJobTest, CreatesRejectedRecordFromValidationErrors) {
    JobSubmissionRequest request;
    request.request_id = "req-1";

    const auto record = make_rejected_job_record(
        "job-1",
        request,
        {"only dry_run is enabled"});

    EXPECT_EQ(record.job_id, "job-1");
    EXPECT_EQ(record.state, JobLifecycleState::Rejected);
    ASSERT_EQ(record.validation_messages.size(), 1u);
    EXPECT_EQ(record.validation_messages[0], "only dry_run is enabled");
}

TEST(ServerJobTest, AppendsLifecycleEventWithoutExecutingCommands) {
    JobRecord record;
    record.job_id = "job-1";

    append_lifecycle_event(record, JobLifecycleState::Queued, "queued by fake backend");

    EXPECT_EQ(record.state, JobLifecycleState::Queued);
    ASSERT_EQ(record.status_events.size(), 1u);
    EXPECT_NE(record.status_events[0].find("queued by fake backend"), std::string::npos);
}
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cmake --build build -j2
```

Expected:

```text
error: use of undeclared identifier 'make_rejected_job_record'
```

- [ ] **Step 3: Implement lifecycle helpers**

Add to `server_job.h`:

```cpp
JobRecord make_rejected_job_record(
    const std::string& job_id,
    const JobSubmissionRequest& request,
    const std::vector<std::string>& validation_messages);

void append_lifecycle_event(
    JobRecord& record,
    JobLifecycleState next_state,
    const std::string& message);
```

Add to `server_job.cpp`:

```cpp
JobRecord make_rejected_job_record(
    const std::string& job_id,
    const JobSubmissionRequest& request,
    const std::vector<std::string>& validation_messages) {
    JobRecord record;
    record.job_id = job_id;
    record.state = JobLifecycleState::Rejected;
    record.request = request;
    record.validation_messages = validation_messages;
    return record;
}

void append_lifecycle_event(
    JobRecord& record,
    JobLifecycleState next_state,
    const std::string& message) {
    record.state = next_state;
    record.status_events.push_back(to_string(next_state) + ": " + message);
}
```

These helpers mutate only in-memory records. They must not call `std::system`,
`popen`, SSH, Slurm, PBS, MPI launchers, or local scripts.

- [ ] **Step 4: Run targeted and full tests**

Run:

```bash
cmake --build build -j2
ctest --test-dir build -R ServerJobTest --output-on-failure
ctest --test-dir build --output-on-failure
git diff --check
```

Expected:

```text
ServerJobTest passes
full ctest passes
git diff --check produces no output
```

- [ ] **Step 5: Commit**

```bash
git add research/include/agent_rpc/research/server_job.h \
  research/src/server_job.cpp \
  tests/test_server_job.cpp \
  docs/upgrade/upgrade-log.md
git commit -m "feat: add server job lifecycle helpers"
```

## Task 6: v0.8 Test Report And Learning Summary

**Files:**
- Create: `docs/upgrade/test-report-v0.8.md`
- Modify: `docs/upgrade/README.md`
- Modify: `docs/upgrade/milestones.md`
- Modify: `docs/upgrade/version-roadmap.md`
- Modify: `docs/upgrade/career-notes.md`
- Modify: `docs/upgrade/upgrade-log.md`

- [ ] **Step 1: Confirm v0.8 safety scope is complete**

Run:

```bash
cmake --build build -j2
ctest --test-dir build --output-on-failure
git diff --check
```

Expected:

```text
cmake build exits 0
ctest reports 100% tests passed
git diff --check produces no output
```

- [ ] **Step 2: Write the test report**

The report must include:

- server-job model tests
- approved-template validation tests
- workspace guard tests
- lifecycle helper tests
- dry-run-only backend rejection evidence
- explicit statement that no real CUDA/MPI, SSH, Slurm, PBS, remote execution,
  local wrapper execution, arbitrary shell execution, or automatic Code Agent
  patch application was added

- [ ] **Step 3: Mark v0.8 complete only if code and tests exist**

Update docs only after Task 2 through Task 5 have landed. Do not mark v0.8
complete after Task 1 alone.

- [ ] **Step 4: Commit**

```bash
git add docs/upgrade/test-report-v0.8.md \
  docs/upgrade/README.md \
  docs/upgrade/milestones.md \
  docs/upgrade/version-roadmap.md \
  docs/upgrade/career-notes.md \
  docs/upgrade/upgrade-log.md
git commit -m "docs: complete v0.8 server backend safety report"
```

## Self-Review

- Spec coverage: this plan covers the safety design, dry-run boundary,
  submission models, approved templates, workspace isolation, lifecycle records,
  tests, docs, and completion report. Real adapters are intentionally excluded.
- Placeholder scan: no `TBD`, `TODO`, or "implement later" placeholders remain.
- Type consistency: `JobSubmissionRequest`, `JobRecord`,
  `JobLifecycleState`, `ApprovedJobTemplate`, and validation helper names are
  consistent across the planned header, source, and tests.
