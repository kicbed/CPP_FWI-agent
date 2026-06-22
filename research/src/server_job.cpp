#include "agent_rpc/research/server_job.h"

namespace agent_rpc::research {

std::string to_string(JobLifecycleState state) {
    switch (state) {
        case JobLifecycleState::Draft:
            return "draft";
        case JobLifecycleState::Rejected:
            return "rejected";
        case JobLifecycleState::Queued:
            return "queued";
        case JobLifecycleState::Submitted:
            return "submitted";
        case JobLifecycleState::Running:
            return "running";
        case JobLifecycleState::Succeeded:
            return "succeeded";
        case JobLifecycleState::Failed:
            return "failed";
        case JobLifecycleState::Cancelled:
            return "cancelled";
    }
    return "rejected";
}

JobLifecycleState parse_job_lifecycle_state(const std::string& value) {
    if (value == "draft") {
        return JobLifecycleState::Draft;
    }
    if (value == "queued") {
        return JobLifecycleState::Queued;
    }
    if (value == "submitted") {
        return JobLifecycleState::Submitted;
    }
    if (value == "running") {
        return JobLifecycleState::Running;
    }
    if (value == "succeeded") {
        return JobLifecycleState::Succeeded;
    }
    if (value == "failed") {
        return JobLifecycleState::Failed;
    }
    if (value == "cancelled") {
        return JobLifecycleState::Cancelled;
    }
    return JobLifecycleState::Rejected;
}

std::vector<std::string> validate_submission_boundary(
    const JobSubmissionRequest& request) {
    std::vector<std::string> errors = validate_backend_enabled(request.backend_type);
    if (!request.dry_run) {
        errors.push_back("server execution is not enabled; submission must stay dry_run");
    }
    if (request.template_id.empty()) {
        errors.push_back("template_id is required for any future submission");
    }
    return errors;
}

std::vector<std::string> validate_approved_template(
    const JobSubmissionRequest& request,
    const std::vector<ApprovedJobTemplate>& approved_templates) {
    for (const auto& approved : approved_templates) {
        if (approved.template_id != request.template_id) {
            continue;
        }

        std::vector<std::string> errors;
        if (!request.template_version.empty() &&
            approved.version != request.template_version) {
            errors.push_back("template version mismatch for '" + request.template_id + "'");
        }
        if (approved.backend_type != request.backend_type) {
            errors.push_back("template backend does not match requested backend");
        }
        return errors;
    }

    return {"unknown approved template '" + request.template_id + "'"};
}

}  // namespace agent_rpc::research
