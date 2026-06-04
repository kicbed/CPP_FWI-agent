#pragma once

#include <string>
#include <vector>
#include <map>
#include <memory>
#include <atomic>
#include <mutex>
#include <chrono>
#include <thread>
#include <queue>
#include <condition_variable>
#include <functional>

namespace agent_rpc {
namespace common {

// 前向声明
class Logger;
class Metrics;

// 配置结构
struct RpcConfig {
    // RPC Server配置
    std::string server_address = "0.0.0.0:50051";
    int max_message_size = 4 * 1024 * 1024;  // 4MB
    int max_receive_message_size = 4 * 1024 * 1024;  // 4MB
    int timeout_seconds = 30;
    int max_retry_attempts = 3;
    int heartbeat_interval = 30;
    bool enable_ssl = false;
    std::string ssl_cert_path;
    std::string ssl_key_path;
    std::string log_level = "INFO";
    bool enable_service_registry = false;
    std::string registry_address = "localhost:8500";
    
    // A2A配置 (Requirements: 9.1)
    std::string orchestrator_url = "http://localhost:5000";
    int orchestrator_port = 5000;
    std::string agent_registry_url = "http://localhost:8500";
    bool enable_a2a = true;
    bool enable_redis_task_store = false;
    std::string redis_url = "localhost:6379";
    int a2a_timeout_seconds = 30;
    int a2a_history_length = 10;
};

// 服务信息
struct ServiceEndpoint {
    std::string host;
    int port;
    std::string service_name;
    std::string version;
    std::map<std::string, std::string> metadata;
    bool is_healthy = true;
    std::chrono::steady_clock::time_point last_heartbeat;
};

// 消息队列
template<typename T>
class MessageQueue {
public:
    void push(const T& item) {
        std::lock_guard<std::mutex> lock(mutex_);
        queue_.push(item);
        condition_.notify_one();
    }
    
    bool try_pop(T& item, std::chrono::milliseconds timeout = std::chrono::milliseconds(0)) {
        std::unique_lock<std::mutex> lock(mutex_);
        if (condition_.wait_for(lock, timeout, [this] { return !queue_.empty(); })) {
            item = queue_.front();
            queue_.pop();
            return true;
        }
        return false;
    }
    
    size_t size() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return queue_.size();
    }
    
    bool empty() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return queue_.empty();
    }

private:
    mutable std::mutex mutex_;
    std::queue<T> queue_;
    std::condition_variable condition_;
};

// 回调函数类型定义
using MessageHandler = std::function<void(const std::string&)>;
using ErrorHandler = std::function<void(const std::string&, int)>;
using HealthCheckHandler = std::function<bool()>;

} // namespace common
} // namespace agent_rpc
