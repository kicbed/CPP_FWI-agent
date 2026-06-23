#pragma once

#include <string>
#include <vector>

namespace agent_rpc::research {

struct WorkspacePlanRequest {
    std::string request_id;
    std::string workspace_root;
    std::string job_directory_name;
    std::string run_directory_name;
    std::string log_file_name;
    std::vector<std::string> artifact_subdirectories;
};

struct WorkspacePlan {
    std::string request_id;
    std::string workspace_root;
    std::string job_directory_name;
    std::string run_directory_name;
    std::string log_file_name;
    std::string planned_workspace_path;
    std::string planned_run_directory_path;
    std::string planned_log_path;
    std::vector<std::string> planned_artifact_paths;
    std::vector<std::string> validation_errors;
    bool directories_created = false;
    bool files_moved = false;
    bool server_connected = false;
};

std::vector<std::string> validate_workspace_plan_request(
    const WorkspacePlanRequest& request);
WorkspacePlan make_workspace_plan(const WorkspacePlanRequest& request);
std::string render_workspace_plan_preview(const WorkspacePlan& plan);

}  // namespace agent_rpc::research
