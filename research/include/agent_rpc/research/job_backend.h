#pragma once

#include "agent_rpc/research/job_spec.h"

#include <string>
#include <vector>

namespace agent_rpc::research {

class DryRunBackend {
public:
    std::vector<std::string> validate(const JobSpec& job) const;
    std::string render(const JobSpec& job) const;
    std::string explain(const JobSpec& job) const;
};

}  // namespace agent_rpc::research
