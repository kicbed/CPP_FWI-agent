/**
 * @file a2a_metrics.h
 * @brief A2A specific metrics integration
 * 
 * Requirements: 10.5
 * Task 15: 日志和指标集成
 */

#pragma once

#include "agent_rpc/common/metrics.h"
#include <string>
#include <chrono>
#include <atomic>
#include <mutex>
#include <map>

namespace agent_rpc {
namespace a2a_adapter {

/**
 * @brief A2A specific metrics collector
 */
class A2AMetrics {
public:
    static A2AMetrics& getInstance();
    
    // ========================================================================
    // Query Metrics
    // ========================================================================
    
    /**
     * @brief Record an A2A query request
     */
    void recordQueryRequest(const std::string& agent_id, bool streaming);
    
    /**
     * @brief Record query completion
     */
    void recordQueryComplete(const std::string& agent_id, 
                            int64_t duration_ms, 
                            bool success);
    
    /**
     * @brief Record query error
     */
    void recordQueryError(const std::string& agent_id, 
                         const std::string& error_type);
    
    // ========================================================================
    // Task Metrics
    // ========================================================================
    
    /**
     * @brief Record task creation
     */
    void recordTaskCreated(const std::string& task_id);
    
    /**
     * @brief Record task state change
     */
    void recordTaskStateChange(const std::string& task_id,
                              const std::string& from_state,
                              const std::string& to_state);
    
    /**
     * @brief Record task completion
     */
    void recordTaskComplete(const std::string& task_id,
                           int64_t duration_ms,
                           bool success);
    
    // ========================================================================
    // Agent Metrics
    // ========================================================================
    
    /**
     * @brief Record agent registration
     */
    void recordAgentRegistered(const std::string& agent_id);
    
    /**
     * @brief Record agent unregistration
     */
    void recordAgentUnregistered(const std::string& agent_id);
    
    /**
     * @brief Record agent health check
     */
    void recordAgentHealthCheck(const std::string& agent_id, bool healthy);
    
    /**
     * @brief Record agent routing decision
     */
    void recordAgentRouting(const std::string& from_agent,
                           const std::string& to_agent,
                           const std::string& skill);
    
    // ========================================================================
    // Connection Metrics
    // ========================================================================
    
    /**
     * @brief Record A2A connection attempt
     */
    void recordConnectionAttempt(const std::string& target_url, bool success);
    
    /**
     * @brief Record A2A message sent
     */
    void recordMessageSent(const std::string& message_type, size_t size_bytes);
    
    /**
     * @brief Record A2A message received
     */
    void recordMessageReceived(const std::string& message_type, size_t size_bytes);
    
    // ========================================================================
    // Statistics
    // ========================================================================
    
    /**
     * @brief Get total query count
     */
    uint64_t getTotalQueries() const { return total_queries_.load(); }
    
    /**
     * @brief Get successful query count
     */
    uint64_t getSuccessfulQueries() const { return successful_queries_.load(); }
    
    /**
     * @brief Get failed query count
     */
    uint64_t getFailedQueries() const { return failed_queries_.load(); }
    
    /**
     * @brief Get average query latency in ms
     */
    double getAverageQueryLatency() const;
    
    /**
     * @brief Get active task count
     */
    uint64_t getActiveTasks() const { return active_tasks_.load(); }
    
    /**
     * @brief Get registered agent count
     */
    uint64_t getRegisteredAgents() const { return registered_agents_.load(); }
    
    /**
     * @brief Export metrics as JSON
     */
    std::string exportJson() const;
    
    /**
     * @brief Reset all metrics
     */
    void reset();

private:
    A2AMetrics() = default;
    ~A2AMetrics() = default;
    A2AMetrics(const A2AMetrics&) = delete;
    A2AMetrics& operator=(const A2AMetrics&) = delete;
    
    // Query metrics
    std::atomic<uint64_t> total_queries_{0};
    std::atomic<uint64_t> successful_queries_{0};
    std::atomic<uint64_t> failed_queries_{0};
    std::atomic<uint64_t> streaming_queries_{0};
    std::atomic<uint64_t> total_query_latency_ms_{0};
    
    // Task metrics
    std::atomic<uint64_t> total_tasks_{0};
    std::atomic<uint64_t> active_tasks_{0};
    std::atomic<uint64_t> completed_tasks_{0};
    std::atomic<uint64_t> failed_tasks_{0};
    
    // Agent metrics
    std::atomic<uint64_t> registered_agents_{0};
    std::atomic<uint64_t> healthy_agents_{0};
    std::atomic<uint64_t> routing_decisions_{0};
    
    // Connection metrics
    std::atomic<uint64_t> connection_attempts_{0};
    std::atomic<uint64_t> successful_connections_{0};
    std::atomic<uint64_t> messages_sent_{0};
    std::atomic<uint64_t> messages_received_{0};
    std::atomic<uint64_t> bytes_sent_{0};
    std::atomic<uint64_t> bytes_received_{0};
    
    mutable std::mutex mutex_;
};

/**
 * @brief RAII helper for timing operations
 */
class ScopedTimer {
public:
    ScopedTimer(std::function<void(int64_t)> callback)
        : callback_(std::move(callback))
        , start_(std::chrono::steady_clock::now()) {}
    
    ~ScopedTimer() {
        auto end = std::chrono::steady_clock::now();
        auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            end - start_).count();
        if (callback_) {
            callback_(duration);
        }
    }

private:
    std::function<void(int64_t)> callback_;
    std::chrono::steady_clock::time_point start_;
};

} // namespace a2a_adapter
} // namespace agent_rpc
