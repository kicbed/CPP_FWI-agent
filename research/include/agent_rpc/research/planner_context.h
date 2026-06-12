#pragma once

#include "agent_rpc/research/algorithm_card.h"
#include "agent_rpc/research/algorithm_registry.h"
#include "agent_rpc/research/research_knowledge.h"

#include <string>
#include <vector>

namespace agent_rpc::research {

struct PlannerContextRequest {
    std::string algorithm_tag;
    std::string method;
    std::string dataset;
    std::string failure_mode;
    std::string parameter;
};

struct PlannerContext {
    std::vector<AlgorithmCard> algorithms;
    std::vector<ResearchKnowledgeNote> notes;
    std::vector<std::string> parameter_advice;

    json to_json() const;
    std::string render_prompt_context() const;
};

PlannerContextRequest infer_planner_context_request(
    const std::string& user_request);

PlannerContext build_planner_context(
    const AlgorithmRegistry& algorithms,
    const ResearchKnowledgeBase& knowledge,
    const PlannerContextRequest& request);

}  // namespace agent_rpc::research
