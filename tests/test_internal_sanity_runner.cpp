#include <gtest/gtest.h>

#include <algorithm>
#include <string>
#include <vector>

#include <agent_rpc/research/internal_sanity_runner.h>

using namespace agent_rpc::research;

namespace {

bool contains_error(const std::vector<std::string>& errors,
                    const std::string& expected) {
    return std::find(errors.begin(), errors.end(), expected) != errors.end();
}

SanityRunnerDefinition make_definition() {
    SanityRunnerDefinition definition;
    definition.runner_id = "echo_version_check";
    definition.display_name = "Echo Version Check";
    definition.fixed_entrypoint_label = "developer-maintained echo version check";
    definition.timeout_seconds = 30;
    definition.captures_stdout = true;
    definition.captures_stderr = true;
    definition.expected_artifacts = {"stdout_log", "stderr_log"};
    return definition;
}

SanityRunnerRequest make_request() {
    SanityRunnerRequest request;
    request.request_id = "sanity-1";
    request.user_id = "researcher-a";
    request.runner_id = "echo_version_check";
    request.workspace_plan_id = "workspace-1";
    request.workspace_root_path = "/lab/workspaces";
    request.planned_artifact_paths = {
        "/lab/workspaces/sanity-1/stdout.log",
        "/lab/workspaces/sanity-1/stderr.log",
    };
    return request;
}

}  // namespace

TEST(InternalSanityRunnerTest, AcceptsAllowlistedRunnerReview) {
    const auto packet =
        make_sanity_runner_review_packet(make_request(), {make_definition()});

    EXPECT_TRUE(packet.validation_errors.empty());
    EXPECT_EQ(packet.definition.runner_id, "echo_version_check");
    EXPECT_EQ(packet.timeout_seconds, 30);
    EXPECT_TRUE(packet.stdout_capture_planned);
    EXPECT_TRUE(packet.stderr_capture_planned);
    EXPECT_EQ(packet.audit_event_type, "sanity_runner_review_packet");
    EXPECT_EQ(packet.planned_artifact_paths.size(), 2U);
    EXPECT_FALSE(packet.execution_enabled);
    EXPECT_FALSE(packet.command_executed);
    EXPECT_FALSE(packet.free_form_command_accepted);
    EXPECT_FALSE(packet.credentials_loaded);
    EXPECT_FALSE(packet.server_connected);
    EXPECT_FALSE(packet.workspace_created);

    const auto rendered = render_sanity_runner_review_packet(packet);
    EXPECT_NE(rendered.find("runner_id: echo_version_check"), std::string::npos);
    EXPECT_NE(rendered.find("timeout_seconds: 30"), std::string::npos);
    EXPECT_NE(rendered.find("stdout_capture_planned: true"), std::string::npos);
    EXPECT_NE(rendered.find("stderr_capture_planned: true"), std::string::npos);
    EXPECT_NE(rendered.find("audit_event_type: sanity_runner_review_packet"),
              std::string::npos);
    EXPECT_NE(rendered.find("execution: disabled"), std::string::npos);
    EXPECT_NE(rendered.find("command_executed: false"), std::string::npos);
}

TEST(InternalSanityRunnerTest, RejectsUnknownRunnerId) {
    auto request = make_request();
    request.request_id = "sanity-2";
    request.runner_id = "unknown";

    const auto packet = make_sanity_runner_review_packet(request, {});

    ASSERT_FALSE(packet.validation_errors.empty());
    EXPECT_TRUE(contains_error(packet.validation_errors,
                               "unknown sanity runner id"));
    EXPECT_FALSE(packet.execution_enabled);
    EXPECT_FALSE(packet.command_executed);
}

TEST(InternalSanityRunnerTest, RejectsFreeFormCommandAndDangerousOperations) {
    auto request = make_request();
    request.free_form_command = "rm -rf /lab/workspaces";
    request.deletion_requested = true;
    request.credential_read_requested = true;
    request.ssh_requested = true;
    request.slurm_requested = true;
    request.pbs_requested = true;
    request.remote_server_access_requested = true;

    const auto packet =
        make_sanity_runner_review_packet(request, {make_definition()});

    EXPECT_TRUE(contains_error(packet.validation_errors,
                               "free_form_command is not accepted by sanity runner gate"));
    EXPECT_TRUE(contains_error(packet.validation_errors,
                               "deletion request is not accepted by sanity runner gate"));
    EXPECT_TRUE(contains_error(packet.validation_errors,
                               "credential read is not accepted by sanity runner gate"));
    EXPECT_TRUE(contains_error(packet.validation_errors,
                               "ssh access is not accepted by sanity runner gate"));
    EXPECT_TRUE(contains_error(packet.validation_errors,
                               "slurm submission is not accepted by sanity runner gate"));
    EXPECT_TRUE(contains_error(packet.validation_errors,
                               "pbs submission is not accepted by sanity runner gate"));
    EXPECT_TRUE(contains_error(packet.validation_errors,
                               "remote server access is not accepted by sanity runner gate"));
    EXPECT_FALSE(packet.free_form_command_accepted);
    EXPECT_FALSE(packet.deletion_executed);
    EXPECT_FALSE(packet.credentials_loaded);
    EXPECT_FALSE(packet.ssh_connected);
    EXPECT_FALSE(packet.slurm_submitted);
    EXPECT_FALSE(packet.pbs_submitted);
    EXPECT_FALSE(packet.server_connected);

    const auto rendered = render_sanity_runner_review_packet(packet);
    EXPECT_EQ(rendered.find("rm -rf"), std::string::npos);
}

TEST(InternalSanityRunnerTest, RejectsArtifactPathOutsideWorkspaceRoot) {
    auto request = make_request();
    request.planned_artifact_paths = {
        "/lab/workspaces/sanity-1/stdout.log",
        "/etc/passwd",
        "/lab/workspaces/sanity-1/../secrets.txt",
    };

    const auto packet =
        make_sanity_runner_review_packet(request, {make_definition()});

    EXPECT_TRUE(contains_error(packet.validation_errors,
                               "artifact path must stay under workspace root"));
    EXPECT_TRUE(contains_error(packet.validation_errors,
                               "artifact path must not contain traversal"));
    EXPECT_FALSE(packet.execution_enabled);
}

TEST(InternalSanityRunnerTest, RejectsRunnerDefinitionWithoutRequiredGateMetadata) {
    auto definition = make_definition();
    definition.timeout_seconds = 0;
    definition.captures_stdout = false;
    definition.captures_stderr = false;

    const auto packet =
        make_sanity_runner_review_packet(make_request(), {definition});

    EXPECT_TRUE(contains_error(packet.validation_errors,
                               "runner timeout must be positive"));
    EXPECT_TRUE(contains_error(packet.validation_errors,
                               "stdout capture must be planned"));
    EXPECT_TRUE(contains_error(packet.validation_errors,
                               "stderr capture must be planned"));
    EXPECT_FALSE(packet.execution_enabled);
}
