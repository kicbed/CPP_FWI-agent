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

struct DeleteReviewRequest {
    std::string request_id;
    std::string user_id;
    LabAccountRole role = LabAccountRole::Unknown;
    std::string workspace_root;
    std::string target_path;
    std::string confirmation_phrase;
    std::vector<std::string> affected_file_types;
    bool dry_run = true;
    bool protected_path = false;
    bool code_path = false;
    bool environment_path = false;
    bool credential_path = false;
    bool shared_dataset_path = false;
    bool contains_symlink = false;
};

struct DeleteReviewPacket {
    std::string request_id;
    std::string user_id;
    LabAccountRole role = LabAccountRole::Unknown;
    std::string workspace_root;
    std::string target_path;
    std::vector<std::string> affected_file_types;
    std::vector<std::string> validation_errors;
    bool dry_run = true;
    bool reviewable = false;
    bool deletion_executed = false;
    bool trash_move_executed = false;
    bool shell_executed = false;
    bool protected_path = false;
    bool code_path = false;
    bool environment_path = false;
    bool credential_path = false;
    bool shared_dataset_path = false;
    bool contains_symlink = false;
};

LabAccountRole parse_lab_account_role(const std::string& value);
std::string to_string(LabAccountRole role);
std::string to_string(SafeOperationType operation_type);
std::vector<std::string> validate_safe_operation_request(
    const SafeOperationRequest& request,
    const SafeOperationPolicy& policy);
std::vector<std::string> validate_delete_review_request(
    const DeleteReviewRequest& request);
DeleteReviewPacket build_delete_review_packet(const DeleteReviewRequest& request);
std::string render_delete_review_packet(const DeleteReviewRequest& request);

}  // namespace agent_rpc::research
