/**
 * @file a2a_metrics.cpp
 * @brief A2A metrics implementation
 * 
 * Requirements: 10.5
 * Task 15: 日志和指标集成
 */

#include "agent_rpc/a2a_adapter/a2a_metrics.h"
#include <sstream>
#include <iomanip>

namespace agent_rpc {
namespace a2a_adapter {

A2AMetrics& A2AMetrics::getInstance() {
    static A2AMetrics instance;
    return instance;
}

void A2AMetrics::recordQueryRequest(const std::string& /*agent_id*/, bool streaming) {
    total_queries_++;
    if (streaming) {
        streaming_queries_++;
    }
}

void A2AMetrics::recordQueryComplete(
    const std::string& /*agent_id*/,
    int64_t duration_ms,
    bool success) {
    
    total_query_latency_ms_ += duration_ms;
    
    if (success) {
        successful_queries_++;
    } else {
        failed_queries_++;
    }
}

void A2AMetrics::recordQueryError(
    const std::string& /*agent_id*/,
    const std::string& /*error_type*/) {
    
    failed_queries_++;
}

void A2AMetrics::recordTaskCreated(const std::string& /*task_id*/) {
    total_tasks_++;
    active_tasks_++;
}

void A2AMetrics::recordTaskStateChange(
    const std::string& /*task_id*/,
    const std::string& /*from_state*/,
    const std::string& /*to_state*/) {
    // Could track state transitions if needed
}

void A2AMetrics::recordTaskComplete(
    const std::string& /*task_id*/,
    int64_t /*duration_ms*/,
    bool success) {
    
    if (active_tasks_ > 0) {
        active_tasks_--;
    }
    
    if (success) {
        completed_tasks_++;
    } else {
        failed_tasks_++;
    }
}

void A2AMetrics::recordAgentRegistered(const std::string& /*agent_id*/) {
    registered_agents_++;
    healthy_agents_++;
}

void A2AMetrics::recordAgentUnregistered(const std::string& /*agent_id*/) {
    if (registered_agents_ > 0) {
        registered_agents_--;
    }
    if (healthy_agents_ > 0) {
        healthy_agents_--;
    }
}

void A2AMetrics::recordAgentHealthCheck(
    const std::string& /*agent_id*/,
    bool healthy) {
    
    // This is a simplified implementation
    // In production, would track per-agent health
    if (healthy) {
        // Agent became healthy
    } else {
        // Agent became unhealthy
        if (healthy_agents_ > 0) {
            healthy_agents_--;
        }
    }
}

void A2AMetrics::recordAgentRouting(
    const std::string& /*from_agent*/,
    const std::string& /*to_agent*/,
    const std::string& /*skill*/) {
    
    routing_decisions_++;
}

void A2AMetrics::recordConnectionAttempt(
    const std::string& /*target_url*/,
    bool success) {
    
    connection_attempts_++;
    if (success) {
        successful_connections_++;
    }
}

void A2AMetrics::recordMessageSent(
    const std::string& /*message_type*/,
    size_t size_bytes) {
    
    messages_sent_++;
    bytes_sent_ += size_bytes;
}

void A2AMetrics::recordMessageReceived(
    const std::string& /*message_type*/,
    size_t size_bytes) {
    
    messages_received_++;
    bytes_received_ += size_bytes;
}

double A2AMetrics::getAverageQueryLatency() const {
    uint64_t total = total_queries_.load();
    if (total == 0) {
        return 0.0;
    }
    return static_cast<double>(total_query_latency_ms_.load()) / total;
}

std::string A2AMetrics::exportJson() const {
    std::lock_guard<std::mutex> lock(mutex_);
    
    std::ostringstream oss;
    oss << "{\n";
    oss << "  \"queries\": {\n";
    oss << "    \"total\": " << total_queries_.load() << ",\n";
    oss << "    \"successful\": " << successful_queries_.load() << ",\n";
    oss << "    \"failed\": " << failed_queries_.load() << ",\n";
    oss << "    \"streaming\": " << streaming_queries_.load() << ",\n";
    oss << "    \"average_latency_ms\": " << std::fixed << std::setprecision(2) 
        << getAverageQueryLatency() << "\n";
    oss << "  },\n";
    oss << "  \"tasks\": {\n";
    oss << "    \"total\": " << total_tasks_.load() << ",\n";
    oss << "    \"active\": " << active_tasks_.load() << ",\n";
    oss << "    \"completed\": " << completed_tasks_.load() << ",\n";
    oss << "    \"failed\": " << failed_tasks_.load() << "\n";
    oss << "  },\n";
    oss << "  \"agents\": {\n";
    oss << "    \"registered\": " << registered_agents_.load() << ",\n";
    oss << "    \"healthy\": " << healthy_agents_.load() << ",\n";
    oss << "    \"routing_decisions\": " << routing_decisions_.load() << "\n";
    oss << "  },\n";
    oss << "  \"connections\": {\n";
    oss << "    \"attempts\": " << connection_attempts_.load() << ",\n";
    oss << "    \"successful\": " << successful_connections_.load() << ",\n";
    oss << "    \"messages_sent\": " << messages_sent_.load() << ",\n";
    oss << "    \"messages_received\": " << messages_received_.load() << ",\n";
    oss << "    \"bytes_sent\": " << bytes_sent_.load() << ",\n";
    oss << "    \"bytes_received\": " << bytes_received_.load() << "\n";
    oss << "  }\n";
    oss << "}";
    
    return oss.str();
}

void A2AMetrics::reset() {
    std::lock_guard<std::mutex> lock(mutex_);
    
    total_queries_ = 0;
    successful_queries_ = 0;
    failed_queries_ = 0;
    streaming_queries_ = 0;
    total_query_latency_ms_ = 0;
    
    total_tasks_ = 0;
    active_tasks_ = 0;
    completed_tasks_ = 0;
    failed_tasks_ = 0;
    
    registered_agents_ = 0;
    healthy_agents_ = 0;
    routing_decisions_ = 0;
    
    connection_attempts_ = 0;
    successful_connections_ = 0;
    messages_sent_ = 0;
    messages_received_ = 0;
    bytes_sent_ = 0;
    bytes_received_ = 0;
}

} // namespace a2a_adapter
} // namespace agent_rpc
