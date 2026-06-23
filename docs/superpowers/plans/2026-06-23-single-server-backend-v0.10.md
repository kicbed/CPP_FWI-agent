# Single Server Backend v0.10 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a metadata-only single-server account preparation layer with profiles, approved templates, structured review requests, and dry-run review packets while keeping all real execution disabled.

**Architecture:** Keep the existing `JobBackend` runtime guard unchanged and add a separate `single_server_backend` metadata module under `agent_rpc_research`. The new module validates non-secret profile/template metadata and renders review text only; it must not read credentials, connect to a server, create workspaces, or execute commands.

**Tech Stack:** C++17/C++20, CMake, GoogleTest, existing `agent_rpc_research` library, Markdown upgrade docs.

---

## Scope

In scope:

- `SingleServerProfile` metadata.
- `SingleServerJobTemplate` metadata.
- `SingleServerReviewRequest` metadata.
- Profile validation for credential references, workspace references, allowed templates, and disabled runtime state.
- Template/request validation for profile matching, version matching, allowed parameters, and dry-run-only review.
- Stable dry-run review packet rendering.
- v0.10 test report and upgrade documentation.

Out of scope:

- Real CUDA/MPI execution.
- SSH, Slurm, PBS, local wrapper, or remote server connection.
- Credential loading from files, environment variables, secret managers, or operators.
- Workspace directory creation, cleanup, upload, download, or deletion.
- Production audit persistence.
- Arbitrary shell execution from user text.
- Automatic Code Agent patch application.

## File Structure

Planned files:

```text
research/include/agent_rpc/research/single_server_backend.h
research/src/single_server_backend.cpp
tests/test_single_server_backend.cpp
docs/upgrade/test-report-v0.10.md
docs/upgrade/learning-summary-v0.10.md
docs/upgrade/single-server-backend-v0.10.md
docs/upgrade/README.md
docs/upgrade/milestones.md
docs/upgrade/version-roadmap.md
docs/upgrade/career-notes.md
docs/upgrade/upgrade-log.md
research/CMakeLists.txt
tests/CMakeLists.txt
```

`single_server_backend.h` owns metadata types and pure validation/rendering functions. It must not include filesystem, SSH, process, scheduler, or credential-loading APIs.

## Task 1: Design And Plan

**Files:**
- Create: `docs/upgrade/single-server-backend-v0.10.md`
- Create: `docs/superpowers/plans/2026-06-23-single-server-backend-v0.10.md`
- Modify: `docs/upgrade/README.md`
- Modify: `docs/upgrade/milestones.md`
- Modify: `docs/upgrade/version-roadmap.md`
- Modify: `docs/upgrade/career-notes.md`
- Modify: `docs/upgrade/upgrade-log.md`

- [x] **Step 1: Write the design document**

Document the single-server account scenario, non-goals, metadata model, review packet shape, implementation order, validation commands, and completion criteria.

- [x] **Step 2: Write this implementation plan**

Keep the plan focused on metadata/profile/template and dry-run review packet only.

- [x] **Step 3: Validate and commit**

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
git add docs/upgrade/single-server-backend-v0.10.md \
  docs/superpowers/plans/2026-06-23-single-server-backend-v0.10.md \
  docs/upgrade/README.md \
  docs/upgrade/milestones.md \
  docs/upgrade/version-roadmap.md \
  docs/upgrade/career-notes.md \
  docs/upgrade/upgrade-log.md
git commit -m "docs: plan single server backend preparation"
```

## Task 2: Metadata Model Contract

**Files:**
- Create: `research/include/agent_rpc/research/single_server_backend.h`
- Create: `research/src/single_server_backend.cpp`
- Create: `tests/test_single_server_backend.cpp`
- Modify: `research/CMakeLists.txt`
- Modify: `tests/CMakeLists.txt`
- Modify: `docs/upgrade/upgrade-log.md`

- [ ] **Step 1: Write the failing metadata tests and test target**

Create `tests/test_single_server_backend.cpp`:

```cpp
#include <gtest/gtest.h>

