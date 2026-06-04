/**
 * @file memory_manager.h
 * @brief MemoryManager - unified memory management for session/agent/task
 *
 * Separates memory into three layers:
 * 1. Session Memory (a2a:session:{context_id}) - user-visible conversation
 * 2. Agent Memory (a2a:agent:{agent_id}:{context_id}) - agent internal processing
 * 3. Task State (a2a:task:{task_id}) - task lifecycle (compatible with existing)
 *
 * This prevents different agents from mixing their histories in the same context.
 */

#pragma once

#include <a2a/server/task_store.hpp>
#include <a2a/models/agent_message.hpp>
#include <a2a/models/agent_task.hpp>
#include <hiredis/hiredis.h>
#include <string>
#include <vector>
#include <mutex>
#include <memory>
#include <nlohmann/json.hpp>

using json = nlohmann::json;

namespace agent_rpc {
namespace orchestrator {

/**
 * @brief Memory type enumeration
 */
enum class MemoryType {
    SESSION,    // User-visible conversation
    AGENT,      // Agent internal processing
    TASK        // Task state
};

/**
 * @brief Unified memory manager for the orchestrator
 *
 * Provides clean separation of:
 * - Session memory: what the user sees (conversation history)
 * - Agent memory: what each agent does internally (reasoning, tool calls)
 * - Task state: task lifecycle management
 *
 * Also provides backward compatibility with legacy a2a:history:{context_id} keys.
 */
class MemoryManager {
public:
    /**
     * @brief Construct with Redis connection parameters
     */
    explicit MemoryManager(const std::string& redis_host = "127.0.0.1",
                          int redis_port = 6379);

    ~MemoryManager();

    // Disable copy
    MemoryManager(const MemoryManager&) = delete;
    MemoryManager& operator=(const MemoryManager&) = delete;

    // ========================================================================
    // Session Memory (user-visible conversation)
    // ========================================================================

    /**
     * @brief Save a message to session memory
     * @param context_id Session/conversation ID
     * @param message The message to save
     */
    void save_session_message(const std::string& context_id,
                             const a2a::AgentMessage& message);

    /**
     * @brief Get session conversation history
     * @param context_id Session/conversation ID
     * @param limit Maximum number of messages (0 = all)
     * @return List of messages in chronological order
     */
    std::vector<a2a::AgentMessage> get_session_history(const std::string& context_id,
                                                       int limit = 0);

    // ========================================================================
    // Agent Memory (agent internal processing)
    // ========================================================================

    /**
     * @brief Save an agent processing step
     * @param agent_id Agent identifier (e.g., "orch-1", "math-1")
     * @param context_id Session/conversation ID
     * @param step Processing step as JSON
     */
    void save_agent_memory(const std::string& agent_id,
                          const std::string& context_id,
                          const json& step);

    /**
     * @brief Get agent processing history
     * @param agent_id Agent identifier
     * @param context_id Session/conversation ID
     * @param limit Maximum number of steps (0 = all)
     * @return List of processing steps in chronological order
     */
    std::vector<json> get_agent_memory(const std::string& agent_id,
                                       const std::string& context_id,
                                       int limit = 0);

    // ========================================================================
    // Task State (task lifecycle)
    // ========================================================================

    /**
     * @brief Save task state
     * @param task_id Task identifier
     * @param state Task state as JSON
     */
    void save_task_state(const std::string& task_id, const json& state);

    /**
     * @brief Get task state
     * @param task_id Task identifier
     * @return Task state as JSON, or empty JSON if not found
     */
    json get_task_state(const std::string& task_id);

    /**
     * @brief Check if task exists
     */
    bool task_exists(const std::string& task_id);

    // ========================================================================
    // Legacy Compatibility
    // ========================================================================

    /**
     * @brief Get history from legacy key (a2a:history:{context_id})
     * @param context_id Context ID
     * @param limit Maximum number of messages
     * @return List of messages
     *
     * This method reads from the old key format for backward compatibility.
     * New code should use get_session_history() instead.
     */
    std::vector<a2a::AgentMessage> get_legacy_history(const std::string& context_id,
                                                      int limit = 0);

    /**
     * @brief Migrate legacy keys to new format
     * @param context_id Context ID to migrate
     *
     * Copies messages from a2a:history:{context_id} to a2a:session:{context_id}
     * and a2a:task:{context_id} to the new task key format.
     */
    void migrate_legacy_keys(const std::string& context_id);

    // ========================================================================
    // Utility
    // ========================================================================

    /**
     * @brief Get all keys for debugging
     * @param pattern Redis key pattern (e.g., "a2a:*")
     * @return List of matching keys
     */
    std::vector<std::string> get_keys(const std::string& pattern = "a2a:*");

private:
    // Redis key builders
    std::string session_key(const std::string& context_id) const {
        return "a2a:session:" + context_id;
    }

    std::string agent_key(const std::string& agent_id,
                         const std::string& context_id) const {
        return "a2a:agent:" + agent_id + ":" + context_id;
    }

    std::string task_key(const std::string& task_id) const {
        return "a2a:task:" + task_id;
    }

    std::string legacy_history_key(const std::string& context_id) const {
        return "a2a:history:" + context_id;
    }

    // Redis operations
    redisReply* execute_command(const char* format, ...);
    void ensure_connection();

    // Connection
    redisContext* context_;
    std::string host_;
    int port_;
    std::mutex mutex_;
};

} // namespace orchestrator
} // namespace agent_rpc
