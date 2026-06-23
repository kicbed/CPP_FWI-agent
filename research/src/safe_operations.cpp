#include "agent_rpc/research/safe_operations.h"

#include <algorithm>
#include <sstream>

namespace agent_rpc::research {
namespace {

bool contains(const std::vector<SafeOperationType>& values, SafeOperationType value) {
    return std::find(values.begin(), values.end(), value) != values.end();
}

bool starts_with(const std::string& value, const std::string& prefix) {
    return value.rfind(prefix, 0) == 0;
}

bool is_forbidden_top_level_path(const std::string& path) {
    return path == "/" ||
           path == "/home" ||
           path == "/data" ||
           path == "/opt" ||
           path == "/usr";
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

std::vector<std::string> validate_delete_review_request(
    const DeleteReviewRequest& request) {
    std::vector<std::string> errors;
    if (request.request_id.empty()) {
        errors.push_back("request_id is required");
    }
    if (request.user_id.empty()) {
        errors.push_back("user_id is required");
    }
    if (request.role == LabAccountRole::ReadOnly ||
        request.role == LabAccountRole::Unknown) {
        errors.push_back("delete review requires lab_user or lab_root role");
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
    if (request.workspace_root.find("..") != std::string::npos ||
        request.target_path.find("..") != std::string::npos) {
        errors.push_back("delete target must not contain path traversal");
    }
    if (!request.workspace_root.empty() &&
        !request.target_path.empty() &&
        request.target_path == request.workspace_root) {
        errors.push_back("delete target must not be the workspace root");
    }
    if (!request.workspace_root.empty() &&
        !request.target_path.empty() &&
        !starts_with(request.target_path, request.workspace_root + "/")) {
        errors.push_back("delete target must stay under workspace root");
    }
    if (is_forbidden_top_level_path(request.target_path)) {
        errors.push_back("delete target is a forbidden system or shared path");
    }
    if (request.protected_path) {
        errors.push_back("delete target is marked as protected");
    }
    if (request.code_path) {
        errors.push_back("delete target is marked as code path");
    }
    if (request.environment_path) {
        errors.push_back("delete target is marked as environment path");
    }
    if (request.credential_path) {
        errors.push_back("delete target is marked as credential path");
    }
    if (request.shared_dataset_path) {
        errors.push_back("delete target is marked as shared dataset path");
    }
    if (request.contains_symlink) {
        errors.push_back("delete review is blocked when target contains symlink");
    }
    if (request.confirmation_phrase != request.target_path) {
        errors.push_back("confirmation phrase must match target_path");
    }
    return errors;
}

DeleteReviewPacket build_delete_review_packet(const DeleteReviewRequest& request) {
    DeleteReviewPacket packet;
    packet.request_id = request.request_id;
    packet.user_id = request.user_id;
    packet.role = request.role;
    packet.workspace_root = request.workspace_root;
    packet.target_path = request.target_path;
    packet.affected_file_types = request.affected_file_types;
    packet.validation_errors = validate_delete_review_request(request);
    packet.dry_run = true;
    packet.reviewable = packet.validation_errors.empty();
    packet.deletion_executed = false;
    packet.trash_move_executed = false;
    packet.shell_executed = false;
    packet.protected_path = request.protected_path;
    packet.code_path = request.code_path;
    packet.environment_path = request.environment_path;
    packet.credential_path = request.credential_path;
    packet.shared_dataset_path = request.shared_dataset_path;
    packet.contains_symlink = request.contains_symlink;
    return packet;
}

std::string render_delete_review_packet(const DeleteReviewRequest& request) {
    const auto packet = build_delete_review_packet(request);
    std::ostringstream out;
    out << "Delete Dry-Run Review Packet\n";
    out << "request_id: " << packet.request_id << "\n";
    out << "user_id: " << packet.user_id << "\n";
    out << "role: " << to_string(packet.role) << "\n";
    out << "workspace_root: " << packet.workspace_root << "\n";
    out << "target_path: " << packet.target_path << "\n";
    out << "dry_run: true\n";
    out << "deletion_executed: false\n";
    out << "trash_move_executed: false\n";
    out << "shell_executed: false\n";
    out << "protected_path: " << (packet.protected_path ? "true" : "false") << "\n";
    out << "code_path: " << (packet.code_path ? "true" : "false") << "\n";
    out << "environment_path: "
        << (packet.environment_path ? "true" : "false") << "\n";
    out << "credential_path: " << (packet.credential_path ? "true" : "false") << "\n";
    out << "shared_dataset_path: "
        << (packet.shared_dataset_path ? "true" : "false") << "\n";
    out << "contains_symlink: " << (packet.contains_symlink ? "true" : "false") << "\n";
    out << "affected_file_types:\n";
    if (packet.affected_file_types.empty()) {
        out << "- none\n";
    } else {
        for (const auto& file_type : packet.affected_file_types) {
            out << "- " << file_type << "\n";
        }
    }
    out << "review_status: " << (packet.reviewable ? "reviewable" : "blocked") << "\n";
    out << "validation_errors:\n";
    if (packet.validation_errors.empty()) {
        out << "- none\n";
    } else {
        for (const auto& error : packet.validation_errors) {
            out << "- " << error << "\n";
        }
    }
    out << "safety_boundary: delete preview only; no file is removed or moved\n";
    return out.str();
}

}  // namespace agent_rpc::research
