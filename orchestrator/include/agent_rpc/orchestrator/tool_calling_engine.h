/**
 * @file tool_calling_engine.h
 * @brief ToolCallingEngine - Tool-RAG + LLM Tool Calling
 *
 * Implements intelligent tool selection:
 * 1. Tool-RAG: Retrieve candidate tools based on query
 * 2. LLM Tool Calling: Let LLM select tool and generate arguments
 * 3. Execute: Call MCP tools/call
 * 4. Summarize: LLM summarizes tool results
 *
 * Supports two modes:
 * - rule: Rule-based tool selection (existing)
 * - llm: LLM-based tool selection (new)
 */

#pragma once

#include <agent_rpc/mcp/mcp_agent_integration.h>
#include <a2a/examples/qwen_client.hpp>
#include <nlohmann/json.hpp>
#include <string>
#include <vector>
#include <sstream>

using json = nlohmann::json;

namespace agent_rpc {
namespace orchestrator {

/**
 * @brief Tool call result
 */
struct ToolCallResult {
    bool success;
    std::string tool_name;
    json arguments;
    std::string result;
    std::string error;
};

/**
 * @brief Tool Calling Engine
 *
 * Implements Tool-RAG + LLM Tool Calling.
 * Part of the modern Agent architecture.
 */
class ToolCallingEngine {
public:
    /**
     * @brief Construct with MCP integration and LLM client
     * @param mcp_integration MCP integration for tool access
     * @param llm_client LLM client for tool selection
     */
    template<typename LLMType>
    ToolCallingEngine(agent_rpc::mcp::MCPAgentIntegration* mcp_integration,
                     LLMType& llm_client)
        : mcp_integration_(mcp_integration)
        , chat_func_([&llm_client](const std::string& sys, const std::string& user) {
            return llm_client.chat(sys, user);
        }) {}

    /**
     * @brief Process a query with tool calling
     * @param query User query
     * @return Tool result as string, or empty if no tool needed
     *
     * Flow:
     * 1. Retrieve candidate tools (Tool-RAG)
     * 2. LLM selects tool and generates arguments
     * 3. Execute tool via MCP
     * 4. Return result
     */
    std::string process(const std::string& query) {
        if (!mcp_integration_ || !mcp_integration_->isAvailable()) {
            return "";
        }

        // Step 1: Retrieve candidate tools
        auto candidates = retrieve_tools(query, 5);
        if (candidates.empty()) {
            return "";
        }

        // Step 2: LLM selects tool
        auto tool_call = select_tool(query, candidates);
        if (tool_call.tool_name.empty()) {
            return "";
        }

        // Step 3: Execute tool
        auto result = execute_tool(tool_call);

        return result.result;
    }

    /**
     * @brief Retrieve candidate tools (Tool-RAG)
     * @param query User query
     * @param topK Maximum candidates
     * @return Candidate tools sorted by relevance
     */
    std::vector<agent_rpc::mcp::ToolInfo> retrieve_tools(const std::string& query, int topK = 5) {
        if (!mcp_integration_) return {};

        // Use RAG if available
        if (mcp_integration_->isRAGEnabled()) {
            return mcp_integration_->getRelevantTools(query, topK);
        }

        // Fallback: return all tools
        return mcp_integration_->getAvailableTools();
    }

    /**
     * @brief Let LLM select tool and generate arguments
     * @param query User query
     * @param candidates Candidate tools
     * @return Tool call (tool_name + arguments)
     */
    ToolCallResult select_tool(const std::string& query,
                              const std::vector<agent_rpc::mcp::ToolInfo>& candidates) {
        ToolCallResult result;
        result.success = false;

        if (candidates.empty()) {
            return result;
        }

        // If only one candidate, use it directly
        if (candidates.size() == 1) {
            result.tool_name = candidates[0].name;
            result.arguments = generate_arguments(query, candidates[0]);
            result.success = true;
            return result;
        }

        // Build prompt for LLM
        std::string prompt = build_tool_selection_prompt(query, candidates);

        try {
            // Call LLM
            std::string response = chat_func_(
                "你是一个工具选择器。根据用户问题，从候选工具中选择最合适的，并生成参数。"
                "只返回 JSON，不要其他内容。",
                prompt
            );

            // Parse response
            return parse_tool_call(response, candidates);

        } catch (const std::exception& e) {
            // Fallback: use first candidate
            result.tool_name = candidates[0].name;
            result.arguments = generate_arguments(query, candidates[0]);
            result.success = true;
            return result;
        }
    }

