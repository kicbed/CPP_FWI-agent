/**
 * @file task_manager_wrapper.cpp
 * @brief Task Manager Wrapper implementation
 * 
 * Task 6.1, 6.3, 6.5: Task management implementation
 */

#include "agent_rpc/a2a_adapter/task_manager_wrapper.h"
#include <a2a/core/exception.hpp>
#include <sstream>
#include <random>
#include <chrono>
#include <iomanip>

namespace agent_rpc {
namespace a2a_adapter {

// ============================================================================
// TaskStateValidator Implementation
// ============================================================================

bool TaskStateValidator::isValidTransition(a2a::TaskState from, a2a::TaskState to) {
    // Terminal states cannot transition
    if (isTerminalState(from)) {
        return false;
    }
    
    switch (from) {
        case a2a::TaskState::Submitted:
            // Submitted can go to Running, or directly to terminal states
            return to == a2a::TaskState::Running ||
                   to == a2a::TaskState::Canceled ||
                   to == a2a::TaskState::Rejected;
            
        case a2a::TaskState::Running:
            // Running can go to any terminal state
            return to == a2a::TaskState::Completed ||
                   to == a2a::TaskState::Failed ||
                   to == a2a::TaskState::Canceled;
            
        default:
            return false;
    }
}

bool TaskStateValidator::isTerminalState(a2a::TaskState state) {
    return state == a2a::TaskState::Completed ||
           state == a2a::TaskState::Failed ||
           state == a2a::TaskState::Canceled ||
           state == a2a::TaskState::Rejected;
}

std::vector<a2a::TaskState> TaskStateValidator::getValidNextStates(a2a::TaskState current) {
    std::vector<a2a::TaskState> valid_states;
    
    if (isTerminalState(current)) {
        return valid_states;  // No valid transitions from terminal states
    }
    
    switch (current) {
        case a2a::TaskState::Submitted:
            valid_states.push_back(a2a::TaskState::Running);
            valid_states.push_back(a2a::TaskState::Canceled);
            valid_states.push_back(a2a::TaskState::Rejected);
            break;
            
        case a2a::TaskState::Running:
            valid_states.push_back(a2a::TaskState::Completed);
            valid_states.push_back(a2a::TaskState::Failed);
            valid_states.push_back(a2a::TaskState::Canceled);
            break;
            
        default:
            break;
    }
    
    return valid_states;
}

// ============================================================================
// TaskManagerWrapper Implementation
// ============================================================================

TaskManagerWrapper::TaskManagerWrapper() = default;

TaskManagerWrapper::~TaskManagerWrapper() {
    shutdown();
}

bool TaskManagerWrapper::initialize(const TaskManagerConfig& config) {
    if (initialized_) {
        return true;
    }
    
    config_ = config;
    
    // Create task store based on configuration
    std::shared_ptr<a2a::ITaskStore> task_store;
    
    if (config.enable_redis_store) {
        // TODO: Implement Redis task store when needed
        // For now, fall back to memory store
        task_store = std::make_shared<a2a::MemoryTaskStore>();
    } else {
        task_store = std::make_shared<a2a::MemoryTaskStore>();
    }
    
    // Create task manager with the store
    task_manager_ = std::make_unique<a2a::TaskManager>(task_store);
    
    // Set up internal callbacks
    task_manager_->set_on_task_created([this](const a2a::AgentTask& task) {
        // Track context-task relationship
        trackContextTask(task.context_id(), task.id());
    });
    
    task_manager_->set_on_task_updated([this](const a2a::AgentTask& task) {
        // State change callback is handled in updateTaskState
    });
    
    initialized_ = true;
    return true;
}

void TaskManagerWrapper::shutdown() {
    if (!initialized_) {
        return;
    }
    
    task_manager_.reset();
    
    {
        std::lock_guard<std::mutex> lock(task_ids_mutex_);
        created_task_ids_.clear();
    }
    
    {
        std::lock_guard<std::mutex> lock(context_mutex_);
        context_to_tasks_.clear();
    }
    
    total_tasks_created_ = 0;
    initialized_ = false;
}

std::string TaskManagerWrapper::generateUniqueTaskId() {
    // Generate a unique task ID using timestamp + random component
    auto now = std::chrono::system_clock::now();
    auto duration = now.time_since_epoch();
    auto millis = std::chrono::duration_cast<std::chrono::milliseconds>(duration).count();
    
    static std::random_device rd;
    static std::mt19937 gen(rd());
    static std::uniform_int_distribution<> dis(1000, 9999);
    
    std::ostringstream oss;
    oss << "task-" << millis << "-" << dis(gen);
    
    return oss.str();
}

void TaskManagerWrapper::trackContextTask(const std::string& context_id, 
                                          const std::string& task_id) {
    std::lock_guard<std::mutex> lock(context_mutex_);
    context_to_tasks_[context_id].push_back(task_id);
}

a2a::AgentTask TaskManagerWrapper::createTask(const std::string& context_id) {
    if (!initialized_) {
        throw std::runtime_error("TaskManagerWrapper not initialized");
    }
    
    // Generate unique task ID
    std::string task_id;
    {
        std::lock_guard<std::mutex> lock(task_ids_mutex_);
        
        // Keep generating until we get a unique ID
        int attempts = 0;
        const int max_attempts = 100;
        
        do {
            task_id = generateUniqueTaskId();
            attempts++;
            
            if (attempts >= max_attempts) {
                throw std::runtime_error("Failed to generate unique task ID after " + 
                                        std::to_string(max_attempts) + " attempts");
            }
        } while (created_task_ids_.find(task_id) != created_task_ids_.end());
        
        // Record the task ID
        created_task_ids_.insert(task_id);
    }
    
    // Create task through A2A TaskManager
    // Note: The on_task_created callback will call trackContextTask, so we don't call it here
    auto task = task_manager_->create_task(context_id, task_id);
    
    // Update statistics
    total_tasks_created_++;
    
    return task;
}

a2a::AgentTask TaskManagerWrapper::getTask(const std::string& task_id) {
    if (!initialized_) {
        throw std::runtime_error("TaskManagerWrapper not initialized");
    }
    
    return task_manager_->get_task(task_id);
}

bool TaskManagerWrapper::taskExists(const std::string& task_id) {
    if (!initialized_) {
        return false;
    }
    
    auto store = task_manager_->get_task_store();
    return store->task_exists(task_id);
}

a2a::AgentTask TaskManagerWrapper::cancelTask(const std::string& task_id) {
    if (!initialized_) {
        throw std::runtime_error("TaskManagerWrapper not initialized");
    }
    
    auto old_task = task_manager_->get_task(task_id);
    auto old_state = old_task.status().state();
    
    auto task = task_manager_->cancel_task(task_id);
    
    // Notify state change
    if (state_change_callback_) {
        state_change_callback_(task_id, old_state, a2a::TaskState::Canceled);
    }
    
    return task;
}

bool TaskManagerWrapper::updateTaskState(const std::string& task_id,
                                         a2a::TaskState new_state,
                                         const std::string& message) {
    if (!initialized_) {
        throw std::runtime_error("TaskManagerWrapper not initialized");
    }
    
    // Get current task
    auto task = task_manager_->get_task(task_id);
    auto old_state = task.status().state();
    
    // Validate state transition
    if (!TaskStateValidator::isValidTransition(old_state, new_state)) {
        std::ostringstream oss;
        oss << "Invalid state transition from " << a2a::to_string(old_state)
            << " to " << a2a::to_string(new_state);
        throw std::invalid_argument(oss.str());
    }
    
    // Update state
    task_manager_->update_status(task_id, new_state, nullptr);
    
    // Notify state change
    if (state_change_callback_) {
        state_change_callback_(task_id, old_state, new_state);
    }
    
    return true;
}

a2a::TaskState TaskManagerWrapper::getTaskState(const std::string& task_id) {
    if (!initialized_) {
        throw std::runtime_error("TaskManagerWrapper not initialized");
    }
    
    auto task = task_manager_->get_task(task_id);
    return task.status().state();
}

void TaskManagerWrapper::setStateChangeCallback(TaskStateCallback callback) {
    state_change_callback_ = std::move(callback);
}

void TaskManagerWrapper::addMessage(const std::string& task_id, 
                                    const a2a::AgentMessage& message) {
    if (!initialized_) {
        throw std::runtime_error("TaskManagerWrapper not initialized");
    }
    
    auto store = task_manager_->get_task_store();
    store->add_history_message(task_id, message);
}

std::vector<a2a::AgentMessage> TaskManagerWrapper::getHistory(const std::string& context_id,
                                                              int max_length) {
    if (!initialized_) {
        return {};
    }
    
    auto store = task_manager_->get_task_store();
    
    // Get all tasks for this context
    std::vector<std::string> task_ids;
    {
        std::lock_guard<std::mutex> lock(context_mutex_);
        auto it = context_to_tasks_.find(context_id);
        if (it != context_to_tasks_.end()) {
            task_ids = it->second;
        }
    }
    
    // Collect all messages from all tasks in this context
    std::vector<a2a::AgentMessage> all_messages;
    
    for (const auto& task_id : task_ids) {
        auto history = store->get_history(task_id, 0);  // Get all
        all_messages.insert(all_messages.end(), history.begin(), history.end());
    }
    
    // Also try direct context_id lookup (for backward compatibility)
    auto direct_history = store->get_history(context_id, 0);
    all_messages.insert(all_messages.end(), direct_history.begin(), direct_history.end());
    
    // Apply max_length limit
    if (max_length > 0 && all_messages.size() > static_cast<size_t>(max_length)) {
        return std::vector<a2a::AgentMessage>(
            all_messages.end() - max_length,
            all_messages.end()
        );
    }
    
    return all_messages;
}

std::vector<std::string> TaskManagerWrapper::getTasksByContext(const std::string& context_id) {
    std::lock_guard<std::mutex> lock(context_mutex_);
    
    auto it = context_to_tasks_.find(context_id);
    if (it != context_to_tasks_.end()) {
        return it->second;
    }
    
    return {};
}

void TaskManagerWrapper::setMessageHandler(MessageCallback callback) {
    message_handler_ = std::move(callback);
    
    if (task_manager_) {
        task_manager_->set_on_message_received(message_handler_);
    }
}

a2a::A2AResponse TaskManagerWrapper::processMessage(const a2a::MessageSendParams& params) {
    if (!initialized_) {
        throw std::runtime_error("TaskManagerWrapper not initialized");
    }
    
    return task_manager_->send_message(params);
}

void TaskManagerWrapper::addArtifact(const std::string& task_id, 
                                     const a2a::Artifact& artifact) {
    if (!initialized_) {
        throw std::runtime_error("TaskManagerWrapper not initialized");
    }
    
    task_manager_->return_artifact(task_id, artifact);
}

size_t TaskManagerWrapper::getTotalTasksCreated() const {
    return total_tasks_created_;
}

size_t TaskManagerWrapper::getActiveTaskCount() const {
    if (!initialized_) {
        return 0;
    }
    
    std::lock_guard<std::mutex> lock(task_ids_mutex_);
    
    size_t active_count = 0;
    auto store = task_manager_->get_task_store();
    
    for (const auto& task_id : created_task_ids_) {
        auto task_opt = store->get_task(task_id);
        if (task_opt.has_value() && !task_opt->is_terminal()) {
            active_count++;
        }
    }
    
    return active_count;
}

std::shared_ptr<a2a::ITaskStore> TaskManagerWrapper::getTaskStore() const {
    if (!initialized_) {
        return nullptr;
    }
    
    return task_manager_->get_task_store();
}

} // namespace a2a_adapter
} // namespace agent_rpc
