#include "agent_rpc/research/experiment_spec.h"

namespace agent_rpc::research {

std::vector<std::string> ExperimentSpec::validate() const {
    std::vector<std::string> errors;

    if (algorithm_id.empty()) errors.push_back("algorithm_id is required");
    if (dataset_id.empty()) errors.push_back("dataset_id is required");
    if (resources.gpu_count < 0) errors.push_back("gpu_count must be >= 0");
    if (resources.mpi_processes < 1) errors.push_back("mpi_processes must be >= 1");

    return errors;
}

}  // namespace agent_rpc::research
