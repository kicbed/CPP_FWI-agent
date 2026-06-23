# Safe Operations v0.11 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a metadata-only safe operation policy for internal lab accounts and deletion dry-run review packets without executing deletion, shell commands, credential reads, or server connections.

**Architecture:** Add a focused `safe_operations` research module next to `single_server_backend`. The module owns role parsing, operation allowlist checks, delete review request validation, and stable review packet rendering. It must not include filesystem deletion APIs, process execution APIs, SSH, scheduler calls, or credential-loading code.

**Tech Stack:** C++17/C++20, CMake, GoogleTest, existing `agent_rpc_research` library, Markdown upgrade docs.

---

## Scope

In scope:

- `LabAccountRole` enum with `LabRoot`, `LabUser`, `ReadOnly`, `Unknown`.
- `SafeOperationType` enum with safe read/review operations and delete dry-run preview.
- `SafeOperationRequest` metadata.
- `SafeOperationPolicy` metadata.
- `DeleteReviewRequest` metadata.
- `DeleteReviewPacket` metadata.
- Validation for role/operation allowlist.
- Validation for delete dry-run preview request.
- Renderer for delete review packet.
- Tests proving no real deletion is represented as executed.

Out of scope:

- Real delete.
- Trash move.
- Filesystem traversal.
- Filesystem remove.
- Shell command execution.
- SSH, Slurm, PBS, local wrapper, remote server connection.
- Credential loading.
- Workspace creation or cleanup.
- Production audit persistence.

## File Structure

Planned files:

```text
research/include/agent_rpc/research/safe_operations.h
research/src/safe_operations.cpp
tests/test_safe_operations.cpp
docs/upgrade/test-report-v0.11.md
docs/upgrade/learning-summary-v0.11-safe-operations.md
docs/upgrade/safe-operations-v0.11.md
docs/upgrade/next-session-safe-operations-v0.11.md
docs/upgrade/README.md
docs/upgrade/milestones.md
docs/upgrade/version-roadmap.md
docs/upgrade/career-notes.md
docs/upgrade/upgrade-log.md
research/CMakeLists.txt
tests/CMakeLists.txt
```

## Task 1: Design, Plan, Prompt, And Learning Docs

**Files:**
- Create: `docs/upgrade/safe-operations-v0.11.md`
- Create: `docs/superpowers/plans/2026-06-23-safe-operations-v0.11.md`
- Create: `docs/upgrade/next-session-safe-operations-v0.11.md`
- Create: `docs/upgrade/learning-summary-v0.11-safe-operations.md`
- Modify: `docs/upgrade/README.md`
- Modify: `docs/upgrade/milestones.md`
- Modify: `docs/upgrade/version-roadmap.md`
- Modify: `docs/upgrade/career-notes.md`
- Modify: `docs/upgrade/upgrade-log.md`

- [x] **Step 1: Write safe operation design**

Document internal lab roles, operation risk levels, delete dry-run boundary, and non-goals.

- [x] **Step 2: Write this implementation plan**

Keep the implementation focused on metadata, validation, and review packet rendering.

- [x] **Step 3: Write next-session prompt**

Create a copyable prompt that reads the v0.11 docs and explicitly forbids real deletion.

- [x] **Step 4: Write learning summary**

Explain why internal lab tools still need operation safety and why delete starts as dry-run review only.

- [x] **Step 5: Validate and commit docs**

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
git add docs/upgrade/safe-operations-v0.11.md \
  docs/superpowers/plans/2026-06-23-safe-operations-v0.11.md \
  docs/upgrade/next-session-safe-operations-v0.11.md \
  docs/upgrade/learning-summary-v0.11-safe-operations.md \
  docs/upgrade/README.md \
  docs/upgrade/milestones.md \
  docs/upgrade/version-roadmap.md \
  docs/upgrade/career-notes.md \
  docs/upgrade/upgrade-log.md
