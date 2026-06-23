#include "agent_rpc/research/internal_sanity_runner.h"

#include <sstream>

namespace agent_rpc::research {
namespace {

const SanityRunnerDefinition* find_definition(
    const std::string& runner_id,
    const std::vector<SanityRunnerDefinition>& definitions) {
    for (const auto& definition : definitions) {
        if (definition.runner_id == runner_id) {
            return &definition;
        }
    }
    return nullptr;
}

bool contains_traversal(const std::string& path) {
    return path == ".." || path.rfind("../", 0) == 0 ||
        path.find("/../") != std::string::npos ||
        (path.size() >= 3 && path.compare(path.size() - 3, 3, "/..") == 0);
}

bool is_under_workspace_root(const std::string& root, const std::string& path) {
    if (root.empty() || path.empty()) {
        return false;
    }
    std::string prefix = root;
    if (prefix.back() != '/') {
        prefix.push_back('/');
    }
    return path.rfind(prefix, 0) == 0;
}

void validate_definition(const SanityRunnerDefinition& definition,
                         std::vector<std::string>& errors) {
    if (definition.timeout_seconds <= 0) {
        errors.push_back("runner timeout must be positive");
    }
    if (!definition.captures_stdout) {
        errors.push_back("stdout capture must be planned");
    }
    if (!definition.captures_stderr) {
        errors.push_back("stderr capture must be planned");
    }
    if (definition.fixed_entrypoint_label.empty()) {
        errors.push_back("fixed entrypoint label is required");
    }
}

void validate_request(const SanityRunnerRequest& request,
                      std::vector<std::string>& errors) {
    if (request.request_id.empty()) {
        errors.push_back("request_id is required");
    }
    if (request.user_id.empty()) {
        errors.push_back("user_id is required");
    }
    if (request.workspace_plan_id.empty()) {
        errors.push_back("workspace_plan_id is required");
    }
    if (request.workspace_root_path.empty()) {
        errors.push_back("workspace_root_path is required");
    }
    if (request.planned_artifact_paths.empty()) {
        errors.push_back("planned artifact path is required");
    }

    for (const auto& path : request.planned_artifact_paths) {
        if (contains_traversal(path)) {
            errors.push_back("artifact path must not contain traversal");
        }
        if (!is_under_workspace_root(request.workspace_root_path, path)) {
            errors.push_back("artifact path must stay under workspace root");
        }
    }

    if (!request.free_form_command.empty()) {
        errors.push_back(
            "free_form_command is not accepted by sanity runner gate");
    }
    if (request.deletion_requested) {
        errors.push_back(
            "deletion request is not accepted by sanity runner gate");
    }
    if (request.credential_read_requested) {
        errors.push_back(
            "credential read is not accepted by sanity runner gate");
    }
    if (request.ssh_requested) {
        errors.push_back(
            "ssh access is not accepted by sanity runner gate");
    }
    if (request.slurm_requested) {
        errors.push_back(
            "slurm submission is not accepted by sanity runner gate");
    }
    if (request.pbs_requested) {
        errors.push_back(
            "pbs submission is not accepted by sanity runner gate");
    }
    if (request.remote_server_access_requested) {
        errors.push_back(
            "remote server access is not accepted by sanity runner gate");
    }
}

void render_list(std::ostringstream& out,
                 const std::vector<std::string>& values) {
    if (values.empty()) {
        out << "- none\n";
        return;
    }
    for (const auto& value : values) {
        out << "- " << value << "\n";
    }
}

}  // namespace

SanityRunnerReviewPacket make_sanity_runner_review_packet(
    const SanityRunnerRequest& request,
    const std::vector<SanityRunnerDefinition>& allowlisted_definitions) {
    SanityRunnerReviewPacket packet;
    packet.request = request;
    packet.planned_artifact_paths = request.planned_artifact_paths;

    const auto* definition =
        find_definition(request.runner_id, allowlisted_definitions);
    if (definition == nullptr) {
        packet.validation_errors.push_back("unknown sanity runner id");
    } else {
        packet.definition = *definition;
        packet.timeout_seconds = definition->timeout_seconds;
        packet.stdout_capture_planned = definition->captures_stdout;
        packet.stderr_capture_planned = definition->captures_stderr;
        validate_definition(*definition, packet.validation_errors);
    }

    validate_request(request, packet.validation_errors);

    packet.audit_event_type = "sanity_runner_review_packet";
    packet.execution_enabled = false;
    packet.command_executed = false;
    packet.free_form_command_accepted = false;
    packet.deletion_executed = false;
    packet.credentials_loaded = false;
    packet.server_connected = false;
    packet.ssh_connected = false;
    packet.slurm_submitted = false;
    packet.pbs_submitted = false;
    packet.workspace_created = false;
    return packet;
}

std::string render_sanity_runner_review_packet(
    const SanityRunnerReviewPacket& packet) {
    std::ostringstream out;
    out << "Internal Sanity Runner Review Packet\n";
    out << "request_id: " << packet.request.request_id << "\n";
    out << "user_id: " << packet.request.user_id << "\n";
    out << "runner_id: " << packet.request.runner_id << "\n";
    out << "runner_display_name: " << packet.definition.display_name << "\n";
    out << "fixed_entrypoint_label: "
        << packet.definition.fixed_entrypoint_label << "\n";
    out << "workspace_plan_id: " << packet.request.workspace_plan_id << "\n";
    out << "workspace_root_path: " << packet.request.workspace_root_path << "\n";
    out << "timeout_seconds: " << packet.timeout_seconds << "\n";
    out << "stdout_capture_planned: "
        << (packet.stdout_capture_planned ? "true" : "false") << "\n";
    out << "stderr_capture_planned: "
        << (packet.stderr_capture_planned ? "true" : "false") << "\n";
    out << "audit_event_type: " << packet.audit_event_type << "\n";
    out << "execution: disabled\n";
    out << "execution_enabled: false\n";
    out << "command_executed: false\n";
    out << "free_form_command_accepted: false\n";
    out << "deletion_executed: false\n";
    out << "credentials_loaded: false\n";
    out << "server_connected: false\n";
    out << "ssh_connected: false\n";
    out << "slurm_submitted: false\n";
    out << "pbs_submitted: false\n";
    out << "workspace_created: false\n";
    out << "expected_artifacts:\n";
    render_list(out, packet.definition.expected_artifacts);
    out << "planned_artifact_paths:\n";
    render_list(out, packet.planned_artifact_paths);
    out << "validation_errors:\n";
    render_list(out, packet.validation_errors);
    out << "safety_boundary: fixed sanity runner review packet only; no "
        << "user shell command, deletion, credential loading, SSH, Slurm, PBS, "
        << "remote server access, workspace creation, or command execution\n";
    return out.str();
}

}  // namespace agent_rpc::research