#include <agent_rpc/research/single_server_backend.h>

#include <algorithm>

using namespace agent_rpc::research;

namespace {

SingleServerProfile make_profile() {
    SingleServerProfile profile;
    profile.profile_id = "single-server-dev";
    profile.display_name = "Single Server Dev Runner";
    profile.account_reference = "lab-single-server-account";
    profile.credential_reference = "secret-ref:single-server-runner";
    profile.workspace_root_reference = "workspace-ref:single-server-runs";
    profile.allowed_template_ids = {"fwi_multiscale_review"};
    profile.runtime_enabled = false;
    return profile;
}

}  // namespace

TEST(SingleServerBackendTest, AcceptsMetadataOnlyProfile) {
    const auto profile = make_profile();
    EXPECT_TRUE(validate_single_server_profile(profile).empty());
}

TEST(SingleServerBackendTest, RejectsProfileWithoutCredentialReference) {
    auto profile = make_profile();
    profile.credential_reference.clear();

    const auto errors = validate_single_server_profile(profile);

    EXPECT_NE(std::find(errors.begin(), errors.end(),
                  "credential_reference is required"),
        errors.end());
}

TEST(SingleServerBackendTest, RejectsInlineSecretLookingCredentialReference) {
    auto profile = make_profile();
    profile.credential_reference = "password=inline-secret-marker";

    const auto errors = validate_single_server_profile(profile);

    EXPECT_NE(std::find(errors.begin(), errors.end(),
                  "credential_reference must be a reference name, not an inline secret"),
        errors.end());
}

TEST(SingleServerBackendTest, RejectsRuntimeEnabledProfile) {
    auto profile = make_profile();
    profile.runtime_enabled = true;

    const auto errors = validate_single_server_profile(profile);

    EXPECT_NE(std::find(errors.begin(), errors.end(),
                  "single-server runtime execution is not enabled"),
        errors.end());
}
```

Append the test target to `tests/CMakeLists.txt` near `test_server_job`:

```cmake
add_executable(test_single_server_backend test_single_server_backend.cpp)
target_link_libraries(test_single_server_backend
    agent_rpc_research
    GTest::gtest
    GTest::gtest_main
    pthread
)
target_include_directories(test_single_server_backend PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../research/include
)
add_test(NAME SingleServerBackendTest COMMAND test_single_server_backend)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cmake --build build -j2
```

Expected:

```text
fatal error: agent_rpc/research/single_server_backend.h: No such file or directory
```

- [ ] **Step 3: Add the public metadata header**

Create `research/include/agent_rpc/research/single_server_backend.h`:

```cpp
#pragma once

#include <string>
#include <utility>
#include <vector>

namespace agent_rpc::research {

struct SingleServerProfile {
    std::string profile_id;
    std::string display_name;
    std::string account_reference;
    std::string credential_reference;
    std::string workspace_root_reference;
    std::vector<std::string> allowed_template_ids;
    bool runtime_enabled = false;
};

struct SingleServerJobTemplate {
    std::string template_id;
    std::string version;
    std::string profile_id;
    std::string entrypoint_label;
    std::vector<std::string> allowed_parameter_names;
    std::vector<std::string> expected_artifacts;
    int max_gpus = 0;
    int max_mpi_ranks = 1;
    int max_wall_time_minutes = 60;
};

struct SingleServerReviewRequest {
    std::string request_id;
    std::string user_id;
    std::string profile_id;
    std::string template_id;
    std::string template_version;
    std::vector<std::pair<std::string, std::string>> parameters;
    bool dry_run = true;
};

std::vector<std::string> validate_single_server_profile(
    const SingleServerProfile& profile);
std::vector<std::string> validate_single_server_template(
    const SingleServerProfile& profile,
    const SingleServerJobTemplate& job_template);
std::vector<std::string> validate_single_server_review_request(
    const SingleServerProfile& profile,
    const SingleServerJobTemplate& job_template,
    const SingleServerReviewRequest& request);
std::string render_single_server_review_packet(
    const SingleServerProfile& profile,
    const SingleServerJobTemplate& job_template,
    const SingleServerReviewRequest& request);

}  // namespace agent_rpc::research
```

- [ ] **Step 4: Add the minimal profile validation implementation**

Create `research/src/single_server_backend.cpp`:

```cpp
#include "agent_rpc/research/single_server_backend.h"

