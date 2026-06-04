#include "agent_rpc/common/circuit_breaker.h"
#include "agent_rpc/common/logger.h"
#include <algorithm>
#include <cmath>

namespace agent_rpc {
namespace common {

// CircuitBreaker 实现
CircuitBreaker::CircuitBreaker(const CircuitBreakerConfig& config)
    : config_(config)
    , state_(CircuitState::CLOSED)
    , last_state_change_(std::chrono::steady_clock::now()) {
}

void CircuitBreaker::recordSuccess() {
    std::lock_guard<std::mutex> lock(stats_mutex_);

    stats_.total_requests++;
    stats_.successful_requests++;
    stats_.last_success_time = std::chrono::steady_clock::now();

    updateFailureRate();

    if (state_ == CircuitState::HALF_OPEN &&
        stats_.successful_requests >= config_.success_threshold) {
        transitionToClosed();
    }
}

void CircuitBreaker::recordFailure() {
    std::lock_guard<std::mutex> lock(stats_mutex_);

    stats_.total_requests++;
    stats_.failed_requests++;
    stats_.last_failure_time = std::chrono::steady_clock::now();

    updateFailureRate();

    if (stats_.failed_requests >= config_.failure_threshold) {
        transitionToOpen();
    }
}

bool CircuitBreaker::isRequestAllowed() {
    std::lock_guard<std::mutex> lock(stats_mutex_);

    switch (state_) {
        case CircuitState::CLOSED:
            return true;

        case CircuitState::OPEN:
            if (shouldAttemptReset()) {
                transitionToHalfOpen();
                return true;
            }
            return false;

        case CircuitState::HALF_OPEN:
            return stats_.total_requests < config_.success_threshold;

        default:
            return false;
    }
}

CircuitBreakerStats CircuitBreaker::getStats() const {
    std::lock_guard<std::mutex> lock(stats_mutex_);
    return stats_;
}

void CircuitBreaker::reset() {
    std::lock_guard<std::mutex> lock(stats_mutex_);
    stats_ = CircuitBreakerStats{};
    state_ = CircuitState::CLOSED;
    last_state_change_ = std::chrono::steady_clock::now();
    LOG_INFO("Circuit breaker reset");
}

void CircuitBreaker::updateConfig(const CircuitBreakerConfig& config) {
    std::lock_guard<std::mutex> lock(stats_mutex_);
    config_ = config;
}

void CircuitBreaker::transitionToOpen() {
    if (state_ != CircuitState::OPEN) {
        state_ = CircuitState::OPEN;
        last_state_change_ = std::chrono::steady_clock::now();
        LOG_WARN("Circuit breaker opened due to failures");
    }
}

void CircuitBreaker::transitionToHalfOpen() {
    if (state_ != CircuitState::HALF_OPEN) {
        state_ = CircuitState::HALF_OPEN;
        last_state_change_ = std::chrono::steady_clock::now();
        stats_.successful_requests = 0;
        stats_.total_requests = 0;
        LOG_INFO("Circuit breaker half-opened for testing");
    }
}

void CircuitBreaker::transitionToClosed() {
    if (state_ != CircuitState::CLOSED) {
        state_ = CircuitState::CLOSED;
        last_state_change_ = std::chrono::steady_clock::now();
        stats_ = CircuitBreakerStats{};
        LOG_INFO("Circuit breaker closed - service recovered");
    }
}

void CircuitBreaker::updateFailureRate() {
    if (stats_.total_requests >= config_.min_request_count) {
        stats_.current_failure_rate = static_cast<double>(stats_.failed_requests) / stats_.total_requests;
        if (stats_.current_failure_rate >= config_.failure_rate_threshold) {
            transitionToOpen();
        }
    }
}

bool CircuitBreaker::shouldAttemptReset() {
    auto now = std::chrono::steady_clock::now();
    auto time_since_open = now - last_state_change_;
    return time_since_open >= config_.timeout;
}

// CircuitBreakerManager 实现
CircuitBreakerManager& CircuitBreakerManager::getInstance() {
    static CircuitBreakerManager instance;
    return instance;
}

std::shared_ptr<CircuitBreaker> CircuitBreakerManager::getCircuitBreaker(const std::string& service_name) {
    std::lock_guard<std::mutex> lock(circuit_breakers_mutex_);

    auto it = circuit_breakers_.find(service_name);
    if (it != circuit_breakers_.end()) {
        return it->second;
    }

    auto circuit_breaker = std::make_shared<CircuitBreaker>();
    circuit_breakers_[service_name] = circuit_breaker;
    LOG_INFO("Created circuit breaker for service: " + service_name);
    return circuit_breaker;
}

void CircuitBreakerManager::removeCircuitBreaker(const std::string& service_name) {
    std::lock_guard<std::mutex> lock(circuit_breakers_mutex_);
    auto it = circuit_breakers_.find(service_name);
    if (it != circuit_breakers_.end()) {
        circuit_breakers_.erase(it);
        LOG_INFO("Removed circuit breaker for service: " + service_name);
    }
}

std::map<std::string, CircuitState> CircuitBreakerManager::getAllStates() const {
    std::lock_guard<std::mutex> lock(circuit_breakers_mutex_);
    std::map<std::string, CircuitState> states;
    for (const auto& pair : circuit_breakers_) {
        states[pair.first] = pair.second->getState();
    }
    return states;
}

void CircuitBreakerManager::resetAll() {
    std::lock_guard<std::mutex> lock(circuit_breakers_mutex_);
    for (auto& pair : circuit_breakers_) {
        pair.second->reset();
    }
    LOG_INFO("Reset all circuit breakers");
}

void CircuitBreakerManager::updateConfig(const std::string& service_name, const CircuitBreakerConfig& config) {
    std::lock_guard<std::mutex> lock(circuit_breakers_mutex_);
    auto it = circuit_breakers_.find(service_name);
    if (it != circuit_breakers_.end()) {
        it->second->updateConfig(config);
        LOG_INFO("Updated circuit breaker config for service: " + service_name);
    }
}

} // namespace common
} // namespace agent_rpc
