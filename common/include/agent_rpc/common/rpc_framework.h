#pragma once

#include "types.h"
#include "logger.h"
#include "metrics.h"
#include "load_balancer.h"
#include "circuit_breaker.h"
#include <memory>
#include <string>

namespace agent_rpc {
namespace common {

// 前向声明
class RpcFramework;

// RPC框架主类
class RpcFramework {
public:
    static RpcFramework& getInstance();
    
    // 初始化框架
    bool initialize(const RpcConfig& config);
    
    // 启动服务
    bool startServer();
    
    // 停止服务
    void stopServer();
    
    // 获取配置
    const RpcConfig& getConfig() const { return config_; }
    
    // 是否运行中
    bool isRunning() const { return running_; }
    
    // 获取日志器
    std::shared_ptr<Logger> getLogger();
    
    // 获取监控指标
    std::shared_ptr<Metrics> getMetrics();
    
    // 获取负载均衡器
    std::shared_ptr<LoadBalancer> getLoadBalancer();

private:
    RpcFramework() = default;
    ~RpcFramework() = default;
    RpcFramework(const RpcFramework&) = delete;
    RpcFramework& operator=(const RpcFramework&) = delete;
    
    RpcConfig config_;
    std::atomic<bool> running_{false};
    
    std::shared_ptr<Logger> logger_;
    std::shared_ptr<Metrics> metrics_;
    std::shared_ptr<LoadBalancer> load_balancer_;
};

} // namespace common
} // namespace agent_rpc
