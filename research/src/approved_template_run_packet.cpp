#include "agent_rpc/research/approved_template_run_packet.h"

#include <algorithm>
#include <sstream>

namespace agent_rpc::research {
namespace {

bool contains(const std::vector<std::string>& values, const std::string& value) {
    return std::find(values.begin(), values.end(), value) != values.end();
}

bool has_parameter(
    const std::vector<std::pair<std::string, std::string>>& parameters,
    const std::string& name) {
    return std::find_if(parameters.begin(),
               parameters.end(),
               [&name](const auto& parameter) {
                   return parameter.first == name;
               }) != parameters.end();
}

void append_errors(std::vector<std::string>& destination,
                   const std::vector<std::string>& source) {
    destination.insert(destination.end(), source.begin(), source.end());
}

}  // namespace

ApprovedTemplateRunPacket make_approved_template_run_packet(
    const ApprovedTemplateRunPacketRequest& request) {
    ApprovedTemplateRunPacket packet;
    packet.request = request;

    append_errors(packet.validation_errors,
        validate_single_server_profile(request.profile));
    append_errors(packet.validation_errors,
        validate_single_server_template(request.profile, request.job_template));
    append_errors(packet.validation_errors,
        validate_single_server_review_request(
            request.profile, request.job_template, request.review_request));

    if (request.lifecycle_id.empty()) {
        packet.validation_errors.push_back("lifecycle_id is required");
    }

    for (const auto& required_name : request.required_parameter_names) {
        if (!has_parameter(request.review_request.parameters, required_name)) {
            packet.validation_errors.push_back(
                "required parameter '" + required_name + "' is missing");
        }
    }

    if (!request.free_form_command.empty()) {
        packet.validation_errors.push_back(
            "free_form_command is not accepted by approved template run packet");
    }

    for (const auto& error : request.workspace_plan.validation_errors) {
        packet.validation_errors.push_back("workspace plan: " + error);
    }
    if (request.workspace_plan.directories_created) {
        packet.validation_errors.push_back(
            "workspace plan must not report created directories");
    }
    if (request.workspace_plan.files_moved) {
        packet.validation_errors.push_back(
            "workspace plan must not report moved files");
    }
    if (request.workspace_plan.server_connected) {
        packet.validation_errors.push_back(
            "workspace plan must not report server connection");
    }

    for (const auto& parameter : request.review_request.parameters) {
        if (contains(request.job_template.allowed_parameter_names, parameter.first)) {
            packet.accepted_parameters.push_back(parameter);
        }
    }

    packet.execution_enabled = false;
    packet.command_executed = false;
    packet.credentials_loaded = false;
    packet.server_connected = false;
    packet.workspace_created = false;
    packet.directories_created = false;
    packet.files_moved = false;
    packet.free_form_command_accepted = false;
    return packet;
}

std::string render_approved_template_run_packet(
    const ApprovedTemplateRunPacket& packet) {
    std::ostringstream out;
    out << "Approved Template Run Packet\n";
    out << "request_id: " << packet.request.review_request.request_id << "\n";
    out << "user_id: " << packet.request.review_request.user_id << "\n";
    out << "profile_id: " << packet.request.profile.profile_id << "\n";
    out << "profile_display_name: " << packet.request.profile.display_name << "\n";
    out << "account_reference: " << packet.request.profile.account_reference << "\n";
    out << "workspace_root_reference: "
        << packet.request.profile.workspace_root_reference << "\n";
    out << "template: " << packet.request.job_template.template_id
        << "@" << packet.request.job_template.version << "\n";
    out << "entrypoint_label: "
        << packet.request.job_template.entrypoint_label << "\n";
    out << "lifecycle_id: " << packet.request.lifecycle_id << "\n";
    out << "workspace_plan_id: " << packet.request.workspace_plan.request_id << "\n";
    out << "planned_workspace_path: "
        << packet.request.workspace_plan.planned_workspace_path << "\n";
    out << "planned_run_directory_path: "
        << packet.request.workspace_plan.planned_run_directory_path << "\n";
    out << "planned_log_path: "
        << packet.request.workspace_plan.planned_log_path << "\n";
    out << "execution: disabled\n";
    out << "execution_enabled: false\n";
    out << "command_executed: false\n";
    out << "credentials_loaded: false\n";
    out << "server_connected: false\n";
    out << "workspace_created: false\n";
    out << "directories_created: false\n";
    out << "files_moved: false\n";
    out << "free_form_command_accepted: false\n";
    out << "parameters:\n";
    if (packet.accepted_parameters.empty()) {
        out << "- none\n";
    } else {
        for (const auto& parameter : packet.accepted_parameters) {
            out << "- " << parameter.first << "=" << parameter.second << "\n";
        }
    }
    out << "planned_artifact_paths:\n";
    if (packet.request.workspace_plan.planned_artifact_paths.empty()) {
        out << "- none\n";
    } else {
        for (const auto& path : packet.request.workspace_plan.planned_artifact_paths) {
            out << "- " << path << "\n";
        }
    }
    out << "expected_artifacts:\n";
    if (packet.request.job_template.expected_artifacts.empty()) {
        out << "- none\n";
    } else {
        for (const auto& artifact : packet.request.job_template.expected_artifacts) {
            out << "- " << artifact << "\n";
        }
    }
    out << "resource_limits:\n";
    out << "- max_gpus=" << packet.request.job_template.max_gpus << "\n";
    out << "- max_mpi_ranks=" << packet.request.job_template.max_mpi_ranks << "\n";
    out << "- max_wall_time_minutes="
        << packet.request.job_template.max_wall_time_minutes << "\n";
    out << "validation_errors:\n";
    if (packet.validation_errors.empty()) {
        out << "- none\n";
    } else {
        for (const auto& error : packet.validation_errors) {
            out << "- " << error << "\n";
        }
    }
    out << "safety_boundary: approved-template review packet only; no command "
        << "execution, credential loading, server connection, workspace "
        << "creation, directory creation, or file movement\n";
    return out.str();
}

}  // namespace agent_rpc::research
