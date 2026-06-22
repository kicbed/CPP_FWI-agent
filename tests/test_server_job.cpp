#include <gtest/gtest.h>

#include <agent_rpc/research/server_job.h>

#include <algorithm>

using namespace agent_rpc::research;

namespace {

BackendApprovalDecision make_complete_local_approval() {
    BackendApprovalDecision decision;
    decision.backend_type = JobBackendType::Local;
    decision.lab_approved = true;
    decision.approved_by = "lab-pi";
    decision.approval_reference = "approval-2026-06-22";
    decision.workspace_root = "/lab/workspaces/agent";
    decision.credential_reference = "vault://lab/backend/local";
    decision.authorization_policy = "lab-members-only";
    decision.audit_retention_policy = "retain-180-days";
    decision.operator_contact = "lab-ops";
    return decision;
}

}  // namespace

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

TEST(ServerJobTest, RejectsRealBackendApprovalWithoutLabDecisionInputs) {
    BackendApprovalDecision decision;
    decision.backend_type = JobBackendType::Local;

    const auto errors = validate_backend_approval_decision(decision);

    EXPECT_NE(std::find(errors.begin(), errors.end(),
                  "lab approval is required before selecting a real backend"),
        errors.end());
    EXPECT_NE(std::find(errors.begin(), errors.end(),
                  "approval_reference is required"),
        errors.end());
    EXPECT_NE(std::find(errors.begin(), errors.end(),
                  "workspace_root is required"),
        errors.end());
    EXPECT_NE(std::find(errors.begin(), errors.end(),
                  "authorization_policy is required"),
        errors.end());
}

TEST(ServerJobTest, RejectsDryRunAsRealBackendApprovalDecision) {
    BackendApprovalDecision decision = make_complete_local_approval();
    decision.backend_type = JobBackendType::DryRun;
    decision.authorized_submitters = {"researcher-a"};

    const auto errors = validate_backend_approval_decision(decision);

    EXPECT_NE(std::find(errors.begin(), errors.end(),
                  "real backend approval must select local, ssh, slurm, or pbs"),
        errors.end());
}

TEST(ServerJobTest, RejectsPlaceholderApprovalDecisionValues) {
    BackendApprovalDecision decision;
    decision.backend_type = JobBackendType::Slurm;
    decision.lab_approved = true;
    decision.approved_by = "TBD";
    decision.approval_reference = "pending";
    decision.workspace_root = "unknown";
    decision.credential_reference = "todo";
    decision.authorization_policy = "n/a";
    decision.audit_retention_policy = "none";
    decision.operator_contact = "   ";

    const auto errors = validate_backend_approval_decision(decision);

    EXPECT_NE(std::find(errors.begin(), errors.end(),
                  "approved_by must be a concrete approval value"),
        errors.end());
    EXPECT_NE(std::find(errors.begin(), errors.end(),
                  "approval_reference must be a concrete approval value"),
        errors.end());
    EXPECT_NE(std::find(errors.begin(), errors.end(),
                  "workspace_root must be a concrete approval value"),
        errors.end());
    EXPECT_NE(std::find(errors.begin(), errors.end(),
                  "credential_reference must be a concrete approval value"),
        errors.end());
    EXPECT_NE(std::find(errors.begin(), errors.end(),
                  "authorization_policy must be a concrete approval value"),
        errors.end());
    EXPECT_NE(std::find(errors.begin(), errors.end(),
                  "audit_retention_policy must be a concrete approval value"),
        errors.end());
    EXPECT_NE(std::find(errors.begin(), errors.end(),
                  "operator_contact is required"),
        errors.end());
}

TEST(ServerJobTest, CompleteApprovalRecordDoesNotEnableRuntimeBackend) {
    BackendApprovalDecision decision = make_complete_local_approval();
    decision.authorized_submitters = {"researcher-a"};

    EXPECT_TRUE(validate_backend_approval_decision(decision).empty());

    const auto runtime_errors = validate_backend_enabled(decision.backend_type);
    ASSERT_FALSE(runtime_errors.empty());
    EXPECT_NE(runtime_errors[0].find("only dry_run is enabled"), std::string::npos);
}

