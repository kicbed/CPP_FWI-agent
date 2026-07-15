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
#include <initializer_list>
#include <algorithm>
#include <cctype>
#include <limits>
#include <mutex>
#include <optional>
#include <regex>
#include <stdexcept>
#include <unordered_map>

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

namespace detail {

inline constexpr const char* kAllowLegacyFwiSubmitMetadata =
    "allow_legacy_fwi_submit";

/**
 * Resolve the opt-out carried by the Web -> gRPC -> A2A request chain.
 * Absence deliberately preserves legacy CLI/MCP/A2A behavior. If the key is
 * present, only the adapter-produced string "false" is accepted.
 */
inline bool resolve_allow_legacy_fwi_submit(const json& params) {
    if (!params.is_object()) {
        throw std::invalid_argument("params must be an object");
    }
    const auto metadata = params.find("metadata");
    if (metadata == params.end()) return true;
    if (!metadata->is_object()) {
        throw std::invalid_argument("params.metadata must be an object");
    }
    const auto value = metadata->find(kAllowLegacyFwiSubmitMetadata);
    if (value == metadata->end()) return true;
    if (!value->is_string() || value->get<std::string>() != "false") {
        throw std::invalid_argument(
            "metadata.allow_legacy_fwi_submit must be the string 'false' when provided");
    }
    return false;
}

inline bool contains_any(const std::string& text,
                         std::initializer_list<const char*> needles) {
    for (const char* needle : needles) {
        if (text.find(needle) != std::string::npos) return true;
    }
    return false;
}

inline std::string ascii_lower_copy(std::string text) {
    std::transform(text.begin(), text.end(), text.begin(), [](unsigned char c) {
        return static_cast<char>(std::tolower(c));
    });
    return text;
}

inline bool is_fwi_tool_call_allowed(const ToolCallResult& call,
                                     bool allow_legacy_fwi_submit) {
    return allow_legacy_fwi_submit || call.tool_name != "fwi_submit_demo";
}

inline bool contains_ascii_word(const std::string& lower_text,
                                const std::string& lower_word) {
    std::string::size_type position = 0;
    const auto is_word_character = [](unsigned char character) {
        return std::isalnum(character) != 0 || character == '_';
    };
    while ((position = lower_text.find(lower_word, position)) != std::string::npos) {
        const bool left_boundary =
            position == 0 ||
            !is_word_character(static_cast<unsigned char>(lower_text[position - 1]));
        const auto end = position + lower_word.size();
        const bool right_boundary =
            end == lower_text.size() ||
            !is_word_character(static_cast<unsigned char>(lower_text[end]));
        if (left_boundary && right_boundary) return true;
        position = end;
    }
    return false;
}

inline bool contains_any_ascii_word(
    const std::string& lower_text,
    std::initializer_list<const char*> lower_words) {
    return std::any_of(
        lower_words.begin(), lower_words.end(), [&](const char* word) {
            return contains_ascii_word(lower_text, word);
        });
}

inline std::string extract_fwi_job_id(const std::string& text) {
    static const std::regex pattern(R"(fwi-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12})");
    std::smatch match;
    return std::regex_search(text, match, pattern) ? match.str(0) : std::string();
}

inline bool mentions_fwi_query(const std::string& query) {
    const std::string lower_query = ascii_lower_copy(query);
    return contains_any(query, {"全波形反演", "声学反演"}) ||
           contains_any(lower_query, {"fwi", "deepwave", "marmousi"});
}

// A negated execution request must never be allowed to fall through to the
// positive keyword planner. Keep this list deliberately concrete: it is a
// safety gate for the fixed experimental runner, not a general NLP model.
inline bool has_fwi_negative_intent(const std::string& query) {
    const std::string lower_query = ascii_lower_copy(query);
    return mentions_fwi_query(query) && (contains_any(
        query, {"不要运行", "不要执行", "不要提交", "不要启动", "不要开始",
                "别运行", "别执行", "别提交", "别启动", "先别运行", "先别执行",
                "不用运行", "不用执行", "无需运行", "无需执行", "不运行", "不执行",
                "不提交", "不启动", "不开始", "勿运行", "勿执行", "仅解释", "只解释",
                "仅说明", "只说明", "只讲解"}) ||
        contains_any(lower_query, {"do not run", "don't run", "do not execute",
                                   "don't execute", "do not submit", "don't submit",
                                   "do not start", "don't start"}));
}

inline bool is_fwi_capability_query(const std::string& query) {
    if (!mentions_fwi_query(query)) return false;
    const std::string lower_query = ascii_lower_copy(query);
    const bool fixed_phrase = contains_any(
        query, {"能否", "能不能", "是否能", "是否可以", "可以做", "能做", "支持吗",
                "是否支持", "支不支持", "会不会", "能运行吗", "可以运行吗", "能执行吗",
                "可以执行吗", "可以使用吗", "能用吗"}) ||
        contains_any(lower_query, {"can you", "do you support", "is it possible"});
    const bool modal_question = contains_any(query, {"吗", "？", "?"}) &&
                                contains_any(query, {"能", "可以", "支持"});
    return fixed_phrase || modal_question;
}

inline bool is_fwi_howto_query(const std::string& query) {
    const std::string lower_query = ascii_lower_copy(query);
    return mentions_fwi_query(query) && (contains_any(
        query, {"怎么启动", "如何启动", "怎样启动", "怎么运行", "如何运行", "怎样运行",
                "怎么执行", "如何执行", "怎样执行", "怎么提交", "如何提交", "怎样提交",
                "怎么开始", "如何开始", "怎样开始", "怎么使用", "如何使用", "怎样使用",
                "怎么用", "如何用", "怎样用", "启动方式", "运行方式", "使用方式"}) ||
        contains_any(lower_query, {"how to run", "how to start", "how to execute",
                                   "how to submit", "how to use"}));
}

/**
 * Extract an iteration count only when it is explicitly attached to an FWI
 * iteration phrase. This deliberately ignores digits in model IDs, 2D, grid
 * sizes, frequencies, and job IDs.
 */
inline std::optional<int> extract_requested_fwi_iterations(const std::string& query) {
    static const std::regex chinese_before_pattern(
        R"(([+-]?[0-9]+(\.[0-9]+)?)\s*(次|轮)(\s*迭代)?)");
    static const std::regex chinese_after_pattern(
        R"(迭代\s*([+-]?[0-9]+(\.[0-9]+)?)\s*(次|轮)?)");
    static const std::regex english_pattern(
        R"(([+-]?[0-9]+(\.[0-9]+)?)\s*(iterations?|iters?))",
        std::regex_constants::icase);
    static const std::regex positive_integer_pattern(R"([0-9]+)");
    std::smatch match;
    if (!std::regex_search(query, match, chinese_before_pattern) &&
        !std::regex_search(query, match, chinese_after_pattern) &&
        !std::regex_search(query, match, english_pattern)) {
        return std::nullopt;
    }
    const std::string token = match.str(1);
    if (!std::regex_match(token, positive_integer_pattern)) {
        return 0;  // Signed and decimal iteration counts are invalid.
    }
    try {
        const unsigned long long value = std::stoull(token);
        if (value > static_cast<unsigned long long>(std::numeric_limits<int>::max())) {
            return 0;  // Present but out of range: always invalid.
        }
        return static_cast<int>(value);
    } catch (const std::exception&) {
        return 0;  // Present but unparsable: fail closed.
    }
}

inline bool has_invalid_fwi_iteration_request(const std::string& query) {
    if (!mentions_fwi_query(query)) return false;
    const auto iterations = extract_requested_fwi_iterations(query);
    return iterations.has_value() && (*iterations < 1 || *iterations > 10000);
}

inline bool is_fwi_theory_query(const std::string& query) {
    const std::string lower_query = ascii_lower_copy(query);
    const bool asks_theory = contains_any(
        query, {"什么是", "解释", "理论", "原理", "公式", "为什么", "为何", "区别",
                "如何", "怎么", "方法", "步骤"}) ||
        contains_any(lower_query, {"what is", "why", "explain", "theory"});
    return mentions_fwi_query(query) && asks_theory;
}

inline bool is_fwi_guidance_query(const std::string& query) {
    return has_fwi_negative_intent(query) || is_fwi_capability_query(query) ||
           is_fwi_howto_query(query) || is_fwi_theory_query(query) ||
           has_invalid_fwi_iteration_request(query);
}

inline bool contains_run_command_word(const std::string& query) {
    static const std::vector<std::string> reference_phrases = {
        "运行状态", "运行进度", "运行结果", "运行情况", "运行了吗", "运行了么",
        "运行过吗", "运行是否", "运行成功", "运行失败", "运行完成"
    };
    std::string::size_type pos = 0;
    while ((pos = query.find("运行", pos)) != std::string::npos) {
        const bool is_reference = std::any_of(
            reference_phrases.begin(), reference_phrases.end(),
            [&](const std::string& phrase) {
                return query.compare(pos, phrase.size(), phrase) == 0;
            });
        if (!is_reference) {
            return true;
        }
        pos += std::string("运行").size();
    }
    return false;
}

/**
 * Deterministic routing for the fixed FWI execution surface. It intentionally
 * requires an execution/status/result intent, so a theory question can never
 * submit a numerical job merely because the same acronym appears.
 */
inline ToolCallResult plan_fwi_tool_call(const std::string& query,
                                         const std::string& last_job_id = {}) {
    ToolCallResult call{false, "", json::object(), "", ""};
    if (is_fwi_guidance_query(query)) return call;

    const std::string explicit_job_id = extract_fwi_job_id(query);
    const std::string job_id = explicit_job_id.empty() ? last_job_id : explicit_job_id;
    const bool mentions_fwi = contains_any(
        query, {"FWI", "fwi", "全波形反演", "声学反演", "反演", "Marmousi",
                "marmousi_94_288"});
    const bool refers_to_previous = contains_any(query, {"刚才", "上一个", "上一项"});

    // Submission takes precedence in a combined sentence such as “运行…并展示
    // 结果”. The result is asynchronous, so this call only submits and returns
    // the queued job; a later status/result request retrieves artifacts.
    const std::string lower_query = ascii_lower_copy(query);
    const bool has_standard_run_action = contains_run_command_word(query) || contains_any(
        query, {"执行", "提交", "启动", "开始"}) ||
        contains_any_ascii_word(lower_query, {"run", "execute", "submit", "start"});
    // Users commonly say “做一下 Marmousi 的反演测试” without spelling out
    // either FWI or “运行”. Accept that wording only when the colloquial verb
    // is paired with a concrete inversion experiment noun; the model whitelist
    // check below is still mandatory. This intentionally does not treat a
    // generic “分析一下反演结果” as authority to start computation.
    const bool has_colloquial_run_action =
        contains_any(query, {"做一下", "做一个", "做个", "做一次", "进行", "开展"}) &&
        (contains_any(query, {"反演测试", "反演实验", "反演演示", "反演计算", "全波形反演"}) ||
         contains_any(lower_query, {"fwi test", "fwi demo", "fwi inversion"}));
    const bool has_run_action = has_standard_run_action || has_colloquial_run_action;
    const bool asks_existing_state = contains_any(
        query, {"查看", "查询", "状态", "进度", "情况", "了吗", "了么", "是否已经",
                "结果", "显示", "展示"}) ||
        contains_any(lower_query, {"status", "progress", "result", "show"});
    const bool asks_to_run_and_show = has_run_action &&
        contains_any(query, {"并", "然后", "随后", "完成后", "结束后", "跑完后",
                             "完成以后", "结束以后"}) &&
        contains_any(query, {"结果", "显示", "展示", "损失曲线"});
    const bool asks_to_run = has_run_action &&
                             (!asks_existing_state || asks_to_run_and_show);
    const bool names_model = contains_any(query, {"marmousi_94_288", "Marmousi", "marmousi"});
    if (asks_to_run && mentions_fwi && names_model) {
        const auto iterations = extract_requested_fwi_iterations(query);
        std::string preset = "fwi_demo";
        if (contains_any(query, {"正演", "forward"})) {
            preset = "forward";
        } else {
            if ((iterations.has_value() && *iterations == 2) ||
                contains_any(query, {"smoke", "两次迭代", "2次迭代", "2 次迭代"})) {
                preset = "fwi_smoke";
            }
        }

        const std::string device = contains_any(query, {"CPU", "cpu"}) ? "cpu" : "cuda";
        call.success = true;
        call.tool_name = "fwi_submit_demo";
        call.arguments = {
            {"model_id", "marmousi_94_288"},
            {"preset", preset},
            {"device", device}
        };
        if (preset != "forward") {
            call.arguments["iterations"] = iterations.value_or(
                preset == "fwi_smoke" ? 2 : 5);
        }
        return call;
    }

    if (contains_any(query, {"状态", "进度", "情况", "了吗", "了么", "status", "progress"}) &&
        (mentions_fwi || refers_to_previous || !explicit_job_id.empty())) {
        if (job_id.empty()) return call;
        call.success = true;
        call.tool_name = "fwi_get_status";
        call.arguments = {{"job_id", job_id}};
        return call;
    }

    if (contains_any(query, {"显示", "展示", "结果", "损失曲线", "loss curve", "炮集",
                             "反演模型"}) &&
        (mentions_fwi || refers_to_previous || !explicit_job_id.empty())) {
        if (job_id.empty()) return call;
        call.success = true;
        call.tool_name = "fwi_get_result";
        call.arguments = {{"job_id", job_id}};
        return call;
    }

    return call;
}

}  // namespace detail

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

    bool has_explicit_fwi_action(const std::string& query,
                                 const std::string& context_id = {}) {
        const std::string last_job_id = last_fwi_job_for_context(context_id);
        return !detail::plan_fwi_tool_call(query, last_job_id).tool_name.empty();
    }

    bool has_explicit_fwi_submission(const std::string& query,
                                     const std::string& context_id = {}) {
        const std::string last_job_id = last_fwi_job_for_context(context_id);
        return detail::plan_fwi_tool_call(query, last_job_id).tool_name ==
               "fwi_submit_demo";
    }

    /** Seed or restore the latest FWI job for one conversation only. */
    void remember_fwi_job(const std::string& context_id,
                          const std::string& job_id) {
        if (context_id.empty() || job_id.empty()) return;
        std::lock_guard<std::mutex> lock(fwi_state_mutex_);
        if (last_fwi_job_ids_.size() >= 1024 &&
            last_fwi_job_ids_.find(context_id) == last_fwi_job_ids_.end()) {
            last_fwi_job_ids_.erase(last_fwi_job_ids_.begin());
        }
        last_fwi_job_ids_[context_id] = job_id;
    }

    /**
     * @brief True for FWI capability, usage, negation, and theory questions.
     *
     * These queries are routed to the deterministic FWI guidance/explanation
     * path and must not enter Agent-RAG or the generic tool selector.
     */
    bool has_fwi_guidance_request(const std::string& query) const {
        return detail::is_fwi_guidance_query(query);
    }

    /**
     * @brief Process a query with tool calling
     * @param query User query
     * @param context_id Conversation-scoped state key
     * @param allow_legacy_fwi_submit Whether this source may invoke the legacy
     *        asynchronous FWI submission tool. Defaults to true for old callers.
     * @return Tool result as string, or empty if no tool needed
     *
     * Flow:
     * 1. Retrieve candidate tools (Tool-RAG)
     * 2. LLM selects tool and generates arguments
     * 3. Execute tool via MCP
     * 4. Return result
     */
    std::string process(const std::string& query,
                        const std::string& context_id = {},
                        bool allow_legacy_fwi_submit = true) {
        // Never let an FWI capability/how-to/negative/theory question reach
        // generic Tool-RAG. In particular, semantically similar knowledge
        // tools must not replace the honest runner guidance or launch a job.
        if (detail::is_fwi_guidance_query(query)) return "";

        const std::string last_job_id = last_fwi_job_for_context(context_id);
        const auto fwi_call = detail::plan_fwi_tool_call(query, last_job_id);
        // This is the execution-boundary check. It is intentionally based on
        // the actual deterministic plan, so a missed UI or intent
        // classification cannot reach fwi_submit_demo.
        if (!detail::is_fwi_tool_call_allowed(
                fwi_call, allow_legacy_fwi_submit)) {
            return "";
        }

        if (!mcp_integration_ || !mcp_integration_->isAvailable()) {
            return "";
        }

        if (!fwi_call.tool_name.empty()) {
            auto result = execute_tool(fwi_call);
            if (result.success && fwi_call.tool_name == "fwi_submit_demo") {
                const std::string submitted_job_id = detail::extract_fwi_job_id(result.result);
                if (!submitted_job_id.empty()) {
                    remember_fwi_job(context_id, submitted_job_id);
                }
            }
            return result.success ? result.result : std::string();
        }

        // Step 1: Retrieve candidate tools
        auto candidates = retrieve_tools(query, 5);
        // FWI job tools are stateful and security-sensitive. They are selected
        // only by the deterministic intent/whitelist planner above, never by
        // the generic LLM fallback (which could otherwise launch on a merely
        // semantically similar theory question).
        candidates.erase(
            std::remove_if(candidates.begin(), candidates.end(), [](const auto& tool) {
                return tool.name == "fwi_submit_demo" || tool.name == "fwi_get_status" ||
                       tool.name == "fwi_get_result";
            }),
            candidates.end());
        if (candidates.empty()) {
            return "";
        }

        // Step 2: LLM selects tool
        auto tool_call = select_tool(query, candidates);
        if (tool_call.tool_name.empty()) {
            return "";
        }

        if (!detail::is_fwi_tool_call_allowed(
                tool_call, allow_legacy_fwi_submit)) {
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

        if (tool.name == "fwi_submit_demo" || tool.name == "fwi_get_status" ||
            tool.name == "fwi_get_result") {
            // FWI tools are removed from generic candidates before this path;
            // keep argument generation fail-closed if called directly.
            const auto call = detail::plan_fwi_tool_call(query);
            if (call.tool_name == tool.name) return call.arguments;
        }

        return json::object();
    }

    agent_rpc::mcp::MCPAgentIntegration* mcp_integration_;
    std::function<std::string(const std::string&, const std::string&)> chat_func_;
    mutable std::mutex fwi_state_mutex_;
    std::unordered_map<std::string, std::string> last_fwi_job_ids_;

    std::string last_fwi_job_for_context(const std::string& context_id) const {
        if (context_id.empty()) return "";
        std::lock_guard<std::mutex> lock(fwi_state_mutex_);
        const auto found = last_fwi_job_ids_.find(context_id);
        return found == last_fwi_job_ids_.end() ? std::string() : found->second;
    }
};

} // namespace orchestrator
} // namespace agent_rpc
