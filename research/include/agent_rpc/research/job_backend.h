#pragma once

#include "agent_rpc/research/job_spec.h"

#include <string>
#include <vector>

namespace agent_rpc::research {

class JobBackend {
public:
    virtual ~JobBackend() = default;

    virtual std::vector<std::string> validate(const JobSpec& job) const = 0;
    virtual std::string render(const JobSpec& job) const = 0;
    virtual std::string explain(const JobSpec& job) const = 0;
};

class DryRunBackend : public JobBackend {
public:
    std::vector<std::string> validate(const JobSpec& job) const override;
    std::string render(const JobSpec& job) const override;
    std::string explain(const JobSpec& job) const override;
};

}  // namespace agent_rpc::research
