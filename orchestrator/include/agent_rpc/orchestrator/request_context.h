/**
 * @file request_context.h
 * @brief RequestContext - unified context for request processing
 *
 * Carries request_id, context_id, task_id, user info, routing config,
 * and metadata through the entire request processing pipeline.
 */

#pragma once

#include <string>
#include <chrono>
#include <nlohmann/json.hpp>

using json = nlohmann::json;

namespace agent_rpc {
namespace orchestrator {

/**
 * @brief Unified request context for the entire processing pipeline
 *
 * Created once per incoming request, passed through all processing stages.
 * Ensures consistent logging, routing decisions, and memory access.
 */
struct RequestContext {
    std::string request_id;          // Unique request ID (UUID)
    std::string context_id;          // Session/conversation ID
    std::string task_id;             // Task ID (usually same as context_id)
    std::string user_text;           // User's input text
    std::string user_id;             // User identifier (for multi-user)
    std::string client_id;           // Client identifier
    std::string routing_mode;        // "fixed" | "agent-rag"
    std::string tool_calling_mode;   // "rule" | "llm"
    json metadata;                   // Extensible metadata

    // Timing
    std::chrono::steady_clock::time_point start_time;

    RequestContext()
        : start_time(std::chrono::steady_clock::now()) {}

    /**
     * @brief Create a new RequestContext with auto-generated request_id
     */
    static RequestContext create(const std::string& context_id = "default") {
        RequestContext ctx;
        ctx.context_id = context_id;
        ctx.task_id = context_id;
        ctx.request_id = generate_uuid();
        return ctx;
    }

    /**
     * @brief Get elapsed time in milliseconds since request start
     */
    int64_t elapsed_ms() const {
        auto now = std::chrono::steady_clock::now();
        return std::chrono::duration_cast<std::chrono::milliseconds>(
            now - start_time).count();
    }

    /**
     * @brief Convert to JSON for logging
     */
    json to_json() const {
        return {
            {"request_id", request_id},
            {"context_id", context_id},
            {"task_id", task_id},
            {"user_text", user_text.substr(0, 100)},  // Truncate for logging
            {"user_id", user_id},
            {"client_id", client_id},
            {"routing_mode", routing_mode},
            {"tool_calling_mode", tool_calling_mode},
            {"elapsed_ms", elapsed_ms()}
        };
    }

private:
    /**
     * @brief Generate a simple UUID-like string
     */
    static std::string generate_uuid() {
        static uint64_t counter = 0;
        auto now = std::chrono::steady_clock::now().time_since_epoch().count();
        return "req-" + std::to_string(now) + "-" + std::to_string(++counter);
    }
};

} // namespace orchestrator
} // namespace agent_rpc
