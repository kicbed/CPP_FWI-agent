/**
 * @file context_window.h
 * @brief Bounded, role-aware conversation history for LLM prompts.
 */

#pragma once

#include <a2a/models/agent_message.hpp>

#include <cstddef>
#include <optional>
#include <string>
#include <vector>

namespace agent_rpc {
namespace orchestrator {

struct ContextWindowConfig {
    std::size_t max_messages = 10;
    std::size_t max_chars = 12000;
    std::size_t max_message_chars = 4000;
};

struct ContextWindowResult {
    // JSON is used as an explicit data boundary. The caller must still tell the
    // model that this content is untrusted conversation data, not instructions.
    std::string history_json = "[]";
    std::size_t included_messages = 0;
    std::size_t omitted_messages = 0;
    bool truncated = false;
};

/**
 * Validate a public conversation identifier before it is used in a Redis key.
 * IDs are intentionally opaque and limited to a conservative ASCII subset.
 */
bool is_valid_context_id(const std::string& context_id);

/** Generate a process-safe opaque conversation identifier. */
std::string generate_context_id();

/**
 * Return the supplied ID, generate one for an absent/empty ID, and reject a
 * malformed non-empty ID.
 */
std::optional<std::string> resolve_context_id(
    const std::optional<std::string>& requested_id);

/**
 * Select recent text messages under message and serialized-byte budgets.
 * The `max_chars` names are retained for environment-variable compatibility;
 * the implementation measures UTF-8/JSON bytes, not Unicode code points.
 * When exclude_trailing_user is true, the most recent user message is omitted;
 * this is useful when the current query has already been appended to storage
 * and is also passed separately to the model.
 */
ContextWindowResult build_context_window(
    const std::vector<a2a::AgentMessage>& history,
    const ContextWindowConfig& config = ContextWindowConfig(),
    bool exclude_trailing_user = false);

}  // namespace orchestrator
}  // namespace agent_rpc
