#pragma once

#include "agent_rpc/research/job_spec.h"

#include <string>
#include <vector>

namespace agent_rpc::research {

enum class JobBackendType {
    DryRun,
    Local,
    Ssh,
    Slurm,
    Pbs,
    Unknown,
};

std::string to_string(JobBackendType type);
JobBackendType parse_job_backend_type(const std::string& value);
std::vector<std::string> supported_job_backend_names();
std::vector<std::string> validate_backend_enabled(JobBackendType type);
std::vector<std::string> validate_backend_enabled(const std::string& backend);

class JobBackend {
public:
    virtual ~JobBackend() = default;

    virtual JobBackendType type() const = 0;
    virtual std::vector<std::string> validate(const JobSpec& job) const = 0;
    virtual std::string render(const JobSpec& job) const = 0;
    virtual std::string explain(const JobSpec& job) const = 0;
};

class DryRunBackend : public JobBackend {
public:
    JobBackendType type() const override;
    std::vector<std::string> validate(const JobSpec& job) const override;
    std::string render(const JobSpec& job) const override;
    std::string explain(const JobSpec& job) const override;
};

}  // namespace agent_rpc::research
