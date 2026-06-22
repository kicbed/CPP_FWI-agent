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