git commit -m "docs: plan safe operations policy"
```

## Task 2: Role And Operation Policy Contract

**Files:**
- Create: `research/include/agent_rpc/research/safe_operations.h`
- Create: `research/src/safe_operations.cpp`
- Create: `tests/test_safe_operations.cpp`
- Modify: `research/CMakeLists.txt`
- Modify: `tests/CMakeLists.txt`
- Modify: `docs/upgrade/upgrade-log.md`

- [ ] **Step 1: Write failing role/policy tests**

Create `tests/test_safe_operations.cpp`:

```cpp
#include <gtest/gtest.h>

#include <agent_rpc/research/safe_operations.h>

#include <algorithm>

using namespace agent_rpc::research;

namespace {

SafeOperationPolicy make_policy() {
    SafeOperationPolicy policy;
    policy.allowed_readonly_operations = {
        SafeOperationType::ListDirectory,
        SafeOperationType::ReadFile,
        SafeOperationType::ParseLog,
        SafeOperationType::RenderReviewPacket,
    };
    policy.allowed_lab_user_operations = {
        SafeOperationType::ListDirectory,
        SafeOperationType::ReadFile,
        SafeOperationType::ParseLog,
        SafeOperationType::RenderReviewPacket,
        SafeOperationType::RunApprovedTemplateDryRun,
        SafeOperationType::DeleteWorkspaceDryRun,
    };
    policy.allowed_lab_root_operations = {
        SafeOperationType::ListDirectory,
        SafeOperationType::ReadFile,
        SafeOperationType::ParseLog,
        SafeOperationType::RenderReviewPacket,
        SafeOperationType::RunApprovedTemplateDryRun,
        SafeOperationType::DeleteWorkspaceDryRun,
        SafeOperationType::MaintainTemplates,
    };
    return policy;
}

}  // namespace

TEST(SafeOperationsTest, ParsesLabAccountRoles) {
    EXPECT_EQ(parse_lab_account_role("lab_root"), LabAccountRole::LabRoot);
    EXPECT_EQ(parse_lab_account_role("lab_user"), LabAccountRole::LabUser);
    EXPECT_EQ(parse_lab_account_role("readonly"), LabAccountRole::ReadOnly);
    EXPECT_EQ(parse_lab_account_role("other"), LabAccountRole::Unknown);
}

TEST(SafeOperationsTest, ReadOnlyCanReadButCannotRequestDeletePreview) {
    SafeOperationRequest read_request;
    read_request.user_id = "observer-a";
    read_request.role = LabAccountRole::ReadOnly;
    read_request.operation_type = SafeOperationType::ReadFile;
    EXPECT_TRUE(validate_safe_operation_request(read_request, make_policy()).empty());

    SafeOperationRequest delete_request;
    delete_request.user_id = "observer-a";
    delete_request.role = LabAccountRole::ReadOnly;
    delete_request.operation_type = SafeOperationType::DeleteWorkspaceDryRun;
    const auto errors = validate_safe_operation_request(delete_request, make_policy());
    EXPECT_NE(std::find(errors.begin(), errors.end(),
                  "operation is not allowed for role"),
        errors.end());
}

TEST(SafeOperationsTest, LabRootStillCannotRequestExecutionDelete) {
    SafeOperationRequest request;
    request.user_id = "root-a";
    request.role = LabAccountRole::LabRoot;
    request.operation_type = SafeOperationType::DeleteWorkspaceExecute;

    const auto errors = validate_safe_operation_request(request, make_policy());

    EXPECT_NE(std::find(errors.begin(), errors.end(),
                  "real deletion is not enabled"),
        errors.end());
}
```

Append target near `test_single_server_backend` in `tests/CMakeLists.txt`:

```cmake
add_executable(test_safe_operations test_safe_operations.cpp)
target_link_libraries(test_safe_operations
    agent_rpc_research
    GTest::gtest
    GTest::gtest_main
    pthread
)
target_include_directories(test_safe_operations PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../research/include
)
add_test(NAME SafeOperationsTest COMMAND test_safe_operations)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cmake --build build -j2
```

Expected:

```text
fatal error: agent_rpc/research/safe_operations.h: No such file or directory
```

- [ ] **Step 3: Add public header**

Create `research/include/agent_rpc/research/safe_operations.h`:

```cpp
#pragma once

