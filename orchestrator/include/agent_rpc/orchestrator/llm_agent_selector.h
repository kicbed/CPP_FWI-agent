/**
 * @file llm_agent_selector.h
 * @brief LLMAgentSelector - select Agent using LLM
 *
 * Implements the "A" (Agent selection) in Agent-RAG.
 * Uses LLM to select the most appropriate Agent from candidates.
 *
 * Flow:
 * 1. Construct prompt with candidate AgentCards
 * 2. Call LLM to select
 * 3. Parse LLM output (JSON)
 * 4. Return selected Agent ID
 */

#pragma once

#include "agent_retriever.h"
#include <a2a/examples/qwen_client.hpp>
#include <nlohmann/json.hpp>
#include <string>
#include <sstream>

using json = nlohmann::json;

namespace agent_rpc {
namespace orchestrator {

/**
 * @brief LLM Agent Selector
 *
 * Uses LLM to select the most appropriate Agent from candidates.
 * Part of the Agent-RAG routing system.
 */
class LLMAgentSelector {
public:
    /**
     * @brief Construct with LLM client
     * @param llm_client LLM client (e.g., QwenClient)
     */
    template<typename LLMType>
    explicit LLMAgentSelector(LLMType& llm_client)
        : chat_func_([&llm_client](const std::string& sys, const std::string& user) {
            return llm_client.chat(sys, user);
        }) {}

    /**
     * @brief Select the most appropriate Agent
     * @param query User query
     * @param candidates Candidate Agents (from AgentRetriever)
     * @return Selected Agent ID, or empty string if no suitable Agent
     *
     * Algorithm:
     * 1. Construct prompt with candidate AgentCards
     * 2. Call LLM
     * 3. Parse JSON output
     * 4. Validate and return
     */
    std::string select(const std::string& query,
                      const std::vector<AgentRetrievalResult>& candidates) {
        if (candidates.empty()) {
            return "";
        }

        // If only one candidate, return it directly
        if (candidates.size() == 1) {
            return candidates[0].agent.id;
        }

        // Construct prompt
        std::string prompt = build_prompt(query, candidates);

        try {
            // Call LLM
            std::string response = chat_func_(
                "你是一个 Agent 选择器。根据用户问题，从候选 Agent 中选择最合适的。"
                "只返回 JSON，不要其他内容。",
                prompt
            );

            // Parse response
            return parse_selection(response, candidates);

        } catch (const std::exception& e) {
            // Fallback: return first candidate
            return candidates[0].agent.id;
        }
    }

private:
    /**
     * @brief Build prompt for LLM
     */
    std::string build_prompt(const std::string& query,
                            const std::vector<AgentRetrievalResult>& candidates) {
        std::ostringstream oss;

        oss << "以下是可用的 Agent 列表：\n\n";

        for (size_t i = 0; i < candidates.size(); ++i) {
            const auto& agent = candidates[i].agent;
            oss << (i + 1) << ". Agent ID: " << agent.id << "\n";
            oss << "   名称: " << agent.name << "\n";
            oss << "   描述: " << agent.description << "\n";
            oss << "   标签: ";
            for (size_t j = 0; j < agent.tags.size(); ++j) {
                if (j > 0) oss << ", ";
                oss << agent.tags[j];
            }
            oss << "\n";

            if (!agent.skills.empty()) {
                oss << "   技能:\n";
                for (const auto& skill : agent.skills) {
                    oss << "     - " << skill.name << ": " << skill.description << "\n";
                    if (!skill.input_examples.empty()) {
                        oss << "       示例: ";
                        for (size_t j = 0; j < skill.input_examples.size(); ++j) {
                            if (j > 0) oss << ", ";
                            oss << skill.input_examples[j];
                        }
                        oss << "\n";
                    }
                }
            }

            oss << "   相关度: " << candidates[i].relevance_score << "\n";
            oss << "   匹配原因: " << candidates[i].match_reason << "\n\n";
        }

        oss << "用户问题: " << query << "\n\n";
        oss << "请选择最合适的 Agent。返回 JSON 格式:\n";
        oss << "{\"agent_id\": \"选中的Agent ID\", \"reason\": \"选择原因\"}\n";

        return oss.str();
    }

    /**
     * @brief Parse LLM selection response
     */
    std::string parse_selection(const std::string& response,
                               const std::vector<AgentRetrievalResult>& candidates) {
        try {
            // Try to parse as JSON
            auto j = json::parse(response);

            if (j.contains("agent_id")) {
                std::string agent_id = j["agent_id"].get<std::string>();

                // Validate that the selected Agent is in candidates
                for (const auto& candidate : candidates) {
                    if (candidate.agent.id == agent_id) {
                        return agent_id;
                    }
                }
            }

            // If validation fails, return first candidate
            return candidates[0].agent.id;

        } catch (const json::exception& e) {
            // If JSON parsing fails, try to extract agent_id from text
            // Look for patterns like "agent_id": "xxx" or agent_id: xxx
            size_t pos = response.find("agent_id");
            if (pos != std::string::npos) {
                size_t start = response.find("\"", pos);
                if (start != std::string::npos) {
                    start++;
                    size_t end = response.find("\"", start);
                    if (end != std::string::npos) {
                        std::string agent_id = response.substr(start, end - start);

                        // Validate
                        for (const auto& candidate : candidates) {
                            if (candidate.agent.id == agent_id) {
                                return agent_id;
                            }
                        }
                    }
                }
            }

            // Fallback: return first candidate
            return candidates[0].agent.id;
        }
    }

    std::function<std::string(const std::string&, const std::string&)> chat_func_;
};

} // namespace orchestrator
} // namespace agent_rpc
