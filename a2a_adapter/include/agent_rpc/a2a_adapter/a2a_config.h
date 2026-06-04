/**
 * @file a2a_config.h
 * @brief A2A adapter configuration
 * 
 * Requirements: 9.1, 9.2, 9.3, 9.4
 */

#pragma once

#include <string>
#include <vector>

namespace agent_rpc {
namespace a2a_adapter {

/**
 * @brief A2A adapter configuration structure
 */
struct A2AConfig {
    // Orchestrator settings (Requirement 9.2)
    std::string orchestrator_url = "http://localhost:5000";
    int orchestrator_port = 5000;
    
    // Registry settings (Requirement 9.3)
    std::string registry_url = "http://localhost:8500";
    int heartbeat_interval_seconds = 30;
    
    // Task storage settings (Requirement 9.4)
    bool enable_redis_store = false;
    std::string redis_url = "localhost:6379";
    
    // Request settings
    int request_timeout_seconds = 30;
    int history_length = 10;
    int max_retries = 3;
    int retry_delay_ms = 1000;
    
    // Feature flags
    bool enable_streaming = true;
    bool enable_metrics = true;
    bool enable_logging = true;
    
    /**
     * @brief Validate configuration and apply defaults for invalid values
     * @return true if configuration is valid, false if defaults were applied
     */
    bool validate() {
        bool valid = true;
        
        if (orchestrator_port <= 0 || orchestrator_port > 65535) {
            orchestrator_port = 5000;
            valid = false;
        }
        
        if (heartbeat_interval_seconds <= 0) {
            heartbeat_interval_seconds = 30;
            valid = false;
        }
        
        if (request_timeout_seconds <= 0) {
            request_timeout_seconds = 30;
            valid = false;
        }
        
        if (history_length < 0) {
            history_length = 10;
            valid = false;
        }
        
        if (max_retries < 0) {
            max_retries = 3;
            valid = false;
        }
        
        if (retry_delay_ms < 0) {
            retry_delay_ms = 1000;
            valid = false;
        }
        
        return valid;
    }
    
    /**
     * @brief Get default configuration
     */
    static A2AConfig getDefault() {
        return A2AConfig{};
    }
};

} // namespace a2a_adapter
} // namespace agent_rpc
