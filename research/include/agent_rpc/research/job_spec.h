#pragma once

#include <map>
#include <string>
#include <vector>

namespace agent_rpc::research {

struct JobSpec {
    std::string command;
    std::string working_dir;
    std::map<std::string, std::string> env;
    int mpi_processes = 1;
    int gpu_count = 0;
    int time_limit_minutes = 60;
    std::vector<std::string> artifact_paths;

    std::vector<std::string> validate() const;
};

}  // namespace agent_rpc::research
