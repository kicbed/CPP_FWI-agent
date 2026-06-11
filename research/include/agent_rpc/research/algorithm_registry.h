#pragma once

#include "agent_rpc/research/algorithm_card.h"

#include <filesystem>
#include <string>
#include <vector>

namespace agent_rpc::research {

class AlgorithmRegistry {
public:
    bool load_from_directory(const std::filesystem::path& directory,
                             std::string* error = nullptr);

    const std::vector<AlgorithmCard>& cards() const;
    const AlgorithmCard* find_by_id(const std::string& id) const;
    std::vector<AlgorithmCard> filter_by_domain(const std::string& domain) const;
    std::vector<AlgorithmCard> filter_by_tag(const std::string& tag) const;

private:
    std::vector<AlgorithmCard> cards_;
};

}  // namespace agent_rpc::research