#include <algorithm>
#include <sstream>

namespace agent_rpc::research {
namespace {

bool contains(const std::vector<std::string>& values, const std::string& value) {
    return std::find(values.begin(), values.end(), value) != values.end();
}

bool looks_like_inline_secret(const std::string& value) {
    return value.find("password=") != std::string::npos ||
           value.find("token=") != std::string::npos ||
           value.find("-----BEGIN") != std::string::npos ||
           value.find("PRIVATE KEY") != std::string::npos;
}

}  // namespace

std::vector<std::string> validate_single_server_profile(
    const SingleServerProfile& profile) {
    std::vector<std::string> errors;
    if (profile.profile_id.empty()) {
        errors.push_back("profile_id is required");
    }
    if (profile.account_reference.empty()) {
        errors.push_back("account_reference is required");
    }
    if (profile.credential_reference.empty()) {
        errors.push_back("credential_reference is required");
    } else if (looks_like_inline_secret(profile.credential_reference)) {
        errors.push_back(
            "credential_reference must be a reference name, not an inline secret");
    }
    if (profile.workspace_root_reference.empty()) {
        errors.push_back("workspace_root_reference is required");
    }
    if (profile.allowed_template_ids.empty()) {
        errors.push_back("allowed_template_ids must include at least one template");
    }
    if (profile.runtime_enabled) {
        errors.push_back("single-server runtime execution is not enabled");
    }
    return errors;
}

std::vector<std::string> validate_single_server_template(
    const SingleServerProfile& profile,
    const SingleServerJobTemplate& job_template) {
    return {};
}

std::vector<std::string> validate_single_server_review_request(
    const SingleServerProfile& profile,
    const SingleServerJobTemplate& job_template,
    const SingleServerReviewRequest& request) {
    return {};
}

std::string render_single_server_review_packet(
    const SingleServerProfile& profile,
    const SingleServerJobTemplate& job_template,
    const SingleServerReviewRequest& request) {
    return {};
}

}  // namespace agent_rpc::research
```

- [ ] **Step 5: Wire the implementation file into CMake**

Modify `research/CMakeLists.txt` so `single_server_backend.cpp` is compiled into `agent_rpc_research` next to `server_job.cpp`.

- [ ] **Step 6: Run metadata tests**

Run:

```bash
cmake --build build -j2
ctest --test-dir build -R SingleServerBackendTest --output-on-failure
```

Expected:

```text
SingleServerBackendTest passes
```

- [ ] **Step 7: Commit**

```bash
git add research/include/agent_rpc/research/single_server_backend.h \
  research/src/single_server_backend.cpp \
  tests/test_single_server_backend.cpp \
  research/CMakeLists.txt \
  tests/CMakeLists.txt \
  docs/upgrade/upgrade-log.md
