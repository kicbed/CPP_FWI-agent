#include <gtest/gtest.h>

#include <agent_rpc/research/approved_template_run_packet.h>

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

SingleServerReviewRequest make_review_request() {
    SingleServerReviewRequest request;
    request.request_id = "run-001";
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

WorkspacePlan make_workspace() {
    WorkspacePlanRequest request;
    request.request_id = "workspace-001";
    request.workspace_root = "/lab/workspaces";
    request.job_directory_name = "job-20260623-0001";
    request.run_directory_name = "run";
    request.log_file_name = "run.log";
    request.artifact_subdirectories = {"logs", "artifacts"};
    return make_workspace_plan(request);
}

ApprovedTemplateRunPacketRequest make_packet_request() {
    ApprovedTemplateRunPacketRequest request;
    request.profile = make_profile();
    request.job_template = make_template();
    request.review_request = make_review_request();
    request.workspace_plan = make_workspace();
    request.lifecycle_id = "job-001";
    request.required_parameter_names = {"dataset_id", "niter", "gpu_count"};
    return request;
}

bool contains_error(const std::vector<std::string>& errors,
                    const std::string& expected) {
    return std::find(errors.begin(), errors.end(), expected) != errors.end();
}

}  // namespace

TEST(ApprovedTemplateRunPacketTest, RendersPacketWithoutExecution) {
    const auto packet = make_approved_template_run_packet(make_packet_request());
    const auto rendered = render_approved_template_run_packet(packet);

    EXPECT_TRUE(packet.validation_errors.empty());
    ASSERT_EQ(packet.accepted_parameters.size(), 4);
    EXPECT_FALSE(packet.execution_enabled);
    EXPECT_FALSE(packet.command_executed);
    EXPECT_FALSE(packet.credentials_loaded);
    EXPECT_FALSE(packet.server_connected);
    EXPECT_FALSE(packet.workspace_created);
    EXPECT_FALSE(packet.directories_created);
    EXPECT_FALSE(packet.files_moved);
    EXPECT_FALSE(packet.free_form_command_accepted);
    EXPECT_NE(rendered.find("Approved Template Run Packet"), std::string::npos);
    EXPECT_NE(rendered.find("request_id: run-001"), std::string::npos);
    EXPECT_NE(rendered.find("user_id: researcher-a"), std::string::npos);
    EXPECT_NE(rendered.find("profile_id: single-server-dev"), std::string::npos);
    EXPECT_NE(rendered.find("template: fwi_multiscale_review@1"),
        std::string::npos);
    EXPECT_NE(rendered.find("entrypoint_label: fwi_multiscale_sanity_check"),
        std::string::npos);
    EXPECT_NE(rendered.find("lifecycle_id: job-001"), std::string::npos);
    EXPECT_NE(rendered.find(
                  "planned_workspace_path: /lab/workspaces/job-20260623-0001"),
        std::string::npos);
    EXPECT_NE(rendered.find(
                  "planned_run_directory_path: /lab/workspaces/job-20260623-0001/run"),
        std::string::npos);
    EXPECT_NE(rendered.find(
                  "planned_log_path: /lab/workspaces/job-20260623-0001/logs/run.log"),
        std::string::npos);
    EXPECT_NE(rendered.find("- /lab/workspaces/job-20260623-0001/artifacts"),
        std::string::npos);
    EXPECT_NE(rendered.find("- dataset_id=marmousi"), std::string::npos);
    EXPECT_NE(rendered.find("- max_gpus=1"), std::string::npos);
    EXPECT_NE(rendered.find("execution: disabled"), std::string::npos);
    EXPECT_NE(rendered.find("command_executed: false"), std::string::npos);
    EXPECT_NE(rendered.find("credentials_loaded: false"), std::string::npos);
    EXPECT_NE(rendered.find("server_connected: false"), std::string::npos);
    EXPECT_NE(rendered.find("workspace_created: false"), std::string::npos);
    EXPECT_NE(rendered.find("directories_created: false"), std::string::npos);
    EXPECT_NE(rendered.find("files_moved: false"), std::string::npos);
    EXPECT_NE(rendered.find("free_form_command_accepted: false"),
        std::string::npos);
    EXPECT_EQ(rendered.find("secret-ref:single-server-runner"),
        std::string::npos);
}

TEST(ApprovedTemplateRunPacketTest, RejectsUnapprovedParameter) {
    auto request = make_packet_request();
    request.review_request.parameters.push_back({"extra_flags", "--unsafe"});

    const auto packet = make_approved_template_run_packet(request);
    const auto rendered = render_approved_template_run_packet(packet);

    EXPECT_TRUE(contains_error(packet.validation_errors,
        "parameter 'extra_flags' is not allowed by template"));
    EXPECT_EQ(packet.accepted_parameters.size(), 4);
    EXPECT_FALSE(packet.command_executed);
    EXPECT_EQ(rendered.find("--unsafe"), std::string::npos);
}

TEST(ApprovedTemplateRunPacketTest, RejectsFreeFormCommand) {
    auto request = make_packet_request();
    request.free_form_command = "mpirun -np 8 ./fwi --unsafe";

    const auto packet = make_approved_template_run_packet(request);
    const auto rendered = render_approved_template_run_packet(packet);

    EXPECT_TRUE(contains_error(packet.validation_errors,
        "free_form_command is not accepted by approved template run packet"));
    EXPECT_FALSE(packet.free_form_command_accepted);
    EXPECT_FALSE(packet.command_executed);
    EXPECT_EQ(rendered.find("mpirun"), std::string::npos);
    EXPECT_EQ(rendered.find("--unsafe"), std::string::npos);
}

TEST(ApprovedTemplateRunPacketTest, RejectsMissingRequiredParameter) {
    auto request = make_packet_request();
    request.review_request.parameters = {
        {"dataset_id", "marmousi"},
        {"gpu_count", "1"},
    };

    const auto packet = make_approved_template_run_packet(request);

    EXPECT_TRUE(contains_error(packet.validation_errors,
        "required parameter 'niter' is missing"));
    EXPECT_FALSE(packet.command_executed);
}

TEST(ApprovedTemplateRunPacketTest, RejectsTemplateProfileMismatch) {
    auto request = make_packet_request();
    request.job_template.profile_id = "other-profile";

    const auto packet = make_approved_template_run_packet(request);

    EXPECT_TRUE(contains_error(packet.validation_errors,
        "template profile_id does not match profile"));
    EXPECT_FALSE(packet.command_executed);
}

TEST(ApprovedTemplateRunPacketTest, CarriesWorkspacePlanValidationErrors) {
    auto request = make_packet_request();
    WorkspacePlanRequest workspace_request;
    workspace_request.request_id = "workspace-bad";
    workspace_request.workspace_root = "/";
    workspace_request.job_directory_name = "job-1";
    workspace_request.run_directory_name = "run";
    workspace_request.log_file_name = "run.log";
    request.workspace_plan = make_workspace_plan(workspace_request);

    const auto packet = make_approved_template_run_packet(request);

    EXPECT_TRUE(contains_error(packet.validation_errors,
        "workspace plan: workspace_root must not be a protected location"));
    EXPECT_FALSE(packet.directories_created);
    EXPECT_FALSE(packet.files_moved);
    EXPECT_FALSE(packet.server_connected);
}