    /**
     * @brief Execute a tool call via MCP
     * @param call Tool call to execute
     * @return Execution result
     */
    ToolCallResult execute_tool(const ToolCallResult& call) {
        ToolCallResult result = call;

        if (!mcp_integration_ || call.tool_name.empty()) {
            result.success = false;
            result.error = "MCP not available or no tool selected";
            return result;
        }

        try {
            auto tool_result = mcp_integration_->callTool(call.tool_name, call.arguments.dump());

            result.success = tool_result.success;
            result.result = tool_result.result;
            result.error = tool_result.error;

        } catch (const std::exception& e) {
            result.success = false;
            result.error = e.what();
        }

        return result;
    }

private:
    /**
     * @brief Build prompt for tool selection
     */
    std::string build_tool_selection_prompt(const std::string& query,
                                           const std::vector<agent_rpc::mcp::ToolInfo>& candidates) {
        std::ostringstream oss;

        oss << "以下是可用的工具列表：\n\n";

        for (size_t i = 0; i < candidates.size(); ++i) {
            oss << (i + 1) << ". 工具名: " << candidates[i].name << "\n";
            oss << "   描述: " << candidates[i].description << "\n";
            if (!candidates[i].input_schema.empty()) {
                oss << "   参数: " << candidates[i].input_schema << "\n";
            }
            oss << "\n";
        }

        oss << "用户问题: " << query << "\n\n";
        oss << "请选择最合适的工具并生成参数。返回 JSON 格式:\n";
        oss << "{\"tool_name\": \"工具名\", \"arguments\": {参数}}\n";
        oss << "如果不需要工具，返回: {\"tool_name\": \"\"}\n";

        return oss.str();
    }

    /**
     * @brief Parse LLM response into tool call
     */
    ToolCallResult parse_tool_call(const std::string& response,
                                  const std::vector<agent_rpc::mcp::ToolInfo>& candidates) {
        ToolCallResult result;
        result.success = false;

        try {
            auto j = json::parse(response);

            if (j.contains("tool_name")) {
                std::string tool_name = j["tool_name"].get<std::string>();

                // Validate tool is in candidates
                bool valid = false;
                for (const auto& c : candidates) {
                    if (c.name == tool_name) {
                        valid = true;
                        break;
                    }
                }

                if (valid && !tool_name.empty()) {
                    result.tool_name = tool_name;
                    result.arguments = j.value("arguments", json::object());
                    result.success = true;
                }
            }

        } catch (const json::exception& e) {
            // Try to extract from text
            // ... 省略错误处理
        }

        return result;
    }

    /**
     * @brief Generate arguments for a tool based on query
     */
    json generate_arguments(const std::string& query, const agent_rpc::mcp::ToolInfo& tool) {
        // Simple heuristic: use query as expression for calculator
        if (tool.name == "calculator" || tool.name == "add" || tool.name == "subtract" ||
            tool.name == "multiply" || tool.name == "divide" || tool.name == "power" ||
            tool.name == "sqrt" || tool.name == "factorial") {
            return {{"expression", query}};
        }

        // For FWI tools
        if (tool.name == "list_models" || tool.name == "list_datasets") {
            return json::object();
        }

        if (tool.name == "inspect_model") {
            // Try to extract model name from query
            return {{"model_id", "marmousi2"}};
        }

        if (tool.name == "formula_helper") {
            // Try to extract formula name from query
            if (query.find("梯度") != std::string::npos) return {{"formula_name", "gradient"}};
            if (query.find("目标函数") != std::string::npos) return {{"formula_name", "objective"}};
            if (query.find("伴随") != std::string::npos) return {{"formula_name", "adjoint"}};
            if (query.find("cycle") != std::string::npos) return {{"formula_name", "cycle_skip"}};
            return {{"formula_name", "objective"}};
        }

        if (tool.name == "search_fwi_notes") {
            return {{"query", query}};
        }

        return json::object();
    }

    agent_rpc::mcp::MCPAgentIntegration* mcp_integration_;
    std::function<std::string(const std::string&, const std::string&)> chat_func_;
};

} // namespace orchestrator
} // namespace agent_rpc