git commit -m "feat: add single server metadata profiles"
```

## Task 3: Template And Review Request Validation

**Files:**
- Modify: `research/src/single_server_backend.cpp`
- Modify: `tests/test_single_server_backend.cpp`
- Modify: `docs/upgrade/upgrade-log.md`

- [ ] **Step 1: Add failing template/request validation tests**

Append to `tests/test_single_server_backend.cpp`:

```cpp
namespace {

SingleServerJobTemplate make_template() {
    SingleServerJobTemplate job_template;
    job_template.template_id = "fwi_multiscale_review";
    job_template.version = "1";
    job_template.profile_id = "single-server-dev";
    job_template.entrypoint_label = "fwi_multiscale_sanity_check";
    job_template.allowed_parameter_names = {
        "dataset_id",
        "niter",
        "frequency_band",
        "gpu_count",
    };
    job_template.expected_artifacts = {
        "loss_curve",
        "final_velocity_model",
    };
    job_template.max_gpus = 1;
    job_template.max_mpi_ranks = 4;
    job_template.max_wall_time_minutes = 60;
    return job_template;
}

SingleServerReviewRequest make_request() {
    SingleServerReviewRequest request;
    request.request_id = "req-single-server-001";
    request.user_id = "researcher-a";
    request.profile_id = "single-server-dev";
    request.template_id = "fwi_multiscale_review";
    request.template_version = "1";
    request.parameters = {
        {"dataset_id", "marmousi"},
        {"niter", "20"},
        {"frequency_band", "3-8Hz"},
        {"gpu_count", "1"},
    };
    request.dry_run = true;
    return request;
}

}  // namespace

TEST(SingleServerBackendTest, AcceptsTemplateAllowedByProfile) {
    EXPECT_TRUE(validate_single_server_template(make_profile(), make_template()).empty());
}

TEST(SingleServerBackendTest, RejectsTemplateNotAllowedByProfile) {
    auto profile = make_profile();
    profile.allowed_template_ids = {"other_template"};

    const auto errors = validate_single_server_template(profile, make_template());

    EXPECT_NE(std::find(errors.begin(), errors.end(),
                  "template_id is not allowed by profile"),
        errors.end());
}

TEST(SingleServerBackendTest, RejectsReviewRequestWithUnknownParameter) {
    auto request = make_request();
    request.parameters.push_back({"extra_flags", "--unsafe"});

    const auto errors = validate_single_server_review_request(
        make_profile(),
        make_template(),
        request);

    EXPECT_NE(std::find(errors.begin(), errors.end(),
                  "parameter 'extra_flags' is not allowed by template"),
        errors.end());
}

