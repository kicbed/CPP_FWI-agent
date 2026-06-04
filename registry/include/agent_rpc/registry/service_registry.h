#pragma once

#include "agent_rpc/common/types.h"
#include "agent_rpc/common/logger.h"
#include <memory>
#include <string>
#include <vector>
#include <map>
#include <mutex>
#include <atomic>
#include <thread>
#include <functional>

namespace agent_rpc {
namespace registry {

// 服务注册中心接口
class ServiceRegistry {
public:
    virtual ~ServiceRegistry() = default;
    
    // 注册服务
    virtual bool registerService(const common::ServiceEndpoint& endpoint) = 0;
    
    // 注销服务
    virtual bool unregisterService(const std::string& service_id) = 0;
    
    // 发现服务
    virtual std::vector<common::ServiceEndpoint> discoverServices(const std::string& service_name) = 0;
    
    // 获取服务健康状态
    virtual bool isServiceHealthy(const std::string& service_id) = 0;
    
    // 更新服务心跳
    virtual bool updateHeartbeat(const std::string& service_id) = 0;
    
    // 监听服务变化
    virtual void watchServices(const std::string& service_name,
                              std::function<void(const std::vector<common::ServiceEndpoint>&)> callback) = 0;
};

// 基于Consul的服务注册中心实现
class ConsulServiceRegistry : public ServiceRegistry {
public:
    ConsulServiceRegistry();
    ~ConsulServiceRegistry();
    
    // 初始化
    bool initialize(const std::string& consul_address);
    
    // 实现接口方法
    bool registerService(const common::ServiceEndpoint& endpoint) override;
    bool unregisterService(const std::string& service_id) override;
    std::vector<common::ServiceEndpoint> discoverServices(const std::string& service_name) override;
    bool isServiceHealthy(const std::string& service_id) override;
    bool updateHeartbeat(const std::string& service_id) override;
    void watchServices(const std::string& service_name,
                      std::function<void(const std::vector<common::ServiceEndpoint>&)> callback) override;
    
    // 健康检查
    void startHealthCheck();
    void stopHealthCheck();
    
    // 获取服务ID
    std::string getServiceId(const common::ServiceEndpoint& endpoint) const;

private:
    void healthCheckLoop();
    std::string makeHttpRequest(const std::string& method, 
                               const std::string& url, 
                               const std::string& body = "");
    std::vector<common::ServiceEndpoint> parseServiceList(const std::string& json_response);
    common::ServiceEndpoint parseServiceEndpoint(const std::string& json_service);
    
    std::string consul_address_;
    std::atomic<bool> health_check_running_{false};
    std::thread health_check_thread_;
    
    mutable std::mutex services_mutex_;
    std::map<std::string, common::ServiceEndpoint> registered_services_;
    std::map<std::string, std::vector<common::ServiceEndpoint>> discovered_services_;
    
    std::map<std::string, std::function<void(const std::vector<common::ServiceEndpoint>&)>> watchers_;
    mutable std::mutex watchers_mutex_;
};

// 基于etcd的服务注册中心实现
class EtcdServiceRegistry : public ServiceRegistry {
public:
    EtcdServiceRegistry();
    ~EtcdServiceRegistry();
    
    // 初始化
    bool initialize(const std::string& etcd_address);
    
    // 实现接口方法
    bool registerService(const common::ServiceEndpoint& endpoint) override;
    bool unregisterService(const std::string& service_id) override;
    std::vector<common::ServiceEndpoint> discoverServices(const std::string& service_name) override;
    bool isServiceHealthy(const std::string& service_id) override;
    bool updateHeartbeat(const std::string& service_id) override;
    void watchServices(const std::string& service_name,
                      std::function<void(const std::vector<common::ServiceEndpoint>&)> callback) override;

private:
    void watchLoop();
    std::string makeEtcdRequest(const std::string& method, 
                               const std::string& key, 
                               const std::string& value = "");
    std::vector<common::ServiceEndpoint> parseEtcdResponse(const std::string& response);
    
    std::string etcd_address_;
    std::atomic<bool> watch_running_{false};
    std::thread watch_thread_;
    
    mutable std::mutex services_mutex_;
    std::map<std::string, common::ServiceEndpoint> registered_services_;
    std::map<std::string, std::vector<common::ServiceEndpoint>> discovered_services_;
    
    std::map<std::string, std::function<void(const std::vector<common::ServiceEndpoint>&)>> watchers_;
    mutable std::mutex watchers_mutex_;
};

// 内存服务注册中心实现（用于测试）
class MemoryServiceRegistry : public ServiceRegistry {
public:
    MemoryServiceRegistry() = default;
    ~MemoryServiceRegistry() = default;
    
    // 实现接口方法
    bool registerService(const common::ServiceEndpoint& endpoint) override;
    bool unregisterService(const std::string& service_id) override;
    std::vector<common::ServiceEndpoint> discoverServices(const std::string& service_name) override;
    bool isServiceHealthy(const std::string& service_id) override;
    bool updateHeartbeat(const std::string& service_id) override;
    void watchServices(const std::string& service_name,
                      std::function<void(const std::vector<common::ServiceEndpoint>&)> callback) override;

private:
    mutable std::mutex services_mutex_;
    std::map<std::string, common::ServiceEndpoint> services_;
    std::map<std::string, std::function<void(const std::vector<common::ServiceEndpoint>&)>> watchers_;
    mutable std::mutex watchers_mutex_;
};

} // namespace registry
} // namespace agent_rpc
