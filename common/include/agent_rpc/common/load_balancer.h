#pragma once

#include "types.h"
#include <memory>
#include <string>
#include <vector>
#include <map>
#include <mutex>
#include <atomic>
#include <random>
#include <algorithm>
#include <chrono>

namespace agent_rpc {
namespace common {

// 负载均衡策略枚举
enum class LoadBalanceStrategy {
    ROUND_ROBIN,        // 轮询
    RANDOM,             // 随机
    LEAST_CONNECTIONS,  // 最少连接
    WEIGHTED_ROUND_ROBIN, // 加权轮询
    CONSISTENT_HASH,    // 一致性哈希
    LEAST_RESPONSE_TIME // 最少响应时间
};

// 负载均衡器接口
class LoadBalancer {
public:
    virtual ~LoadBalancer() = default;
    
    // 选择服务端点
    virtual ServiceEndpoint selectEndpoint(const std::vector<ServiceEndpoint>& endpoints) = 0;
    
    // 更新服务端点列表
    virtual void updateEndpoints(const std::vector<ServiceEndpoint>& endpoints) = 0;
    
    // 标记服务端点状态
    virtual void markEndpointStatus(const std::string& endpoint_id, bool healthy) = 0;
    
    // 获取策略名称
    virtual std::string getStrategyName() const = 0;
};

// 轮询负载均衡器
class RoundRobinLoadBalancer : public LoadBalancer {
public:
    RoundRobinLoadBalancer();
    ~RoundRobinLoadBalancer() = default;
    
    ServiceEndpoint selectEndpoint(const std::vector<ServiceEndpoint>& endpoints) override;
    void updateEndpoints(const std::vector<ServiceEndpoint>& endpoints) override;
    void markEndpointStatus(const std::string& endpoint_id, bool healthy) override;
    std::string getStrategyName() const override { return "RoundRobin"; }

private:
    std::atomic<size_t> current_index_{0};
    mutable std::mutex endpoints_mutex_;
    std::vector<ServiceEndpoint> healthy_endpoints_;
};

// 随机负载均衡器
class RandomLoadBalancer : public LoadBalancer {
public:
    RandomLoadBalancer();
    ~RandomLoadBalancer() = default;
    
    ServiceEndpoint selectEndpoint(const std::vector<ServiceEndpoint>& endpoints) override;
    void updateEndpoints(const std::vector<ServiceEndpoint>& endpoints) override;
    void markEndpointStatus(const std::string& endpoint_id, bool healthy) override;
    std::string getStrategyName() const override { return "Random"; }

private:
    mutable std::mutex endpoints_mutex_;
    std::vector<ServiceEndpoint> healthy_endpoints_;
    std::random_device rd_;
    mutable std::mt19937 gen_;
};

// 最少连接负载均衡器
class LeastConnectionsLoadBalancer : public LoadBalancer {
public:
    LeastConnectionsLoadBalancer();
    ~LeastConnectionsLoadBalancer() = default;

    ServiceEndpoint selectEndpoint(const std::vector<ServiceEndpoint>& endpoints) override;
    void updateEndpoints(const std::vector<ServiceEndpoint>& endpoints) override;
    void markEndpointStatus(const std::string& endpoint_id, bool healthy) override;
    std::string getStrategyName() const override { return "LeastConnections"; }

    // 连接管理
    void incrementConnections(const std::string& endpoint_id);
    void decrementConnections(const std::string& endpoint_id);

private:
    mutable std::mutex endpoints_mutex_;
    std::map<std::string, ServiceEndpoint> endpoints_;
    std::map<std::string, int> connection_counts_;
};

// 加权轮询负载均衡器
class WeightedRoundRobinLoadBalancer : public LoadBalancer {
public:
    WeightedRoundRobinLoadBalancer();
    ~WeightedRoundRobinLoadBalancer() = default;

    ServiceEndpoint selectEndpoint(const std::vector<ServiceEndpoint>& endpoints) override;
    void updateEndpoints(const std::vector<ServiceEndpoint>& endpoints) override;
    void markEndpointStatus(const std::string& endpoint_id, bool healthy) override;
    std::string getStrategyName() const override { return "WeightedRoundRobin"; }

private:
    struct WeightedEndpoint {
        ServiceEndpoint endpoint;
        int weight;
        int current_weight;
    };

    mutable std::mutex endpoints_mutex_;
    std::vector<WeightedEndpoint> weighted_endpoints_;
    std::atomic<size_t> current_index_{0};
};

// 一致性哈希负载均衡器
class ConsistentHashLoadBalancer : public LoadBalancer {
public:
    ConsistentHashLoadBalancer(int virtual_nodes = 150);
    ~ConsistentHashLoadBalancer() = default;

    ServiceEndpoint selectEndpoint(const std::vector<ServiceEndpoint>& endpoints) override;
    void updateEndpoints(const std::vector<ServiceEndpoint>& endpoints) override;
    void markEndpointStatus(const std::string& endpoint_id, bool healthy) override;
    std::string getStrategyName() const override { return "ConsistentHash"; }

    // 根据键选择端点
    ServiceEndpoint selectEndpointByKey(const std::string& key,
                                       const std::vector<ServiceEndpoint>& endpoints);

private:
    struct HashNode {
        std::string key;
        ServiceEndpoint endpoint;
        uint32_t hash;
    };

    void buildHashRing();
    uint32_t hash(const std::string& key);
    ServiceEndpoint findEndpoint(uint32_t hash_value);

    int virtual_nodes_;
    mutable std::mutex ring_mutex_;
    std::vector<HashNode> hash_ring_;
    std::map<std::string, ServiceEndpoint> endpoints_;
};

// 最少响应时间负载均衡器
class LeastResponseTimeLoadBalancer : public LoadBalancer {
public:
    LeastResponseTimeLoadBalancer();
    ~LeastResponseTimeLoadBalancer() = default;

    ServiceEndpoint selectEndpoint(const std::vector<ServiceEndpoint>& endpoints) override;
    void updateEndpoints(const std::vector<ServiceEndpoint>& endpoints) override;
    void markEndpointStatus(const std::string& endpoint_id, bool healthy) override;
    std::string getStrategyName() const override { return "LeastResponseTime"; }

    // 更新响应时间
    void updateResponseTime(const std::string& endpoint_id,
                           std::chrono::milliseconds response_time);

private:
    struct EndpointStats {
        ServiceEndpoint endpoint;
        std::chrono::milliseconds avg_response_time{0};
        int request_count{0};
        std::chrono::steady_clock::time_point last_update;
    };

    mutable std::mutex stats_mutex_;
    std::map<std::string, EndpointStats> endpoint_stats_;
    std::chrono::milliseconds calculateAverageResponseTime(const std::string& endpoint_id);
};

// 负载均衡器工厂
class LoadBalancerFactory {
public:
    static std::unique_ptr<LoadBalancer> createLoadBalancer(LoadBalanceStrategy strategy);
    static std::vector<std::string> getAvailableStrategies();
};

} // namespace common
} // namespace agent_rpc
