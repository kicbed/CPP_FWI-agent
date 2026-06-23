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

    EXPECT_NE(std::find(errors.begin(),
                  errors.end(),
                  "operation is not allowed for role"),
        errors.end());
}

TEST(SafeOperationsTest, LabRootStillCannotRequestExecutionDelete) {
    SafeOperationRequest request;
    request.user_id = "root-a";
    request.role = LabAccountRole::LabRoot;
    request.operation_type = SafeOperationType::DeleteWorkspaceExecute;

    const auto errors = validate_safe_operation_request(request, make_policy());

    EXPECT_NE(std::find(errors.begin(),
                  errors.end(),
                  "real deletion is not enabled"),
        errors.end());
}

TEST(SafeOperationsTest, LabUserCanRequestWorkspaceDeleteDryRunPreview) {
    SafeOperationRequest request;
    request.user_id = "researcher-a";
    request.role = LabAccountRole::LabUser;
    request.operation_type = SafeOperationType::DeleteWorkspaceDryRun;

    EXPECT_TRUE(validate_safe_operation_request(request, make_policy()).empty());
}

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

    EXPECT_NE(std::find(errors.begin(),
                  errors.end(),
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

    EXPECT_NE(std::find(errors.begin(),
                  errors.end(),
                  "delete target must not contain path traversal"),
        errors.end());
}

TEST(SafeOperationsTest, LabRootCannotBypassWorkspaceRootProtection) {
    DeleteReviewRequest request;
    request.request_id = "delete-root";
    request.user_id = "root-a";
    request.role = LabAccountRole::LabRoot;
    request.workspace_root = "/lab/workspaces";
    request.target_path = "/lab/workspaces";
    request.confirmation_phrase = "/lab/workspaces";
    request.dry_run = true;

    const auto errors = validate_delete_review_request(request);

    EXPECT_NE(std::find(errors.begin(),
                  errors.end(),
                  "delete target must not be the workspace root"),
        errors.end());
}

TEST(SafeOperationsTest, RejectsProtectedDeleteTargets) {
    DeleteReviewRequest request;
    request.request_id = "delete-protected";
    request.user_id = "root-a";
    request.role = LabAccountRole::LabRoot;
    request.workspace_root = "/lab/workspaces";
    request.target_path = "/lab/workspaces/project/.ssh";
    request.confirmation_phrase = "/lab/workspaces/project/.ssh";
    request.dry_run = true;
    request.protected_path = true;

    const auto errors = validate_delete_review_request(request);

    EXPECT_NE(std::find(errors.begin(),
                  errors.end(),
                  "delete target is marked as protected"),
        errors.end());
}

TEST(SafeOperationsTest, SymlinkDeletePreviewIsBlocked) {
    DeleteReviewRequest request;
    request.request_id = "delete-symlink";
    request.user_id = "researcher-a";
    request.role = LabAccountRole::LabUser;
    request.workspace_root = "/lab/workspaces";
    request.target_path = "/lab/workspaces/job-symlink";
    request.confirmation_phrase = "/lab/workspaces/job-symlink";
    request.dry_run = true;
    request.contains_symlink = true;

    const auto errors = validate_delete_review_request(request);

    EXPECT_NE(std::find(errors.begin(),
                  errors.end(),
                  "delete review is blocked when target contains symlink"),
        errors.end());
}

TEST(SafeOperationsTest, MissingConfirmationKeepsDeleteReviewBlocked) {
    DeleteReviewRequest request;
    request.request_id = "delete-confirm";
    request.user_id = "researcher-a";
    request.role = LabAccountRole::LabUser;
    request.workspace_root = "/lab/workspaces";
    request.target_path = "/lab/workspaces/job-confirm";
    request.confirmation_phrase = "";
    request.dry_run = true;

    const auto errors = validate_delete_review_request(request);

    EXPECT_NE(std::find(errors.begin(),
                  errors.end(),
                  "confirmation phrase must match target_path"),
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
    request.affected_file_types = {"logs", "temporary_artifacts"};
    request.dry_run = true;

    const auto packet = render_delete_review_packet(request);

    EXPECT_NE(packet.find("Delete Dry-Run Review Packet"), std::string::npos);
    EXPECT_NE(packet.find("request_id: delete-3"), std::string::npos);
    EXPECT_NE(packet.find("target_path: /lab/workspaces/job-3"), std::string::npos);
    EXPECT_NE(packet.find("- logs"), std::string::npos);
    EXPECT_NE(packet.find("deletion_executed: false"), std::string::npos);
    EXPECT_NE(packet.find("trash_move_executed: false"), std::string::npos);
    EXPECT_NE(packet.find("shell_executed: false"), std::string::npos);
    EXPECT_NE(packet.find("review_status: reviewable"), std::string::npos);
}

TEST(SafeOperationsTest, BuildsDeleteReviewPacketMetadataWithoutExecutionFlags) {
    DeleteReviewRequest request;
    request.request_id = "delete-metadata";
    request.user_id = "researcher-a";
    request.role = LabAccountRole::LabUser;
    request.workspace_root = "/lab/workspaces";
    request.target_path = "/lab/workspaces/job-metadata";
    request.confirmation_phrase = "/lab/workspaces/job-metadata";
    request.dry_run = true;

    const auto packet = build_delete_review_packet(request);

    EXPECT_TRUE(packet.reviewable);
    EXPECT_FALSE(packet.deletion_executed);
    EXPECT_FALSE(packet.trash_move_executed);
    EXPECT_FALSE(packet.shell_executed);
    EXPECT_TRUE(packet.validation_errors.empty());
}
