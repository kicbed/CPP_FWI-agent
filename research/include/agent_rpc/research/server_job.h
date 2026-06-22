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

struct ApprovedJobTemplate {
    std::string template_id;
    std::string version;
    JobBackendType backend_type = JobBackendType::DryRun;
    std::vector<std::string> allowed_arguments;
    std::vector<std::string> allowed_input_roots;
    int max_gpus = 0;
    int max_mpi_ranks = 1;
};

std::string to_string(JobLifecycleState state);
JobLifecycleState parse_job_lifecycle_state(const std::string& value);
std::vector<std::string> validate_submission_boundary(
    const JobSubmissionRequest& request);
std::vector<std::string> validate_approved_template(
    const JobSubmissionRequest& request,
    const std::vector<ApprovedJobTemplate>& approved_templates);

}  // namespace agent_rpc::research