TEST(ServerJobTest, RejectsApprovalDecisionWithoutAuthorizedSubmitters) {
    const BackendApprovalDecision decision = make_complete_local_approval();

    const auto errors = validate_backend_approval_decision(decision);

    EXPECT_NE(std::find(errors.begin(), errors.end(),
                  "authorized_submitters must include at least one submitter"),
        errors.end());
}

TEST(ServerJobTest, RejectsPlaceholderAuthorizedSubmitters) {
    BackendApprovalDecision decision = make_complete_local_approval();
    decision.authorized_submitters = {"researcher-a", "pending", "  "};

    const auto errors = validate_backend_approval_decision(decision);

    EXPECT_NE(std::find(errors.begin(), errors.end(),
                  "authorized_submitters must contain only concrete submitter ids"),
        errors.end());
}

TEST(ServerJobTest, RejectsUnauthorizedSubmitterForApprovedBackend) {
    JobSubmissionRequest request;
    request.user_id = "researcher-b";

    BackendApprovalDecision decision = make_complete_local_approval();
    decision.authorized_submitters = {"researcher-a"};

    const auto errors = validate_submitter_authorization(request, decision);

    EXPECT_NE(std::find(errors.begin(), errors.end(),
                  "user_id 'researcher-b' is not authorized by backend approval decision"),
        errors.end());
}

TEST(ServerJobTest, AcceptsAuthorizedSubmitterForApprovedBackend) {
    JobSubmissionRequest request;
    request.user_id = "researcher-a";

    BackendApprovalDecision decision = make_complete_local_approval();
    decision.authorized_submitters = {"researcher-a", "researcher-b"};

    EXPECT_TRUE(validate_submitter_authorization(request, decision).empty());
}

TEST(ServerJobTest, CreatesMetadataOnlyAuditEventFromRequest) {
    JobSubmissionRequest request;
    request.request_id = "req-42";
    request.user_id = "researcher-a";
    request.backend_type = JobBackendType::DryRun;

    const auto event = make_job_audit_event(
        "job-42",
        request,
        JobAuditEventType::SubmissionRejected,
        "dry-run backend only",
        "2026-06-22T12:00:00Z");

    EXPECT_EQ(event.job_id, "job-42");
    EXPECT_EQ(event.request_id, "req-42");
    EXPECT_EQ(event.user_id, "researcher-a");
    EXPECT_EQ(event.event_type, JobAuditEventType::SubmissionRejected);
    EXPECT_EQ(event.message, "dry-run backend only");
    EXPECT_EQ(event.timestamp, "2026-06-22T12:00:00Z");
    EXPECT_EQ(event.backend_type, JobBackendType::DryRun);
    EXPECT_TRUE(validate_job_audit_event(event).empty());
}

TEST(ServerJobTest, RejectsIncompleteAuditEventMetadata) {
    JobAuditEvent event;
    event.backend_type = JobBackendType::DryRun;

    const auto errors = validate_job_audit_event(event);

    EXPECT_NE(std::find(errors.begin(), errors.end(), "job_id is required"),
        errors.end());
    EXPECT_NE(std::find(errors.begin(), errors.end(), "request_id is required"),
        errors.end());
    EXPECT_NE(std::find(errors.begin(), errors.end(), "user_id is required"),
        errors.end());
    EXPECT_NE(std::find(errors.begin(), errors.end(), "message is required"),
        errors.end());
    EXPECT_NE(std::find(errors.begin(), errors.end(), "timestamp is required"),
        errors.end());
}

TEST(ServerJobTest, RejectsRealBackendAuditEventWhileRuntimeIsDryRunOnly) {
    JobAuditEvent event;
    event.job_id = "job-42";
    event.request_id = "req-42";
    event.user_id = "researcher-a";
    event.event_type = JobAuditEventType::SubmissionRequested;
    event.message = "operator requested submission";
    event.timestamp = "2026-06-22T12:00:00Z";
    event.backend_type = JobBackendType::Slurm;

    const auto errors = validate_job_audit_event(event);

    ASSERT_FALSE(errors.empty());
    EXPECT_NE(errors[0].find("only dry_run is enabled"), std::string::npos);
}
