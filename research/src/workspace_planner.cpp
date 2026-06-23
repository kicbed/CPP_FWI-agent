#include "agent_rpc/research/workspace_planner.h"

#include <algorithm>
#include <sstream>
#include <string>
#include <vector>

namespace agent_rpc::research {
namespace {

std::string trim_trailing_slashes(std::string value) {
    while (value.size() > 1 && value.back() == '/') {
        value.pop_back();
    }
    return value;
}

bool is_absolute_path(const std::string& path) {
    if (path.empty()) {
        return false;
    }
    return path.front() == '/' || path.front() == '\\' ||
           (path.size() > 1 && path[1] == ':');
}

bool contains_path_traversal(const std::string& value) {
    return value.find("..") != std::string::npos;
}

bool contains_path_separator(const std::string& value) {
    return value.find('/') != std::string::npos ||
           value.find('\\') != std::string::npos;
}

std::vector<std::string> split_segments(const std::string& path) {
    std::vector<std::string> segments;
    std::string current;
    for (const char ch : path) {
        if (ch == '/' || ch == '\\') {
            if (!current.empty()) {
                segments.push_back(current);
                current.clear();
            }
        } else {
            current.push_back(ch);
        }
    }
    if (!current.empty()) {
        segments.push_back(current);
    }
    return segments;
}

bool equals_any(const std::string& value, const std::vector<std::string>& values) {
    return std::find(values.begin(), values.end(), value) != values.end();
}

const std::vector<std::string>& protected_path_labels() {
    static const std::vector<std::string> labels = {
        ".git",
        ".ssh",
        "code",
        "credentials",
        "credential",
        "env",
        "envs",
        "private_keys",
        "repo",
        "repos",
        "secrets",
        "shared_data",
        "shared-datasets",
        "venv",
    };
    return labels;
}

bool is_protected_root(const std::string& root) {
    const auto normalized = trim_trailing_slashes(root);
    const std::vector<std::string> protected_roots = {
        "/",
        "/bin",
        "/boot",
        "/dev",
        "/etc",
        "/lib",
        "/lib64",
        "/proc",
        "/root",
        "/run",
        "/sbin",
        "/sys",
        "/tmp",
        "/usr",
        "/var",
    };
    if (equals_any(normalized, protected_roots)) {
        return true;
    }

    for (const auto& segment : split_segments(normalized)) {
        if (equals_any(segment, protected_path_labels())) {
            return true;
        }
    }
    return false;
}

std::string join_path(const std::string& root, const std::string& child) {
    if (root.empty()) {
        return child;
    }
    if (child.empty()) {
        return trim_trailing_slashes(root);
    }
    return trim_trailing_slashes(root) + "/" + child;
}

void validate_relative_component(const std::string& value,
                                 const std::string& label,
                                 std::vector<std::string>& errors) {
    if (value.empty()) {
        errors.push_back(label + " is required");
        return;
    }
    if (is_absolute_path(value)) {
        errors.push_back(label + " must be relative");
    }
    if (contains_path_traversal(value)) {
        errors.push_back(label + " must not contain path traversal");
    }
    if (contains_path_separator(value)) {
        errors.push_back(label + " must not contain path separators");
    }
    if (equals_any(value, protected_path_labels())) {
        errors.push_back(label + " must not use protected labels");
    }
}

}  // namespace

std::vector<std::string> validate_workspace_plan_request(
    const WorkspacePlanRequest& request) {
    std::vector<std::string> errors;
    if (request.request_id.empty()) {
        errors.push_back("request_id is required");
    }

    if (request.workspace_root.empty()) {
        errors.push_back("workspace_root is required");
    } else {
        if (!is_absolute_path(request.workspace_root)) {
            errors.push_back("workspace_root must be absolute");
        }
        if (contains_path_traversal(request.workspace_root)) {
            errors.push_back("workspace_root must not contain path traversal");
        }
        if (is_protected_root(request.workspace_root)) {
            errors.push_back("workspace_root must not be a protected location");
        }
    }

    validate_relative_component(
        request.job_directory_name, "job directory name", errors);
    validate_relative_component(
        request.run_directory_name, "run directory name", errors);
    validate_relative_component(request.log_file_name, "log file name", errors);

    for (const auto& subdirectory : request.artifact_subdirectories) {
        validate_relative_component(
            subdirectory, "artifact subdirectory", errors);
    }

    return errors;
}

WorkspacePlan make_workspace_plan(const WorkspacePlanRequest& request) {
    WorkspacePlan plan;
    plan.request_id = request.request_id;
    plan.workspace_root = trim_trailing_slashes(request.workspace_root);
    plan.job_directory_name = request.job_directory_name;
    plan.run_directory_name = request.run_directory_name;
    plan.log_file_name = request.log_file_name;
    plan.planned_workspace_path =
        join_path(plan.workspace_root, request.job_directory_name);
    plan.planned_run_directory_path =
        join_path(plan.planned_workspace_path, request.run_directory_name);
    plan.planned_log_path =
        join_path(join_path(plan.planned_workspace_path, "logs"),
            request.log_file_name);
    for (const auto& subdirectory : request.artifact_subdirectories) {
        plan.planned_artifact_paths.push_back(
            join_path(plan.planned_workspace_path, subdirectory));
    }
    plan.validation_errors = validate_workspace_plan_request(request);
    plan.directories_created = false;
    plan.files_moved = false;
    plan.server_connected = false;
    return plan;
}

std::string render_workspace_plan_preview(const WorkspacePlan& plan) {
    std::ostringstream out;
    out << "Workspace Plan Preview\n";
    out << "request_id: " << plan.request_id << "\n";
    out << "workspace_root: " << plan.workspace_root << "\n";
    out << "job_directory_name: " << plan.job_directory_name << "\n";
    out << "run_directory_name: " << plan.run_directory_name << "\n";
    out << "log_file_name: " << plan.log_file_name << "\n";
    out << "planned_workspace_path: " << plan.planned_workspace_path << "\n";
    out << "planned_run_directory_path: " << plan.planned_run_directory_path
        << "\n";
    out << "planned_log_path: " << plan.planned_log_path << "\n";
    out << "directories_created: false\n";
    out << "files_moved: false\n";
    out << "server_connected: false\n";
    out << "planned_artifact_paths:\n";
    if (plan.planned_artifact_paths.empty()) {
        out << "- none\n";
    } else {
        for (const auto& path : plan.planned_artifact_paths) {
            out << "- " << path << "\n";
        }
    }
    out << "validation_errors:\n";
    if (plan.validation_errors.empty()) {
        out << "- none\n";
    } else {
        for (const auto& error : plan.validation_errors) {
            out << "- " << error << "\n";
        }
    }
    out << "safety_boundary: preview only; no directory creation, deletion, "
        << "file movement, server connection, or remote filesystem access\n";
    return out.str();
}

}  // namespace agent_rpc::research
