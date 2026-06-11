#include "agent_rpc/research/job_spec.h"

namespace agent_rpc::research {

std::vector<std::string> JobSpec::validate() const {
    std::vector<std::string> errors;

    if (command.empty()) errors.push_back("command is required");
    if (working_dir.empty()) errors.push_back("working_dir is required");
    if (gpu_count < 0) errors.push_back("gpu_count must be >= 0");
    if (mpi_processes < 1) errors.push_back("mpi_processes must be >= 1");

    return errors;
}

}  // namespace agent_rpc::research
