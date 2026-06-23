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

TEST(SingleServerBackendTest, AcceptsMetadataOnlyProfile) {
    const auto profile = make_profile();
    EXPECT_TRUE(validate_single_server_profile(profile).empty());
}

TEST(SingleServerBackendTest, RejectsProfileWithoutCredentialReference) {
    auto profile = make_profile();
    profile.credential_reference.clear();

    const auto errors = validate_single_server_profile(profile);

    EXPECT_NE(std::find(errors.begin(),
                  errors.end(),
                  "credential_reference is required"),
        errors.end());
}

TEST(SingleServerBackendTest, RejectsInlineSecretLookingCredentialReference) {
    auto profile = make_profile();
    profile.credential_reference = "password=inline-secret-marker";

    const auto errors = validate_single_server_profile(profile);

    EXPECT_NE(std::find(errors.begin(),
                  errors.end(),
                  "credential_reference must be a reference name, not an inline secret"),
        errors.end());
}

TEST(SingleServerBackendTest, RejectsRuntimeEnabledProfile) {
    auto profile = make_profile();
    profile.runtime_enabled = true;

    const auto errors = validate_single_server_profile(profile);

    EXPECT_NE(std::find(errors.begin(),
                  errors.end(),
                  "single-server runtime execution is not enabled"),
        errors.end());
}

TEST(SingleServerBackendTest, AcceptsTemplateAllowedByProfile) {
    EXPECT_TRUE(validate_single_server_template(
                    make_profile(),
                    make_template())
                    .empty());
}

TEST(SingleServerBackendTest, RejectsTemplateNotAllowedByProfile) {
    auto profile = make_profile();
    profile.allowed_template_ids = {"other_template"};

    const auto errors = validate_single_server_template(profile, make_template());

    EXPECT_NE(std::find(errors.begin(),
                  errors.end(),
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

    EXPECT_NE(std::find(errors.begin(),
                  errors.end(),
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

    EXPECT_NE(std::find(errors.begin(),
                  errors.end(),
                  "single-server review request must remain dry_run"),
        errors.end());
}

TEST(SingleServerBackendTest, RendersDryRunReviewPacketWithoutSecretsOrExecution) {
    const auto rendered = render_single_server_review_packet(
        make_profile(),
        make_template(),
        make_request());

    EXPECT_NE(rendered.find("Single Server Dry-Run Review Packet"),
        std::string::npos);
    EXPECT_NE(rendered.find("request_id: req-single-server-001"),
        std::string::npos);
    EXPECT_NE(rendered.find("user_id: researcher-a"), std::string::npos);
    EXPECT_NE(rendered.find("profile_id: single-server-dev"), std::string::npos);
    EXPECT_NE(rendered.find("account_reference: lab-single-server-account"),
        std::string::npos);
    EXPECT_NE(rendered.find(
                  "workspace_root_reference: workspace-ref:single-server-runs"),
        std::string::npos);
    EXPECT_NE(rendered.find("template: fwi_multiscale_review@1"),
        std::string::npos);
    EXPECT_NE(rendered.find("entrypoint_label: fwi_multiscale_sanity_check"),
        std::string::npos);
    EXPECT_NE(rendered.find("execution: disabled"), std::string::npos);
    EXPECT_NE(rendered.find("credentials_loaded: false"), std::string::npos);
    EXPECT_NE(rendered.find("server_connection: disabled"), std::string::npos);
    EXPECT_NE(rendered.find("workspace_created: false"), std::string::npos);
    EXPECT_NE(rendered.find("- dataset_id=marmousi"), std::string::npos);
    EXPECT_NE(rendered.find("- loss_curve"), std::string::npos);
    EXPECT_EQ(rendered.find("secret-ref:single-server-runner"), std::string::npos);
}