#include <string>
#include <vector>

namespace agent_rpc::research {

enum class LabAccountRole {
    LabRoot,
    LabUser,
    ReadOnly,
    Unknown,
};

enum class SafeOperationType {
    ListDirectory,
    ReadFile,
    ParseLog,
    RenderReviewPacket,
    RunApprovedTemplateDryRun,
    DeleteWorkspaceDryRun,
    DeleteWorkspaceExecute,
    MaintainTemplates,
    Unknown,
};

struct SafeOperationRequest {
    std::string request_id;
    std::string user_id;
    LabAccountRole role = LabAccountRole::Unknown;
    SafeOperationType operation_type = SafeOperationType::Unknown;
};

struct SafeOperationPolicy {
    std::vector<SafeOperationType> allowed_readonly_operations;
    std::vector<SafeOperationType> allowed_lab_user_operations;
    std::vector<SafeOperationType> allowed_lab_root_operations;
};

LabAccountRole parse_lab_account_role(const std::string& value);
std::string to_string(LabAccountRole role);
std::string to_string(SafeOperationType operation_type);
std::vector<std::string> validate_safe_operation_request(
    const SafeOperationRequest& request,
    const SafeOperationPolicy& policy);

}  // namespace agent_rpc::research
```

- [ ] **Step 4: Add minimal implementation**

Create `research/src/safe_operations.cpp`:

```cpp
#include "agent_rpc/research/safe_operations.h"

#include <algorithm>

namespace agent_rpc::research {
namespace {

bool contains(const std::vector<SafeOperationType>& values, SafeOperationType value) {
    return std::find(values.begin(), values.end(), value) != values.end();
}

}  // namespace

LabAccountRole parse_lab_account_role(const std::string& value) {
    if (value == "lab_root") {
        return LabAccountRole::LabRoot;
    }
    if (value == "lab_user") {
        return LabAccountRole::LabUser;
    }
    if (value == "readonly") {
        return LabAccountRole::ReadOnly;
    }
    return LabAccountRole::Unknown;
}

std::string to_string(LabAccountRole role) {
    switch (role) {
        case LabAccountRole::LabRoot:
            return "lab_root";
        case LabAccountRole::LabUser:
            return "lab_user";
        case LabAccountRole::ReadOnly:
            return "readonly";
        case LabAccountRole::Unknown:
            return "unknown";
    }
    return "unknown";
}

std::string to_string(SafeOperationType operation_type) {
    switch (operation_type) {
        case SafeOperationType::ListDirectory:
            return "list_directory";
        case SafeOperationType::ReadFile:
            return "read_file";
        case SafeOperationType::ParseLog:
            return "parse_log";
        case SafeOperationType::RenderReviewPacket:
            return "render_review_packet";
        case SafeOperationType::RunApprovedTemplateDryRun:
            return "run_approved_template_dry_run";
        case SafeOperationType::DeleteWorkspaceDryRun:
            return "delete_workspace_dry_run";
        case SafeOperationType::DeleteWorkspaceExecute:
            return "delete_workspace_execute";
        case SafeOperationType::MaintainTemplates:
            return "maintain_templates";
        case SafeOperationType::Unknown:
            return "unknown";
    }
    return "unknown";
}

std::vector<std::string> validate_safe_operation_request(
    const SafeOperationRequest& request,
    const SafeOperationPolicy& policy) {
    std::vector<std::string> errors;
    if (request.user_id.empty()) {
        errors.push_back("user_id is required");
    }
    if (request.operation_type == SafeOperationType::DeleteWorkspaceExecute) {
        errors.push_back("real deletion is not enabled");
        return errors;
    }
    const std::vector<SafeOperationType>* allowed = nullptr;
    if (request.role == LabAccountRole::ReadOnly) {
        allowed = &policy.allowed_readonly_operations;
    } else if (request.role == LabAccountRole::LabUser) {
        allowed = &policy.allowed_lab_user_operations;
    } else if (request.role == LabAccountRole::LabRoot) {
        allowed = &policy.allowed_lab_root_operations;
    } else {
        errors.push_back("known lab account role is required");
        return errors;
    }
    if (!contains(*allowed, request.operation_type)) {
        errors.push_back("operation is not allowed for role");
    }
    return errors;
}

}  // namespace agent_rpc::research
```

- [ ] **Step 5: Wire CMake**

Add `src/safe_operations.cpp` to `research/CMakeLists.txt`.

- [ ] **Step 6: Run role/policy tests**

Run:

```bash
cmake --build build -j2
ctest --test-dir build -R SafeOperationsTest --output-on-failure
```

Expected:

```text
SafeOperationsTest passes
```

## Task 3: Delete Dry-Run Review Packet

**Files:**
- Modify: `research/include/agent_rpc/research/safe_operations.h`
- Modify: `research/src/safe_operations.cpp`
- Modify: `tests/test_safe_operations.cpp`
- Modify: `docs/upgrade/upgrade-log.md`

- [ ] **Step 1: Add failing delete review tests**

Append to `tests/test_safe_operations.cpp`:

```cpp
TEST(SafeOperationsTest, RejectsNonDryRunDeleteReviewRequest) {
    DeleteReviewRequest request;
    request.request_id = "delete-1";
    request.user_id = "researcher-a";
    request.role = LabAccountRole::LabUser;
    request.workspace_root = "/lab/workspaces";
    request.target_path = "/lab/workspaces/job-1";
    request.confirmation_phrase = "/lab/workspaces/job-1";
    request.dry_run = false;

    const auto errors = validate_delete_review_request(request);

    EXPECT_NE(std::find(errors.begin(), errors.end(),
                  "delete review request must remain dry_run"),
        errors.end());
}

