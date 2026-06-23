#include "agent_rpc/research/single_server_backend.h"

#include <algorithm>
#include <sstream>

namespace agent_rpc::research {
namespace {

bool contains(const std::vector<std::string>& values, const std::string& value) {
    return std::find(values.begin(), values.end(), value) != values.end();
}

bool looks_like_inline_secret(const std::string& value) {
    return value.find("password=") != std::string::npos ||
           value.find("token=") != std::string::npos ||
           value.find("-----BEGIN") != std::string::npos ||
           value.find("PRIVATE KEY") != std::string::npos;
}

}  // namespace

std::vector<std::string> validate_single_server_profile(
    const SingleServerProfile& profile) {
    std::vector<std::string> errors;
    if (profile.profile_id.empty()) {
        errors.push_back("profile_id is required");
    }
    if (profile.account_reference.empty()) {
        errors.push_back("account_reference is required");
    }
    if (profile.credential_reference.empty()) {
        errors.push_back("credential_reference is required");
    } else if (looks_like_inline_secret(profile.credential_reference)) {
        errors.push_back(
            "credential_reference must be a reference name, not an inline secret");
    }
    if (profile.workspace_root_reference.empty()) {
        errors.push_back("workspace_root_reference is required");
    }
    if (profile.allowed_template_ids.empty()) {
        errors.push_back("allowed_template_ids must include at least one template");
    }
    if (profile.runtime_enabled) {
        errors.push_back("single-server runtime execution is not enabled");
    }
    return errors;
}

std::vector<std::string> validate_single_server_template(
    const SingleServerProfile& profile,
    const SingleServerJobTemplate& job_template) {
    std::vector<std::string> errors;
    if (job_template.template_id.empty()) {
        errors.push_back("template_id is required");
    }
    if (job_template.version.empty()) {
        errors.push_back("template version is required");
    }
    if (job_template.profile_id.empty()) {
        errors.push_back("template profile_id is required");
    } else if (job_template.profile_id != profile.profile_id) {
        errors.push_back("template profile_id does not match profile");
    }
    if (!contains(profile.allowed_template_ids, job_template.template_id)) {
        errors.push_back("template_id is not allowed by profile");
    }
    if (job_template.entrypoint_label.empty()) {
        errors.push_back("entrypoint_label is required");
    }
    if (job_template.allowed_parameter_names.empty()) {
        errors.push_back("allowed_parameter_names must include at least one parameter");
    }
    if (job_template.max_gpus < 0) {
        errors.push_back("max_gpus must be zero or greater");
    }
    if (job_template.max_mpi_ranks <= 0) {
        errors.push_back("max_mpi_ranks must be greater than zero");
    }
    if (job_template.max_wall_time_minutes <= 0) {
        errors.push_back("max_wall_time_minutes must be greater than zero");
    }
    return errors;
}

std::vector<std::string> validate_single_server_review_request(
    const SingleServerProfile& profile,
    const SingleServerJobTemplate& job_template,
    const SingleServerReviewRequest& request) {
    std::vector<std::string> errors;
    if (request.request_id.empty()) {
        errors.push_back("request_id is required");
    }
    if (request.user_id.empty()) {
        errors.push_back("user_id is required");
    }
    if (request.profile_id != profile.profile_id) {
        errors.push_back("request profile_id does not match profile");
    }
    if (request.template_id != job_template.template_id) {
        errors.push_back("request template_id does not match template");
    }
    if (request.template_version != job_template.version) {
        errors.push_back("request template_version does not match template");
    }
    if (!request.dry_run) {
        errors.push_back("single-server review request must remain dry_run");
    }
    for (const auto& parameter : request.parameters) {
        if (!contains(job_template.allowed_parameter_names, parameter.first)) {
            errors.push_back(
                "parameter '" + parameter.first + "' is not allowed by template");
        }
    }
    return errors;
}

std::string render_single_server_review_packet(
    const SingleServerProfile& profile,
    const SingleServerJobTemplate& job_template,
    const SingleServerReviewRequest& request) {
    std::ostringstream out;
    out << "Single Server Dry-Run Review Packet\n";
    out << "request_id: " << request.request_id << "\n";
    out << "user_id: " << request.user_id << "\n";
    out << "profile_id: " << profile.profile_id << "\n";
    out << "profile_display_name: " << profile.display_name << "\n";
    out << "account_reference: " << profile.account_reference << "\n";
    out << "workspace_root_reference: " << profile.workspace_root_reference << "\n";
    out << "template: " << job_template.template_id
        << "@" << job_template.version << "\n";
    out << "entrypoint_label: " << job_template.entrypoint_label << "\n";
    out << "execution: disabled\n";
    out << "credentials_loaded: false\n";
    out << "server_connection: disabled\n";
    out << "workspace_created: false\n";
    out << "parameters:\n";
    for (const auto& parameter : request.parameters) {
        out << "- " << parameter.first << "=" << parameter.second << "\n";
    }
    out << "expected_artifacts:\n";
    for (const auto& artifact : job_template.expected_artifacts) {
        out << "- " << artifact << "\n";
    }
    out << "resource_limits:\n";
    out << "- max_gpus=" << job_template.max_gpus << "\n";
    out << "- max_mpi_ranks=" << job_template.max_mpi_ranks << "\n";
    out << "- max_wall_time_minutes=" << job_template.max_wall_time_minutes << "\n";
    out << "safety_boundary: review packet only; no command is submitted or executed\n";
    return out.str();
}

}  // namespace agent_rpc::research
