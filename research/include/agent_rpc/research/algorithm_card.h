#pragma once

#include <nlohmann/json.hpp>

#include <string>
#include <vector>

namespace agent_rpc::research {

using json = nlohmann::json;

struct AlgorithmCard {
    std::string id;
    std::string name;
    std::string domain;
    std::string description;
    std::vector<std::string> tags;
    std::vector<std::string> parameters;
    std::vector<std::string> inputs;
    std::vector<std::string> outputs;
    std::vector<std::string> failure_modes;
    bool job_spec_supported = false;
    std::string backend = "dry_run";

    json to_json() const;
    static AlgorithmCard from_json(const json& value);
    std::vector<std::string> validate() const;
    bool is_valid() const;
};

}  // namespace agent_rpc::research
