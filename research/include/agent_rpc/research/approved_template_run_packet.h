#pragma once

#include "agent_rpc/research/single_server_backend.h"
#include "agent_rpc/research/workspace_planner.h"

#include <string>
#include <utility>
#include <vector>

namespace agent_rpc::research {

struct ApprovedTemplateRunPacketRequest {
    SingleServerProfile profile;
    SingleServerJobTemplate job_template;
    SingleServerReviewRequest review_request;
    WorkspacePlan workspace_plan;
    std::string lifecycle_id;
    std::vector<std::string> required_parameter_names;
    std::string free_form_command;
};

struct ApprovedTemplateRunPacket {
    ApprovedTemplateRunPacketRequest request;
    std::vector<std::pair<std::string, std::string>> accepted_parameters;
    std::vector<std::string> validation_errors;
    bool execution_enabled = false;
    bool command_executed = false;
    bool credentials_loaded = false;
    bool server_connected = false;
    bool workspace_created = false;
    bool directories_created = false;
    bool files_moved = false;
    bool free_form_command_accepted = false;
};

ApprovedTemplateRunPacket make_approved_template_run_packet(
    const ApprovedTemplateRunPacketRequest& request);
std::string render_approved_template_run_packet(
    const ApprovedTemplateRunPacket& packet);

}  // namespace agent_rpc::research
