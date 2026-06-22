#pragma once

#include "agent_rpc/research/experiment_spec.h"
#include "agent_rpc/research/job_backend.h"
#include "agent_rpc/research/job_spec.h"

#include <string>
#include <vector>

namespace agent_rpc::research {

enum class JobLifecycleState {
    Draft,
    Rejected,
    Queued,
    Submitted,
    Running,
    Succeeded,
    Failed,
    Cancelled,
};

enum class JobAuditEventType {
    SubmissionRequested,
    SubmissionRejected,
    LifecycleChanged,
    ArtifactIndexed,
    OperatorNote,
};

struct JobSubmissionRequest {
    std::string request_id;
    std::string user_id;
    std::string experiment_id;
    JobBackendType backend_type = JobBackendType::DryRun;
    std::string template_id;
    std::string template_version;
    ExperimentSpec experiment;
    JobSpec job;
    bool dry_run = true;
};

struct JobRecord {
    std::string job_id;
    JobLifecycleState state = JobLifecycleState::Draft;
    JobSubmissionRequest request;
    std::string workspace_path;
    std::vector<std::string> validation_messages;
    std::vector<std::string> status_events;
    std::vector<std::string> log_paths;
    std::vector<std::string> artifact_paths;
};

struct JobAuditEvent {
    std::string job_id;
    std::string request_id;
    std::string user_id;
    JobAuditEventType event_type = JobAuditEventType::OperatorNote;
    std::string message;
    std::string timestamp;
    JobBackendType backend_type = JobBackendType::DryRun;
};

struct JobAuditLog {
    std::string job_id;
    std::vector<JobAuditEvent> events;
};

struct ApprovedJobTemplate {
    std::string template_id;
    std::string version;
    JobBackendType backend_type = JobBackendType::DryRun;
    std::vector<std::string> allowed_arguments;
    std::vector<std::string> allowed_input_roots;
    int max_gpus = 0;
    int max_mpi_ranks = 1;
};

struct BackendApprovalDecision {
    JobBackendType backend_type = JobBackendType::Unknown;
    bool lab_approved = false;
    std::string approved_by;
    std::string approval_reference;
    std::string workspace_root;
    std::string credential_reference;
    std::string authorization_policy;
    std::vector<std::string> authorized_submitters;
    std::string audit_retention_policy;
    std::string operator_contact;
};

struct BackendPreflightPackage {
    JobSubmissionRequest request;
    BackendApprovalDecision approval;
    std::vector<ApprovedJobTemplate> approved_templates;
    std::string job_directory_name;
    JobAuditLog audit_log;
};

struct BackendPreflightReport {
    bool metadata_ready = false;
    bool runtime_enabled = false;
    std::vector<std::string> validation_errors;
    std::vector<std::string> runtime_blockers;
    std::vector<std::string> safety_boundaries;
};

std::string to_string(JobLifecycleState state);
JobLifecycleState parse_job_lifecycle_state(const std::string& value);
std::vector<std::string> validate_submission_boundary(
    const JobSubmissionRequest& request);
std::vector<std::string> validate_approved_template(
    const JobSubmissionRequest& request,
    const std::vector<ApprovedJobTemplate>& approved_templates);
std::vector<std::string> validate_workspace_path(
    const std::string& workspace_root,
    const std::string& job_directory_name);
std::vector<std::string> validate_backend_approval_decision(
    const BackendApprovalDecision& decision);
std::vector<std::string> validate_submitter_authorization(
    const JobSubmissionRequest& request,
    const BackendApprovalDecision& decision);
std::vector<std::string> validate_job_audit_event(const JobAuditEvent& event);
std::vector<std::string> validate_job_audit_log(const JobAuditLog& log);
std::vector<std::string> append_job_audit_event(
    JobAuditLog& log,
    const JobAuditEvent& event);
BackendPreflightReport evaluate_backend_preflight(
    const BackendPreflightPackage& package);
JobAuditEvent make_job_audit_event(
    const std::string& job_id,
    const JobSubmissionRequest& request,
    JobAuditEventType event_type,
    const std::string& message,
    const std::string& timestamp);
JobRecord make_rejected_job_record(
    const std::string& job_id,
    const JobSubmissionRequest& request,
    const std::vector<std::string>& validation_messages);
void append_lifecycle_event(
    JobRecord& record,
    JobLifecycleState next_state,
    const std::string& message);

}  // namespace agent_rpc::research
