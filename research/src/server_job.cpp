#include "agent_rpc/research/server_job.h"

#include <algorithm>
#include <cctype>

namespace agent_rpc::research {
namespace {

std::string normalized_approval_value(const std::string& value) {
    const auto begin = std::find_if_not(
        value.begin(),
        value.end(),
        [](unsigned char ch) { return std::isspace(ch); });
    const auto end = std::find_if_not(
        value.rbegin(),
        value.rend(),
        [](unsigned char ch) { return std::isspace(ch); }).base();

    if (begin >= end) {
        return "";
    }

    std::string normalized(begin, end);
    std::transform(
        normalized.begin(),
        normalized.end(),
        normalized.begin(),
        [](unsigned char ch) {
            return static_cast<char>(std::tolower(ch));
        });
    return normalized;
}

bool is_placeholder_approval_value(const std::string& value) {
    const auto normalized = normalized_approval_value(value);
    return normalized == "tbd" ||
           normalized == "todo" ||
           normalized == "pending" ||
           normalized == "unknown" ||
           normalized == "n/a" ||
           normalized == "na" ||
           normalized == "none";
}

void require_concrete_approval_value(
    const std::string& field_name,
    const std::string& value,
    std::vector<std::string>& errors) {
    if (normalized_approval_value(value).empty()) {
        errors.push_back(field_name + " is required");
        return;
    }
    if (is_placeholder_approval_value(value)) {
        errors.push_back(field_name + " must be a concrete approval value");
    }
}

}  // namespace

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

std::vector<std::string> validate_workspace_path(
    const std::string& workspace_root,
    const std::string& job_directory_name) {
    std::vector<std::string> errors;
    if (workspace_root.empty()) {
        errors.push_back("workspace root is required");
    }
    if (job_directory_name.empty()) {
        errors.push_back("job directory name is required");
    }
    if (job_directory_name.find("..") != std::string::npos ||
        job_directory_name.find('/') != std::string::npos ||
        job_directory_name.find('\\') != std::string::npos) {
        errors.push_back("workspace path escapes the configured workspace root");
    }
    return errors;
}

std::vector<std::string> validate_backend_approval_decision(
    const BackendApprovalDecision& decision) {
    std::vector<std::string> errors;

    if (decision.backend_type != JobBackendType::Local &&
        decision.backend_type != JobBackendType::Ssh &&
        decision.backend_type != JobBackendType::Slurm &&
        decision.backend_type != JobBackendType::Pbs) {
        errors.push_back(
            "real backend approval must select local, ssh, slurm, or pbs");
    }
    if (!decision.lab_approved) {
        errors.push_back(
            "lab approval is required before selecting a real backend");
    }
    require_concrete_approval_value("approved_by", decision.approved_by, errors);
    require_concrete_approval_value(
        "approval_reference",
        decision.approval_reference,
        errors);
    require_concrete_approval_value("workspace_root", decision.workspace_root, errors);
    require_concrete_approval_value(
        "credential_reference",
        decision.credential_reference,
        errors);
    require_concrete_approval_value(
        "authorization_policy",
        decision.authorization_policy,
        errors);
    if (decision.authorized_submitters.empty()) {
        errors.push_back("authorized_submitters must include at least one submitter");
    } else if (std::any_of(
                   decision.authorized_submitters.begin(),
                   decision.authorized_submitters.end(),
                   [](const std::string& submitter) {
                       return normalized_approval_value(submitter).empty() ||
                              is_placeholder_approval_value(submitter);
                   })) {
        errors.push_back(
            "authorized_submitters must contain only concrete submitter ids");
    }
    require_concrete_approval_value(
        "audit_retention_policy",
        decision.audit_retention_policy,
        errors);
    require_concrete_approval_value(
        "operator_contact",
        decision.operator_contact,
        errors);

    return errors;
}

std::vector<std::string> validate_submitter_authorization(
    const JobSubmissionRequest& request,
    const BackendApprovalDecision& decision) {
    if (std::find(
            decision.authorized_submitters.begin(),
            decision.authorized_submitters.end(),
            request.user_id) != decision.authorized_submitters.end()) {
        return {};
    }

    return {
        "user_id '" + request.user_id +
        "' is not authorized by backend approval decision"};
}

std::vector<std::string> validate_job_audit_event(const JobAuditEvent& event) {
    std::vector<std::string> errors = validate_backend_enabled(event.backend_type);
    if (event.job_id.empty()) {
        errors.push_back("job_id is required");
    }
    if (event.request_id.empty()) {
        errors.push_back("request_id is required");
    }
    if (event.user_id.empty()) {
        errors.push_back("user_id is required");
    }
    if (event.message.empty()) {
        errors.push_back("message is required");
    }
    if (event.timestamp.empty()) {
        errors.push_back("timestamp is required");
    }
    return errors;
}

std::vector<std::string> validate_job_audit_log(const JobAuditLog& log) {
    std::vector<std::string> errors;
    if (log.job_id.empty()) {
        errors.push_back("job_id is required");
    }
    if (log.events.empty()) {
        errors.push_back("job audit log must include at least one event");
    }

    for (const auto& event : log.events) {
        const auto event_errors = validate_job_audit_event(event);
        errors.insert(errors.end(), event_errors.begin(), event_errors.end());
        if (!log.job_id.empty() &&
            !event.job_id.empty() &&
            event.job_id != log.job_id) {
            errors.push_back("audit event job_id must match audit log job_id");
        }
    }
    return errors;
}

std::vector<std::string> append_job_audit_event(
    JobAuditLog& log,
    const JobAuditEvent& event) {
    JobAuditLog candidate = log;
    candidate.events.push_back(event);

    const auto errors = validate_job_audit_log(candidate);
    if (errors.empty()) {
        log.events.push_back(event);
    }
    return errors;
}

BackendPreflightReport evaluate_backend_preflight(
    const BackendPreflightPackage& package) {
    BackendPreflightReport report;
    report.safety_boundaries = {
        "preflight report does not submit jobs",
        "runtime backend guard still controls enablement",
        "audit log is in-memory metadata only",
    };

    const auto approval_errors =
        validate_backend_approval_decision(package.approval);
    report.validation_errors.insert(
        report.validation_errors.end(),
        approval_errors.begin(),
        approval_errors.end());

    const auto authorization_errors =
        validate_submitter_authorization(package.request, package.approval);
    report.validation_errors.insert(
        report.validation_errors.end(),
        authorization_errors.begin(),
        authorization_errors.end());

    const auto submission_errors = validate_submission_boundary(package.request);
    report.validation_errors.insert(
        report.validation_errors.end(),
        submission_errors.begin(),
        submission_errors.end());

    const auto template_errors = validate_approved_template(
        package.request,
        package.approved_templates);
    report.validation_errors.insert(
        report.validation_errors.end(),
        template_errors.begin(),
        template_errors.end());

    const auto workspace_errors = validate_workspace_path(
        package.approval.workspace_root,
        package.job_directory_name);
    report.validation_errors.insert(
        report.validation_errors.end(),
        workspace_errors.begin(),
        workspace_errors.end());

    const auto audit_errors = validate_job_audit_log(package.audit_log);
    report.validation_errors.insert(
        report.validation_errors.end(),
        audit_errors.begin(),
        audit_errors.end());

    report.runtime_blockers = validate_backend_enabled(package.approval.backend_type);
    report.metadata_ready = report.validation_errors.empty();
    report.runtime_enabled = report.runtime_blockers.empty();
    return report;
}

JobAuditEvent make_job_audit_event(
    const std::string& job_id,
    const JobSubmissionRequest& request,
    JobAuditEventType event_type,
    const std::string& message,
    const std::string& timestamp) {
    JobAuditEvent event;
    event.job_id = job_id;
    event.request_id = request.request_id;
    event.user_id = request.user_id;
    event.event_type = event_type;
    event.message = message;
    event.timestamp = timestamp;
    event.backend_type = request.backend_type;
    return event;
}

JobRecord make_rejected_job_record(
    const std::string& job_id,
    const JobSubmissionRequest& request,
    const std::vector<std::string>& validation_messages) {
    JobRecord record;
    record.job_id = job_id;
    record.state = JobLifecycleState::Rejected;
    record.request = request;
    record.validation_messages = validation_messages;
    return record;
}

void append_lifecycle_event(
    JobRecord& record,
    JobLifecycleState next_state,
    const std::string& message) {
    record.state = next_state;
    record.status_events.push_back(to_string(next_state) + ": " + message);
}

}  // namespace agent_rpc::research
