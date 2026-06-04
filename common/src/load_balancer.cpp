#include "agent_rpc/common/load_balancer.h"
#include "agent_rpc/common/logger.h"
#include <algorithm>
#include <random>
#include <set>
#include <climits>
#include <limits>

namespace agent_rpc {
namespace common {

// RoundRobinLoadBalancer 实现
RoundRobinLoadBalancer::RoundRobinLoadBalancer() = default;

ServiceEndpoint RoundRobinLoadBalancer::selectEndpoint(const std::vector<ServiceEndpoint>& endpoints) {
    if (endpoints.empty()) {
        throw std::runtime_error("No endpoints available");
    }
    std::lock_guard<std::mutex> lock(endpoints_mutex_);
    std::vector<ServiceEndpoint> healthy;
    for (const auto& ep : endpoints) {
        if (ep.is_healthy) healthy.push_back(ep);
    }
    if (healthy.empty()) throw std::runtime_error("No healthy endpoints available");
    size_t index = current_index_.fetch_add(1) % healthy.size();
    return healthy[index];
}

void RoundRobinLoadBalancer::updateEndpoints(const std::vector<ServiceEndpoint>& endpoints) {
    std::lock_guard<std::mutex> lock(endpoints_mutex_);
    healthy_endpoints_ = endpoints;
    current_index_ = 0;
}

void RoundRobinLoadBalancer::markEndpointStatus(const std::string& endpoint_id, bool healthy) {
    std::lock_guard<std::mutex> lock(endpoints_mutex_);
    for (auto& ep : healthy_endpoints_) {
        if (ep.host + ":" + std::to_string(ep.port) == endpoint_id) {
            ep.is_healthy = healthy;
            break;
        }
    }
}

// RandomLoadBalancer 实现
RandomLoadBalancer::RandomLoadBalancer() : gen_(rd_()) {}

ServiceEndpoint RandomLoadBalancer::selectEndpoint(const std::vector<ServiceEndpoint>& endpoints) {
    if (endpoints.empty()) throw std::runtime_error("No endpoints available");
    std::lock_guard<std::mutex> lock(endpoints_mutex_);
    std::vector<ServiceEndpoint> healthy;
    for (const auto& ep : endpoints) {
        if (ep.is_healthy) healthy.push_back(ep);
    }
    if (healthy.empty()) throw std::runtime_error("No healthy endpoints available");
    std::uniform_int_distribution<> dis(0, healthy.size() - 1);
    return healthy[dis(gen_)];
}

void RandomLoadBalancer::updateEndpoints(const std::vector<ServiceEndpoint>& endpoints) {
    std::lock_guard<std::mutex> lock(endpoints_mutex_);
    healthy_endpoints_ = endpoints;
}

void RandomLoadBalancer::markEndpointStatus(const std::string& endpoint_id, bool healthy) {
    std::lock_guard<std::mutex> lock(endpoints_mutex_);
    for (auto& ep : healthy_endpoints_) {
        if (ep.host + ":" + std::to_string(ep.port) == endpoint_id) {
            ep.is_healthy = healthy;
            break;
        }
    }
}

// LeastConnectionsLoadBalancer 实现
LeastConnectionsLoadBalancer::LeastConnectionsLoadBalancer() = default;

ServiceEndpoint LeastConnectionsLoadBalancer::selectEndpoint(const std::vector<ServiceEndpoint>& endpoints) {
    if (endpoints.empty()) throw std::runtime_error("No endpoints available");
    std::lock_guard<std::mutex> lock(endpoints_mutex_);
    ServiceEndpoint* best = nullptr;
    int min_conn = INT_MAX;
    for (const auto& ep : endpoints) {
        if (!ep.is_healthy) continue;
        std::string id = ep.host + ":" + std::to_string(ep.port);
        int conn = connection_counts_[id];
        if (conn < min_conn) {
            min_conn = conn;
            best = const_cast<ServiceEndpoint*>(&ep);
        }
    }
    if (!best) throw std::runtime_error("No healthy endpoints available");
    std::string id = best->host + ":" + std::to_string(best->port);
    connection_counts_[id]++;
    return *best;
}

void LeastConnectionsLoadBalancer::updateEndpoints(const std::vector<ServiceEndpoint>& endpoints) {
    std::lock_guard<std::mutex> lock(endpoints_mutex_);
    std::set<std::string> current;
    for (const auto& ep : endpoints) {
        current.insert(ep.host + ":" + std::to_string(ep.port));
    }
    auto it = connection_counts_.begin();
    while (it != connection_counts_.end()) {
        if (current.find(it->first) == current.end()) it = connection_counts_.erase(it);
        else ++it;
    }
    for (const auto& ep : endpoints) {
        endpoints_[ep.host + ":" + std::to_string(ep.port)] = ep;
    }
}

void LeastConnectionsLoadBalancer::markEndpointStatus(const std::string& endpoint_id, bool healthy) {
    std::lock_guard<std::mutex> lock(endpoints_mutex_);
    auto it = endpoints_.find(endpoint_id);
    if (it != endpoints_.end()) it->second.is_healthy = healthy;
}

void LeastConnectionsLoadBalancer::incrementConnections(const std::string& endpoint_id) {
    std::lock_guard<std::mutex> lock(endpoints_mutex_);
    connection_counts_[endpoint_id]++;
}

void LeastConnectionsLoadBalancer::decrementConnections(const std::string& endpoint_id) {
    std::lock_guard<std::mutex> lock(endpoints_mutex_);
    auto it = connection_counts_.find(endpoint_id);
    if (it != connection_counts_.end() && it->second > 0) it->second--;
}

// WeightedRoundRobinLoadBalancer 实现
WeightedRoundRobinLoadBalancer::WeightedRoundRobinLoadBalancer() = default;

ServiceEndpoint WeightedRoundRobinLoadBalancer::selectEndpoint(const std::vector<ServiceEndpoint>& endpoints) {
    if (endpoints.empty()) throw std::runtime_error("No endpoints available");
    std::lock_guard<std::mutex> lock(endpoints_mutex_);
    if (weighted_endpoints_.empty()) throw std::runtime_error("No weighted endpoints available");
    WeightedEndpoint* best = nullptr;
    int max_weight = std::numeric_limits<int>::min();
    int total_weight = 0;
    for (auto& we : weighted_endpoints_) {
        if (!we.endpoint.is_healthy) continue;
        we.current_weight += we.weight;
        total_weight += we.weight;
        if (we.current_weight > max_weight) {
            max_weight = we.current_weight;
            best = &we;
        }
    }
    if (!best) throw std::runtime_error("No healthy endpoints available");
    best->current_weight -= total_weight;
    return best->endpoint;
}

void WeightedRoundRobinLoadBalancer::updateEndpoints(const std::vector<ServiceEndpoint>& endpoints) {
    std::lock_guard<std::mutex> lock(endpoints_mutex_);
    weighted_endpoints_.clear();
    for (const auto& ep : endpoints) {
        WeightedEndpoint we;
        we.endpoint = ep;
        we.weight = 1;
        we.current_weight = 0;
        auto it = ep.metadata.find("weight");
        if (it != ep.metadata.end()) {
            try { we.weight = std::stoi(it->second); } catch (...) { we.weight = 1; }
        }
        weighted_endpoints_.push_back(we);
    }
    current_index_ = 0;
}

void WeightedRoundRobinLoadBalancer::markEndpointStatus(const std::string& endpoint_id, bool healthy) {
    std::lock_guard<std::mutex> lock(endpoints_mutex_);
    for (auto& we : weighted_endpoints_) {
        if (we.endpoint.host + ":" + std::to_string(we.endpoint.port) == endpoint_id) {
            we.endpoint.is_healthy = healthy;
            break;
        }
    }
}

// ConsistentHashLoadBalancer 实现
ConsistentHashLoadBalancer::ConsistentHashLoadBalancer(int virtual_nodes) : virtual_nodes_(virtual_nodes) {}

ServiceEndpoint ConsistentHashLoadBalancer::selectEndpoint(const std::vector<ServiceEndpoint>& endpoints) {
    if (endpoints.empty()) throw std::runtime_error("No endpoints available");
    std::lock_guard<std::mutex> lock(ring_mutex_);
    if (hash_ring_.empty()) throw std::runtime_error("Hash ring is empty");
    std::random_device rd;
    std::mt19937 gen(rd());
    std::uniform_int_distribution<> dis(0, 1000000);
    uint32_t hash_value = hash(std::to_string(dis(gen)));
    return findEndpoint(hash_value);
}

ServiceEndpoint ConsistentHashLoadBalancer::selectEndpointByKey(const std::string& key,
                                                               const std::vector<ServiceEndpoint>& endpoints) {
    if (endpoints.empty()) throw std::runtime_error("No endpoints available");
    std::lock_guard<std::mutex> lock(ring_mutex_);
    if (hash_ring_.empty()) throw std::runtime_error("Hash ring is empty");
    return findEndpoint(hash(key));
}

void ConsistentHashLoadBalancer::updateEndpoints(const std::vector<ServiceEndpoint>& endpoints) {
    std::lock_guard<std::mutex> lock(ring_mutex_);
    endpoints_.clear();
    for (const auto& ep : endpoints) {
        endpoints_[ep.host + ":" + std::to_string(ep.port)] = ep;
    }
    buildHashRing();
}

void ConsistentHashLoadBalancer::markEndpointStatus(const std::string& endpoint_id, bool healthy) {
    std::lock_guard<std::mutex> lock(ring_mutex_);
    auto it = endpoints_.find(endpoint_id);
    if (it != endpoints_.end()) {
        it->second.is_healthy = healthy;
        buildHashRing();
    }
}

void ConsistentHashLoadBalancer::buildHashRing() {
    hash_ring_.clear();
    for (const auto& pair : endpoints_) {
        if (!pair.second.is_healthy) continue;
        for (int i = 0; i < virtual_nodes_; ++i) {
            std::string vkey = pair.first + "#" + std::to_string(i);
            HashNode node;
            node.key = vkey;
            node.endpoint = pair.second;
            node.hash = hash(vkey);
            hash_ring_.push_back(node);
        }
    }
    std::sort(hash_ring_.begin(), hash_ring_.end(),
              [](const HashNode& a, const HashNode& b) { return a.hash < b.hash; });
}

uint32_t ConsistentHashLoadBalancer::hash(const std::string& key) {
    uint32_t h = 0;
    for (char c : key) h = h * 31 + c;
    return h;
}

ServiceEndpoint ConsistentHashLoadBalancer::findEndpoint(uint32_t hash_value) {
    if (hash_ring_.empty()) throw std::runtime_error("Hash ring is empty");
    auto it = std::lower_bound(hash_ring_.begin(), hash_ring_.end(), hash_value,
                              [](const HashNode& node, uint32_t value) { return node.hash < value; });
    if (it == hash_ring_.end()) it = hash_ring_.begin();
    return it->endpoint;
}

// LeastResponseTimeLoadBalancer 实现
LeastResponseTimeLoadBalancer::LeastResponseTimeLoadBalancer() = default;

ServiceEndpoint LeastResponseTimeLoadBalancer::selectEndpoint(const std::vector<ServiceEndpoint>& endpoints) {
    if (endpoints.empty()) throw std::runtime_error("No endpoints available");
    std::lock_guard<std::mutex> lock(stats_mutex_);
    ServiceEndpoint* best = nullptr;
    std::chrono::milliseconds min_time = std::chrono::milliseconds::max();
    for (const auto& ep : endpoints) {
        if (!ep.is_healthy) continue;
        std::string id = ep.host + ":" + std::to_string(ep.port);
        auto it = endpoint_stats_.find(id);
        if (it == endpoint_stats_.end()) {
            if (!best) best = const_cast<ServiceEndpoint*>(&ep);
        } else {
            auto rt = calculateAverageResponseTime(id);
            if (rt < min_time) {
                min_time = rt;
                best = const_cast<ServiceEndpoint*>(&ep);
            }
        }
    }
    if (!best) throw std::runtime_error("No healthy endpoints available");
    return *best;
}

void LeastResponseTimeLoadBalancer::updateEndpoints(const std::vector<ServiceEndpoint>& endpoints) {
    std::lock_guard<std::mutex> lock(stats_mutex_);
    std::set<std::string> current;
    for (const auto& ep : endpoints) current.insert(ep.host + ":" + std::to_string(ep.port));
    auto it = endpoint_stats_.begin();
    while (it != endpoint_stats_.end()) {
        if (current.find(it->first) == current.end()) it = endpoint_stats_.erase(it);
        else ++it;
    }
}

void LeastResponseTimeLoadBalancer::markEndpointStatus(const std::string& endpoint_id, bool healthy) {
    std::lock_guard<std::mutex> lock(stats_mutex_);
    auto it = endpoint_stats_.find(endpoint_id);
    if (it != endpoint_stats_.end()) it->second.endpoint.is_healthy = healthy;
}

void LeastResponseTimeLoadBalancer::updateResponseTime(const std::string& endpoint_id,
                                                      std::chrono::milliseconds response_time) {
    std::lock_guard<std::mutex> lock(stats_mutex_);
    auto& stats = endpoint_stats_[endpoint_id];
    stats.request_count++;
    stats.last_update = std::chrono::steady_clock::now();
    if (stats.avg_response_time.count() == 0) {
        stats.avg_response_time = response_time;
    } else {
        stats.avg_response_time = std::chrono::milliseconds(
            static_cast<long long>(stats.avg_response_time.count() * 0.8 + response_time.count() * 0.2));
    }
}

std::chrono::milliseconds LeastResponseTimeLoadBalancer::calculateAverageResponseTime(const std::string& endpoint_id) {
    auto it = endpoint_stats_.find(endpoint_id);
    if (it == endpoint_stats_.end()) return std::chrono::milliseconds(1000);
    return it->second.avg_response_time;
}

// LoadBalancerFactory 实现
std::unique_ptr<LoadBalancer> LoadBalancerFactory::createLoadBalancer(LoadBalanceStrategy strategy) {
    switch (strategy) {
        case LoadBalanceStrategy::ROUND_ROBIN: return std::make_unique<RoundRobinLoadBalancer>();
        case LoadBalanceStrategy::RANDOM: return std::make_unique<RandomLoadBalancer>();
        case LoadBalanceStrategy::LEAST_CONNECTIONS: return std::make_unique<LeastConnectionsLoadBalancer>();
        case LoadBalanceStrategy::WEIGHTED_ROUND_ROBIN: return std::make_unique<WeightedRoundRobinLoadBalancer>();
        case LoadBalanceStrategy::CONSISTENT_HASH: return std::make_unique<ConsistentHashLoadBalancer>();
        case LoadBalanceStrategy::LEAST_RESPONSE_TIME: return std::make_unique<LeastResponseTimeLoadBalancer>();
        default: return std::make_unique<RoundRobinLoadBalancer>();
    }
}

std::vector<std::string> LoadBalancerFactory::getAvailableStrategies() {
    return {"RoundRobin", "Random", "LeastConnections", "WeightedRoundRobin", "ConsistentHash", "LeastResponseTime"};
}

} // namespace common
} // namespace agent_rpc
