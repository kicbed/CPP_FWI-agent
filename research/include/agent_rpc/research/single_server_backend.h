#pragma once

#include <string>
#include <utility>
#include <vector>

namespace agent_rpc::research {

struct SingleServerProfile {
    std::string profile_id;
    std::string display_name;
    std::string account_reference;
    std::string credential_reference;
    std::string workspace_root_reference;
    std::vector<std::string> allowed_template_ids;
    bool runtime_enabled = false;
};

struct SingleServerJobTemplate {
    std::string template_id;
    std::string version;
    std::string profile_id;
    std::string entrypoint_label;
    std::vector<std::string> allowed_parameter_names;
    std::vector<std::string> expected_artifacts;
    int max_gpus = 0;
    int max_mpi_ranks = 1;
    int max_wall_time_minutes = 60;
};

struct SingleServerReviewRequest {
    std::string request_id;
    std::string user_id;
    std::string profile_id;
    std::string template_id;
    std::string template_version;
    std::vector<std::pair<std::string, std::string>> parameters;
    bool dry_run = true;
};

std::vector<std::string> validate_single_server_profile(
    const SingleServerProfile& profile);
std::vector<std::string> validate_single_server_template(
    const SingleServerProfile& profile,
    const SingleServerJobTemplate& job_template);
std::vector<std::string> validate_single_server_review_request(
    const SingleServerProfile& profile,
    const SingleServerJobTemplate& job_template,
    const SingleServerReviewRequest& request);
std::string render_single_server_review_packet(
    const SingleServerProfile& profile,
    const SingleServerJobTemplate& job_template,
    const SingleServerReviewRequest& request);

}  // namespace agent_rpc::research