TEST(SingleServerBackendTest, RejectsNonDryRunReviewRequest) {
    auto request = make_request();
    request.dry_run = false;

    const auto errors = validate_single_server_review_request(
        make_profile(),
        make_template(),
        request);

    EXPECT_NE(std::find(errors.begin(), errors.end(),
                  "single-server review request must remain dry_run"),
        errors.end());
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cmake --build build -j2
ctest --test-dir build -R SingleServerBackendTest --output-on-failure
```

Expected:

```text
The new template/request tests fail because validation functions still return empty errors.
```

- [ ] **Step 3: Implement template/request validation**

Replace the empty validation functions in `research/src/single_server_backend.cpp`:

```cpp
std::vector<std::string> validate_single_server_template(
    const SingleServerProfile& profile,
    const SingleServerJobTemplate& job_template) {
    std::vector<std::string> errors;
    if (job_template.template_id.empty()) {
        errors.push_back("template_id is required");
    }
    if (job_template.version.empty()) {
        errors.push_back("template version is required");
    }
    if (job_template.profile_id.empty()) {
        errors.push_back("template profile_id is required");
    } else if (job_template.profile_id != profile.profile_id) {
        errors.push_back("template profile_id does not match profile");
    }
    if (!contains(profile.allowed_template_ids, job_template.template_id)) {
        errors.push_back("template_id is not allowed by profile");
    }
    if (job_template.entrypoint_label.empty()) {
        errors.push_back("entrypoint_label is required");
    }
    if (job_template.allowed_parameter_names.empty()) {
        errors.push_back("allowed_parameter_names must include at least one parameter");
    }
    if (job_template.max_gpus < 0) {
        errors.push_back("max_gpus must be zero or greater");
    }
    if (job_template.max_mpi_ranks <= 0) {
        errors.push_back("max_mpi_ranks must be greater than zero");
    }
    if (job_template.max_wall_time_minutes <= 0) {
        errors.push_back("max_wall_time_minutes must be greater than zero");
    }
    return errors;
}

std::vector<std::string> validate_single_server_review_request(
    const SingleServerProfile& profile,
    const SingleServerJobTemplate& job_template,
    const SingleServerReviewRequest& request) {
    std::vector<std::string> errors;
    if (request.request_id.empty()) {
        errors.push_back("request_id is required");
    }
    if (request.user_id.empty()) {
        errors.push_back("user_id is required");
    }
    if (request.profile_id != profile.profile_id) {
        errors.push_back("request profile_id does not match profile");
    }
    if (request.template_id != job_template.template_id) {
        errors.push_back("request template_id does not match template");
    }
    if (request.template_version != job_template.version) {
        errors.push_back("request template_version does not match template");
    }
    if (!request.dry_run) {
        errors.push_back("single-server review request must remain dry_run");
    }
    for (const auto& parameter : request.parameters) {
        if (!contains(job_template.allowed_parameter_names, parameter.first)) {
            errors.push_back(
                "parameter '" + parameter.first + "' is not allowed by template");
        }
    }
    return errors;
}
```

- [ ] **Step 4: Run template/request tests**

Run:

```bash
cmake --build build -j2
ctest --test-dir build -R SingleServerBackendTest --output-on-failure
```

Expected:

```text
SingleServerBackendTest passes
```

- [ ] **Step 5: Commit**

```bash
git add research/src/single_server_backend.cpp \
  tests/test_single_server_backend.cpp \
  docs/upgrade/upgrade-log.md
git commit -m "feat: validate single server templates"
```

## Task 4: Dry-Run Review Packet Renderer

**Files:**
- Modify: `research/src/single_server_backend.cpp`
- Modify: `tests/test_single_server_backend.cpp`
- Modify: `docs/upgrade/upgrade-log.md`

- [ ] **Step 1: Add failing renderer test**

Append to `tests/test_single_server_backend.cpp`:

```cpp
TEST(SingleServerBackendTest, RendersDryRunReviewPacketWithoutSecretsOrExecution) {
    const auto rendered = render_single_server_review_packet(
        make_profile(),
        make_template(),
        make_request());

    EXPECT_NE(rendered.find("Single Server Dry-Run Review Packet"), std::string::npos);
    EXPECT_NE(rendered.find("request_id: req-single-server-001"), std::string::npos);
    EXPECT_NE(rendered.find("user_id: researcher-a"), std::string::npos);
    EXPECT_NE(rendered.find("profile_id: single-server-dev"), std::string::npos);
    EXPECT_NE(rendered.find("account_reference: lab-single-server-account"), std::string::npos);
    EXPECT_NE(rendered.find("workspace_root_reference: workspace-ref:single-server-runs"), std::string::npos);
    EXPECT_NE(rendered.find("template: fwi_multiscale_review@1"), std::string::npos);
    EXPECT_NE(rendered.find("entrypoint_label: fwi_multiscale_sanity_check"), std::string::npos);
    EXPECT_NE(rendered.find("execution: disabled"), std::string::npos);
    EXPECT_NE(rendered.find("credentials_loaded: false"), std::string::npos);
    EXPECT_NE(rendered.find("server_connection: disabled"), std::string::npos);
    EXPECT_NE(rendered.find("workspace_created: false"), std::string::npos);
    EXPECT_NE(rendered.find("- dataset_id=marmousi"), std::string::npos);
    EXPECT_NE(rendered.find("- loss_curve"), std::string::npos);
    EXPECT_EQ(rendered.find("secret-ref:single-server-runner"), std::string::npos);
}
```

- [ ] **Step 2: Run renderer test to verify it fails**

Run:

```bash
cmake --build build -j2
ctest --test-dir build -R SingleServerBackendTest --output-on-failure
```

Expected:

```text
The renderer test fails because render_single_server_review_packet returns an empty string.
```

- [ ] **Step 3: Implement renderer**

Replace `render_single_server_review_packet` in `research/src/single_server_backend.cpp`:

```cpp
std::string render_single_server_review_packet(
    const SingleServerProfile& profile,
    const SingleServerJobTemplate& job_template,
    const SingleServerReviewRequest& request) {
    std::ostringstream out;
    out << "Single Server Dry-Run Review Packet\n";
    out << "request_id: " << request.request_id << "\n";
    out << "user_id: " << request.user_id << "\n";
    out << "profile_id: " << profile.profile_id << "\n";
    out << "profile_display_name: " << profile.display_name << "\n";
    out << "account_reference: " << profile.account_reference << "\n";
    out << "workspace_root_reference: " << profile.workspace_root_reference << "\n";
    out << "template: " << job_template.template_id
        << "@" << job_template.version << "\n";
    out << "entrypoint_label: " << job_template.entrypoint_label << "\n";
    out << "execution: disabled\n";
    out << "credentials_loaded: false\n";
    out << "server_connection: disabled\n";
    out << "workspace_created: false\n";
    out << "parameters:\n";
    for (const auto& parameter : request.parameters) {
        out << "- " << parameter.first << "=" << parameter.second << "\n";
    }
    out << "expected_artifacts:\n";
    for (const auto& artifact : job_template.expected_artifacts) {
        out << "- " << artifact << "\n";
    }
    out << "resource_limits:\n";
    out << "- max_gpus=" << job_template.max_gpus << "\n";
    out << "- max_mpi_ranks=" << job_template.max_mpi_ranks << "\n";
    out << "- max_wall_time_minutes=" << job_template.max_wall_time_minutes << "\n";
    out << "safety_boundary: review packet only; no command is submitted or executed\n";
    return out.str();
}
```

- [ ] **Step 4: Run renderer tests and full tests**

Run:

```bash
cmake --build build -j2
ctest --test-dir build -R SingleServerBackendTest --output-on-failure
ctest --test-dir build --output-on-failure
git diff --check
```

Expected:

```text
cmake build exits 0
SingleServerBackendTest passes
full ctest reports 100% tests passed
git diff --check produces no output
```

- [ ] **Step 5: Commit**

```bash
git add research/src/single_server_backend.cpp \
  tests/test_single_server_backend.cpp \
  docs/upgrade/upgrade-log.md
git commit -m "feat: render single server review packets"
```

## Task 5: v0.10 Test Report And Learning Summary

**Files:**
- Create: `docs/upgrade/test-report-v0.10.md`
- Create: `docs/upgrade/learning-summary-v0.10.md`
- Modify: `docs/upgrade/README.md`
- Modify: `docs/upgrade/milestones.md`
- Modify: `docs/upgrade/version-roadmap.md`
- Modify: `docs/upgrade/career-notes.md`
- Modify: `docs/upgrade/upgrade-log.md`

- [ ] **Step 1: Write the test report**

Document:

- scope
- tests added
- RED/GREEN evidence
- final validation commands
- explicit safety boundaries

- [ ] **Step 2: Write the Chinese learning summary**

Cover:

- why single-server account preparation comes before fake lifecycle and real connection
- profile/template/review packet data flow
- why credential references are not credentials
- why review packet is not executable
- interview pitch and likely questions

- [ ] **Step 3: Update upgrade docs**

Mark v0.10 first implementation batch complete only after tests pass. Do not claim server execution capability.

- [ ] **Step 4: Final validation**

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

- [ ] **Step 5: Commit**

```bash
git add docs/upgrade/test-report-v0.10.md \
  docs/upgrade/learning-summary-v0.10.md \
  docs/upgrade/README.md \
  docs/upgrade/milestones.md \
  docs/upgrade/version-roadmap.md \
  docs/upgrade/career-notes.md \
  docs/upgrade/upgrade-log.md
git commit -m "docs: complete single server backend preparation"
```
