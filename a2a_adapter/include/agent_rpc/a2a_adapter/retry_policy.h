/**
 * @file retry_policy.h
 * @brief Retry policy for A2A operations
 * 
 * Requirements: 10.2
 * Task 15: 日志和指标集成 - 重试策略
 */

#pragma once

#include <functional>
#include <chrono>
#include <thread>
#include <random>
#include <string>

namespace agent_rpc {
namespace a2a_adapter {

/**
 * @brief Retry policy configuration
 */
struct RetryConfig {
    int max_retries = 3;
    int initial_delay_ms = 100;
    int max_delay_ms = 10000;
    double backoff_multiplier = 2.0;
    bool add_jitter = true;
    double jitter_factor = 0.1;  // 10% jitter
};

/**
 * @brief Result of a retry operation
 */
template<typename T>
struct RetryResult {
    bool success = false;
    T value;
    int attempts = 0;
    std::string last_error;
};

/**
 * @brief Retry policy with exponential backoff
 */
class RetryPolicy {
public:
    explicit RetryPolicy(const RetryConfig& config = RetryConfig{})
        : config_(config)
        , rng_(std::random_device{}()) {}
    
    /**
     * @brief Execute an operation with retry
     * @param operation The operation to execute
     * @param should_retry Predicate to determine if retry should occur
     * @return Result of the operation
     */
    template<typename T>
    RetryResult<T> execute(
        std::function<T()> operation,
        std::function<bool(const std::exception&)> should_retry = nullptr) {
        
        RetryResult<T> result;
        result.attempts = 0;
        
        int delay_ms = config_.initial_delay_ms;
        
        for (int attempt = 0; attempt <= config_.max_retries; ++attempt) {
            result.attempts = attempt + 1;
            
            try {
                result.value = operation();
                result.success = true;
                return result;
                
            } catch (const std::exception& e) {
                result.last_error = e.what();
                
                // Check if we should retry
                if (attempt >= config_.max_retries) {
                    break;
                }
                
                if (should_retry && !should_retry(e)) {
                    break;
                }
                
                // Calculate delay with exponential backoff
                int actual_delay = delay_ms;
                
                // Add jitter if enabled
                if (config_.add_jitter) {
                    std::uniform_real_distribution<double> dist(
                        1.0 - config_.jitter_factor,
                        1.0 + config_.jitter_factor);
                    actual_delay = static_cast<int>(delay_ms * dist(rng_));
                }
                
                // Sleep before retry
                std::this_thread::sleep_for(
                    std::chrono::milliseconds(actual_delay));
                
                // Update delay for next iteration
                delay_ms = std::min(
                    static_cast<int>(delay_ms * config_.backoff_multiplier),
                    config_.max_delay_ms);
            }
        }
        
        return result;
    }
    
    /**
     * @brief Execute a void operation with retry
     */
    RetryResult<bool> executeVoid(
        std::function<void()> operation,
        std::function<bool(const std::exception&)> should_retry = nullptr) {
        
        return execute<bool>(
            [&operation]() {
                operation();
                return true;
            },
            should_retry);
    }
    
    /**
     * @brief Get the configuration
     */
    const RetryConfig& getConfig() const { return config_; }
    
    /**
     * @brief Update configuration
     */
    void setConfig(const RetryConfig& config) { config_ = config; }

private:
    RetryConfig config_;
    std::mt19937 rng_;
};

/**
 * @brief Check if an error is retryable
 */
inline bool isRetryableError(const std::string& error_message) {
    // Network-related errors
    if (error_message.find("connection") != std::string::npos ||
        error_message.find("timeout") != std::string::npos ||
        error_message.find("unavailable") != std::string::npos ||
        error_message.find("UNAVAILABLE") != std::string::npos ||
        error_message.find("DEADLINE_EXCEEDED") != std::string::npos) {
        return true;
    }
    
    // Temporary errors
    if (error_message.find("temporary") != std::string::npos ||
        error_message.find("retry") != std::string::npos) {
        return true;
    }
    
    return false;
}

/**
 * @brief Default retry predicate
 */
inline bool defaultShouldRetry(const std::exception& e) {
    return isRetryableError(e.what());
}

} // namespace a2a_adapter
} // namespace agent_rpc
