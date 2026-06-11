#include "agent_rpc/research/algorithm_listing_tool.h"

namespace agent_rpc::research {

json list_algorithms_for_tool(const AlgorithmRegistry& registry) {
    json algorithms = json::array();

    for (const auto& card : registry.cards()) {
        algorithms.push_back({
            {"id", card.id},
            {"name", card.name},
            {"domain", card.domain},
            {"tags", card.tags},
            {"backend", card.backend},
            {"job_spec_supported", card.job_spec_supported}
        });
    }

    return {
        {"tool", "list_algorithms"},
        {"count", algorithms.size()},
        {"algorithms", algorithms}
    };
}

}  // namespace agent_rpc::research