TEST(SafeOperationsTest, RejectsPathTraversalDeleteReviewRequest) {
    DeleteReviewRequest request;
    request.request_id = "delete-2";
    request.user_id = "researcher-a";
    request.role = LabAccountRole::LabUser;
    request.workspace_root = "/lab/workspaces";
    request.target_path = "/lab/workspaces/../other";
    request.confirmation_phrase = "/lab/workspaces/../other";
    request.dry_run = true;

    const auto errors = validate_delete_review_request(request);

    EXPECT_NE(std::find(errors.begin(), errors.end(),
                  "delete target must not contain path traversal"),
        errors.end());
}

TEST(SafeOperationsTest, RendersDeleteReviewPacketWithoutExecutingDeletion) {
    DeleteReviewRequest request;
    request.request_id = "delete-3";
    request.user_id = "researcher-a";
    request.role = LabAccountRole::LabUser;
    request.workspace_root = "/lab/workspaces";
    request.target_path = "/lab/workspaces/job-3";
    request.confirmation_phrase = "/lab/workspaces/job-3";
    request.dry_run = true;

    const auto packet = render_delete_review_packet(request);

    EXPECT_NE(packet.find("Delete Dry-Run Review Packet"), std::string::npos);
    EXPECT_NE(packet.find("request_id: delete-3"), std::string::npos);
    EXPECT_NE(packet.find("target_path: /lab/workspaces/job-3"), std::string::npos);
    EXPECT_NE(packet.find("deletion_executed: false"), std::string::npos);
    EXPECT_NE(packet.find("trash_move_executed: false"), std::string::npos);
    EXPECT_NE(packet.find("shell_executed: false"), std::string::npos);
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cmake --build build -j2
ctest --test-dir build -R SafeOperationsTest --output-on-failure
```

Expected:

```text
Build fails because DeleteReviewRequest and render_delete_review_packet are not declared.
```

- [ ] **Step 3: Add delete review API to header**

Append to `research/include/agent_rpc/research/safe_operations.h` before the final namespace close:

```cpp
struct DeleteReviewRequest {
    std::string request_id;
    std::string user_id;
    LabAccountRole role = LabAccountRole::Unknown;
    std::string workspace_root;
    std::string target_path;
    std::string confirmation_phrase;
    bool dry_run = true;
};

std::vector<std::string> validate_delete_review_request(
    const DeleteReviewRequest& request);
std::string render_delete_review_packet(const DeleteReviewRequest& request);
```

- [ ] **Step 4: Implement delete review validation and renderer**

Append to `research/src/safe_operations.cpp` before the final namespace close:

```cpp
std::vector<std::string> validate_delete_review_request(
    const DeleteReviewRequest& request) {
    std::vector<std::string> errors;
    if (request.request_id.empty()) {
        errors.push_back("request_id is required");
    }
    if (request.user_id.empty()) {
        errors.push_back("user_id is required");
    }
    if (!request.dry_run) {
        errors.push_back("delete review request must remain dry_run");
    }
    if (request.workspace_root.empty()) {
        errors.push_back("workspace_root is required");
    }
    if (request.target_path.empty()) {
        errors.push_back("target_path is required");
    }
    if (request.target_path.find("..") != std::string::npos) {
        errors.push_back("delete target must not contain path traversal");
    }
    if (!request.workspace_root.empty() &&
        request.target_path == request.workspace_root) {
        errors.push_back("delete target must not be the workspace root");
    }
    if (!request.workspace_root.empty() &&
        request.target_path.rfind(request.workspace_root + "/", 0) != 0) {
        errors.push_back("delete target must stay under workspace root");
    }
    if (request.confirmation_phrase != request.target_path) {
        errors.push_back("confirmation phrase must match target_path");
    }
    return errors;
}

std::string render_delete_review_packet(const DeleteReviewRequest& request) {
    std::ostringstream out;
    const auto errors = validate_delete_review_request(request);
    out << "Delete Dry-Run Review Packet\n";
    out << "request_id: " << request.request_id << "\n";
    out << "user_id: " << request.user_id << "\n";
    out << "role: " << to_string(request.role) << "\n";
    out << "workspace_root: " << request.workspace_root << "\n";
    out << "target_path: " << request.target_path << "\n";
    out << "dry_run: true\n";
    out << "deletion_executed: false\n";
    out << "trash_move_executed: false\n";
    out << "shell_executed: false\n";
    out << "review_status: " << (errors.empty() ? "reviewable" : "blocked") << "\n";
    out << "validation_errors:\n";
    if (errors.empty()) {
        out << "- none\n";
    } else {
        for (const auto& error : errors) {
            out << "- " << error << "\n";
        }
    }
    return out.str();
}
```

Add `#include <sstream>` to `research/src/safe_operations.cpp`.

- [ ] **Step 5: Run delete review tests and full tests**

Run:

```bash
cmake --build build -j2
ctest --test-dir build -R SafeOperationsTest --output-on-failure
ctest --test-dir build --output-on-failure
git diff --check
```

Expected:

```text
cmake build exits 0
SafeOperationsTest passes
full ctest reports 100% tests passed
git diff --check produces no output
```

## Task 4: v0.11 Test Report And Completion Docs

**Files:**
- Create: `docs/upgrade/test-report-v0.11.md`
- Modify: `docs/upgrade/README.md`
- Modify: `docs/upgrade/milestones.md`
- Modify: `docs/upgrade/version-roadmap.md`
- Modify: `docs/upgrade/career-notes.md`
- Modify: `docs/upgrade/upgrade-log.md`

- [ ] **Step 1: Write test report**

Record RED/GREEN evidence, test names, and final verification commands.

- [ ] **Step 2: Mark v0.11 implementation complete in upgrade docs**

Update docs only after tests pass. Do not claim real delete support.

- [ ] **Step 3: Final validation**

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

- [ ] **Step 4: Commit**

```bash
git add research/include/agent_rpc/research/safe_operations.h \
  research/src/safe_operations.cpp \
  tests/test_safe_operations.cpp \
  research/CMakeLists.txt \
  tests/CMakeLists.txt \
  docs/upgrade/test-report-v0.11.md \
  docs/upgrade/README.md \
  docs/upgrade/milestones.md \
  docs/upgrade/version-roadmap.md \
  docs/upgrade/career-notes.md \
  docs/upgrade/upgrade-log.md
git commit -m "feat: add safe operations policy"
```
