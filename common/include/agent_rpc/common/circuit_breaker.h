#pragma once

#include "types.h"
#include <memory>
#include <string>
#include <map>
#include <mutex>
#include <atomic>
#include <chrono>

namespace agent_rpc {
namespace common {

// 熔断器状态
enum class CircuitState {
    CLOSED,     // 关闭状态 - 正常请求
    OPEN,       // 开启状态 - 熔断，拒绝请求
    HALF_OPEN   // 半开状态 - 尝试恢复
};

// 熔断器配置
struct CircuitBreakerConfig {
    int failure_threshold = 5;           // 失败阈值
    int success_threshold = 3;           // 半开状态下的成功阈值
    std::chrono::milliseconds timeout = std::chrono::milliseconds(60000); // 熔断超时时间
    std::chrono::milliseconds half_open_timeout = std::chrono::milliseconds(30000); // 半开超时时间
    double failure_rate_threshold = 0.5; // 失败率阈值
    int min_request_count = 10;          // 最小请求数量（用于计算失败率）
};

// 熔断器统计信息
struct CircuitBreakerStats {
    int total_requests = 0;
    int successful_requests = 0;
    int failed_requests = 0;
    int rejected_requests = 0;
    std::chrono::steady_clock::time_point last_failure_time;
    std::chrono::steady_clock::time_point last_success_time;
    double current_failure_rate = 0.0;
};

// 熔断器类
class CircuitBreaker {
public:
    explicit CircuitBreaker(const CircuitBreakerConfig& config = CircuitBreakerConfig{});
    ~CircuitBreaker() = default;
    
    // 执行请求
    template<typename Func>
    auto execute(Func&& func) -> decltype(func());
    
    // 记录成功
    void recordSuccess();
    
    // 记录失败
    void recordFailure();
    
    // 检查是否允许请求
    bool isRequestAllowed();
    
    // 获取当前状态
    CircuitState getState() const { return state_; }
    
    // 获取统计信息
    CircuitBreakerStats getStats() const;
    
    // 重置熔断器
    void reset();
    
    // 更新配置
    void updateConfig(const CircuitBreakerConfig& config);
    
    // 获取配置
    const CircuitBreakerConfig& getConfig() const { return config_; }

private:
    void transitionToOpen();
    void transitionToHalfOpen();
    void transitionToClosed();
    void updateFailureRate();
    bool shouldAttemptReset();
    
    CircuitBreakerConfig config_;
    std::atomic<CircuitState> state_{CircuitState::CLOSED};
    mutable std::mutex stats_mutex_;
    CircuitBreakerStats stats_;
    std::chrono::steady_clock::time_point last_state_change_;
};

// 熔断器管理器
class CircuitBreakerManager {
public:
    static CircuitBreakerManager& getInstance();
    
    // 获取或创建熔断器
    std::shared_ptr<CircuitBreaker> getCircuitBreaker(const std::string& service_name);
    
    // 移除熔断器
    void removeCircuitBreaker(const std::string& service_name);
    
    // 获取所有熔断器状态
    std::map<std::string, CircuitState> getAllStates() const;
    
    // 重置所有熔断器
    void resetAll();
    
    // 更新配置
    void updateConfig(const std::string& service_name, const CircuitBreakerConfig& config);

private:
    CircuitBreakerManager() = default;
    ~CircuitBreakerManager() = default;
    CircuitBreakerManager(const CircuitBreakerManager&) = delete;
    CircuitBreakerManager& operator=(const CircuitBreakerManager&) = delete;
    
    mutable std::mutex circuit_breakers_mutex_;
    std::map<std::string, std::shared_ptr<CircuitBreaker>> circuit_breakers_;
};

// 熔断器装饰器
template<typename T>
class CircuitBreakerDecorator {
public:
    CircuitBreakerDecorator(const std::string& service_name,
                           const CircuitBreakerConfig& config = CircuitBreakerConfig{})
        : circuit_breaker_(CircuitBreakerManager::getInstance().getCircuitBreaker(service_name)) {
        if (circuit_breaker_) {
            circuit_breaker_->updateConfig(config);
        }
    }

    template<typename Func>
    auto call(Func&& func) -> decltype(func()) {
        return circuit_breaker_->execute(std::forward<Func>(func));
    }

    std::shared_ptr<CircuitBreaker> getCircuitBreaker() const { return circuit_breaker_; }

private:
    std::shared_ptr<CircuitBreaker> circuit_breaker_;
};

} // namespace common
} // namespace agent_rpc
