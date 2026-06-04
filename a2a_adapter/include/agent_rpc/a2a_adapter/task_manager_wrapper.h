/**
 * @file task_manager_wrapper.h
 * @brief Task Manager Wrapper - integrates A2A TaskManager with RPC framework
 * 
 * Task 6.1: 集成TaskManager任务管理器
 * Requirements: 6.1, 9.4
 */

#pragma once

#include <a2a/server/task_manager.hpp>
#include <a2a/server/memory_task_store.hpp>
#include <a2a/models/agent_task.hpp>
#include <a2a/models/agent_message.hpp>
#include <a2a/core/types.hpp>

#include <atomic>
#include <memory>
#include <string>
#include <vector>
#include <functional>
#include <mutex>
#include <unordered_set>
#include <unordered_map>

namespace agent_rpc {
namespace a2a_adapter {

/**
 * @brief Task Manager configuration
 */
struct TaskManagerConfig {
    bool enable_redis_store = false;
    std::string redis_url = "localhost:6379";
    int max_history_length = 100;
    int task_timeout_seconds = 3600;  // 1 hour default
};

/**
 * @brief Task state transition validator
 * 
 * Valid transitions:
 *   Submitted -> Running
 *   Running -> Completed | Failed | Canceled
 *   (Terminal states cannot transition)
 */
class TaskStateValidator {
public:
    /**
     * @brief Check if a state transition is valid
     * @param from Current state
     * @param to Target state
     * @return true if transition is valid
     */
    static bool isValidTransition(a2a::TaskState from, a2a::TaskState to);
    
    /**
     * @brief Check if a state is terminal
     * @param state State to check
     * @return true if state is terminal (Completed, Failed, Canceled, Rejected)
     */
    static bool isTerminalState(a2a::TaskState state);
    
    /**
     * @brief Get all valid next states from current state
     * @param current Current state
     * @return Vector of valid next states
     */
    static std::vector<a2a::TaskState> getValidNextStates(a2a::TaskState current);
};

/**
 * @brief Task Manager Wrapper - provides RPC-friendly interface to A2A TaskManager
 * 
 * Features:
 * - Task ID uniqueness tracking
 * - State machine validation
 * - Context-based history queries
 * - Thread-safe operations
 */
class TaskManagerWrapper {
public:
    /**
     * @brief Callback for task state changes
     */
    using TaskStateCallback = std::function<void(const std::string& task_id, 
                                                  a2a::TaskState old_state,
                                                  a2a::TaskState new_state)>;
    
    /**
     * @brief Callback for message processing
     */
    using MessageCallback = std::function<a2a::A2AResponse(const a2a::MessageSendParams&)>;
    
    TaskManagerWrapper();
    ~TaskManagerWrapper();
    
    // Disable copy
    TaskManagerWrapper(const TaskManagerWrapper&) = delete;
    TaskManagerWrapper& operator=(const TaskManagerWrapper&) = delete;
    
    /**
     * @brief Initialize with configuration
     * @param config Configuration options
     * @return true if initialization successful
     */
    bool initialize(const TaskManagerConfig& config = TaskManagerConfig());
    
    /**
     * @brief Shutdown and cleanup
     */
    void shutdown();
    
    // === Task Operations ===
    
    /**
     * @brief Create a new task with unique ID
     * @param context_id Optional context ID (generated if empty)
     * @return Created task
     * @throws std::runtime_error if task ID collision detected
     * 
     * Property 2: Task ID Uniqueness - each task gets a unique ID
     */
    a2a::AgentTask createTask(const std::string& context_id = "");
    
    /**
     * @brief Get task by ID
     * @param task_id Task identifier
     * @return Task if found
     * @throws a2a::A2AException if not found
     */
    a2a::AgentTask getTask(const std::string& task_id);
    
    /**
     * @brief Check if task exists
     * @param task_id Task identifier
     * @return true if task exists
     */
    bool taskExists(const std::string& task_id);
    
    /**
     * @brief Cancel a task
     * @param task_id Task identifier
     * @return Updated task
     * @throws a2a::A2AException if task cannot be cancelled
     */
    a2a::AgentTask cancelTask(const std::string& task_id);
    
    // === State Management ===
    
    /**
     * @brief Update task state with validation
     * @param task_id Task identifier
     * @param new_state New state
     * @param message Optional status message
     * @return true if state was updated
     * @throws std::invalid_argument if transition is invalid
     * 
     * Property 3: Task State Machine Consistency
     */
    bool updateTaskState(const std::string& task_id, 
                        a2a::TaskState new_state,
                        const std::string& message = "");
    
    /**
     * @brief Get current task state
     * @param task_id Task identifier
     * @return Current state
     * @throws a2a::A2AException if task not found
     */
    a2a::TaskState getTaskState(const std::string& task_id);
    
    /**
     * @brief Set callback for state changes
     */
    void setStateChangeCallback(TaskStateCallback callback);
    
    // === Message & History ===
    
    /**
     * @brief Add message to task history
     * @param task_id Task identifier
     * @param message Message to add
     */
    void addMessage(const std::string& task_id, const a2a::AgentMessage& message);
    
    /**
     * @brief Get message history by context ID
     * @param context_id Context identifier
     * @param max_length Maximum messages to return (0 = all)
     * @return Vector of messages in chronological order
     * 
     * Property 6: Context ID Preservation
     */
    std::vector<a2a::AgentMessage> getHistory(const std::string& context_id, 
                                               int max_length = 0);
    
    /**
     * @brief Get all task IDs for a context
     * @param context_id Context identifier
     * @return Vector of task IDs
     */
    std::vector<std::string> getTasksByContext(const std::string& context_id);
    
    // === Message Processing ===
    
    /**
     * @brief Set message handler callback
     */
    void setMessageHandler(MessageCallback callback);
    
    /**
     * @brief Process a message
     * @param params Message parameters
     * @return Response
     */
    a2a::A2AResponse processMessage(const a2a::MessageSendParams& params);
    
    // === Artifact Management ===
    
    /**
     * @brief Add artifact to task
     * @param task_id Task identifier
     * @param artifact Artifact to add
     */
    void addArtifact(const std::string& task_id, const a2a::Artifact& artifact);
    
    // === Statistics ===
    
    /**
     * @brief Get total number of tasks created
     */
    size_t getTotalTasksCreated() const;
    
    /**
     * @brief Get number of active (non-terminal) tasks
     */
    size_t getActiveTaskCount() const;
    
    /**
     * @brief Get underlying task store (for testing)
     */
    std::shared_ptr<a2a::ITaskStore> getTaskStore() const;

private:
    std::unique_ptr<a2a::TaskManager> task_manager_;
    TaskManagerConfig config_;
    
    // Task ID tracking for uniqueness
    mutable std::mutex task_ids_mutex_;
    std::unordered_set<std::string> created_task_ids_;
    
    // Context to task mapping
    mutable std::mutex context_mutex_;
    std::unordered_map<std::string, std::vector<std::string>> context_to_tasks_;
    
    // Callbacks
    TaskStateCallback state_change_callback_;
    MessageCallback message_handler_;
    
    // Statistics
    std::atomic<size_t> total_tasks_created_{0};
    
    bool initialized_ = false;
    
    // Helper to generate unique task ID
    std::string generateUniqueTaskId();
    
    // Helper to track context-task relationship
    void trackContextTask(const std::string& context_id, const std::string& task_id);
};

} // namespace a2a_adapter
} // namespace agent_rpc
