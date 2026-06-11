#pragma once

#include <map>
#include <string>
#include <vector>

namespace agent_rpc::research {

struct ResourceRequest {
    int mpi_processes = 1;
    int gpu_count = 0;
    int time_limit_minutes = 60;
};

struct ExperimentSpec {
    std::string algorithm_id;
    std::string dataset_id;
    std::map<std::string, std::string> parameters;
    ResourceRequest resources;
    std::vector<std::string> expected_outputs;

    std::vector<std::string> validate() const;
};

}  // namespace agent_rpc::research
