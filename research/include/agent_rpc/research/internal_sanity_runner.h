#pragma once

#include <string>
#include <vector>

namespace agent_rpc::research {

struct SanityRunnerDefinition {
    std::string runner_id;
    std::string display_name;
    std::string fixed_entrypoint_label;
    int timeout_seconds = 0;
    bool captures_stdout = false;
    bool captures_stderr = false;
    std::vector<std::string> expected_artifacts;
};

struct SanityRunnerRequest {
    std::string request_id;
    std::string user_id;
    std::string runner_id;
    std::string workspace_plan_id;
    std::string workspace_root_path;
    std::vector<std::string> planned_artifact_paths;
    std::string free_form_command;
    bool deletion_requested = false;
    bool credential_read_requested = false;
    bool ssh_requested = false;
    bool slurm_requested = false;
    bool pbs_requested = false;
    bool remote_server_access_requested = false;
};

struct SanityRunnerReviewPacket {
    SanityRunnerRequest request;
    SanityRunnerDefinition definition;
    std::vector<std::string> validation_errors;
    std::vector<std::string> planned_artifact_paths;
    std::string audit_event_type = "sanity_runner_review_packet";
    int timeout_seconds = 0;
    bool stdout_capture_planned = false;
    bool stderr_capture_planned = false;
    bool execution_enabled = false;
    bool command_executed = false;
    bool free_form_command_accepted = false;
    bool deletion_executed = false;
    bool credentials_loaded = false;
    bool server_connected = false;
    bool ssh_connected = false;
    bool slurm_submitted = false;
    bool pbs_submitted = false;
    bool workspace_created = false;
};

SanityRunnerReviewPacket make_sanity_runner_review_packet(
    const SanityRunnerRequest& request,
    const std::vector<SanityRunnerDefinition>& allowlisted_definitions);
std::string render_sanity_runner_review_packet(
    const SanityRunnerReviewPacket& packet);

}  // namespace agent_rpc::research
