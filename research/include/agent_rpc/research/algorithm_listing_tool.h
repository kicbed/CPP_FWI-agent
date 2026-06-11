#pragma once

#include "agent_rpc/research/algorithm_registry.h"

namespace agent_rpc::research {

json list_algorithms_for_tool(const AlgorithmRegistry& registry);

}  // namespace agent_rpc::research
