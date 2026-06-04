#include "agent_rpc/common/circuit_breaker.h"
#include "agent_rpc/common/load_balancer.h"
#include "agent_rpc/common/metrics.h"

#include <gtest/gtest.h>

#include <chrono>
#include <thread>
#include <vector>

namespace agent_rpc::tests {

namespace {

common::ServiceEndpoint makeEndpoint(const std::string& host,
                                     int port,
                                     const std::string& service_name = "rpc",
                                     bool healthy = true,
                                     const std::map<std::string, std::string>& metadata = {}) {
    common::ServiceEndpoint endpoint;
    endpoint.host = host;
    endpoint.port = port;
    endpoint.service_name = service_name;
    endpoint.version = "1.0.0";
    endpoint.is_healthy = healthy;
    endpoint.metadata = metadata;
    return endpoint;
}

std::string endpointId(const common::ServiceEndpoint& endpoint) {
    return endpoint.host + ":" + std::to_string(endpoint.port);
}

}  // namespace

TEST(CircuitBreakerTest, TransitionsFromOpenToHalfOpenToClosed) {
    common::CircuitBreakerConfig config;
    config.failure_threshold = 2;
    config.success_threshold = 1;
    config.timeout = std::chrono::milliseconds(20);
    config.min_request_count = 1000;

    common::CircuitBreaker breaker(config);

    breaker.recordFailure();
    breaker.recordFailure();
    EXPECT_EQ(breaker.getState(), common::CircuitState::OPEN);
    EXPECT_FALSE(breaker.isRequestAllowed());

    std::this_thread::sleep_for(std::chrono::milliseconds(25));
    EXPECT_TRUE(breaker.isRequestAllowed());
    EXPECT_EQ(breaker.getState(), common::CircuitState::HALF_OPEN);

    breaker.recordSuccess();
    EXPECT_EQ(breaker.getState(), common::CircuitState::CLOSED);
}

TEST(CircuitBreakerManagerTest, ReusesBreakerPerServiceName) {
    auto& manager = common::CircuitBreakerManager::getInstance();
    manager.resetAll();

    auto first = manager.getCircuitBreaker("service-a");
    auto second = manager.getCircuitBreaker("service-a");
    auto third = manager.getCircuitBreaker("service-b");

    EXPECT_EQ(first.get(), second.get());
    EXPECT_NE(first.get(), third.get());
}

TEST(LoadBalancerTest, RoundRobinAlternatesAcrossHealthyEndpoints) {
    common::RoundRobinLoadBalancer load_balancer;
    std::vector<common::ServiceEndpoint> endpoints = {
        makeEndpoint("127.0.0.1", 5001),
        makeEndpoint("127.0.0.1", 5002)
    };

    auto first = load_balancer.selectEndpoint(endpoints);
    auto second = load_balancer.selectEndpoint(endpoints);
    auto third = load_balancer.selectEndpoint(endpoints);

    EXPECT_EQ(endpointId(first), "127.0.0.1:5001");
    EXPECT_EQ(endpointId(second), "127.0.0.1:5002");
    EXPECT_EQ(endpointId(third), "127.0.0.1:5001");
}

TEST(LoadBalancerTest, RandomReturnsOneOfAvailableEndpoints) {
    common::RandomLoadBalancer load_balancer;
    std::vector<common::ServiceEndpoint> endpoints = {
        makeEndpoint("127.0.0.1", 5001),
        makeEndpoint("127.0.0.1", 5002)
    };

    auto selected = load_balancer.selectEndpoint(endpoints);
    EXPECT_TRUE(endpointId(selected) == "127.0.0.1:5001" ||
                endpointId(selected) == "127.0.0.1:5002");
}

TEST(LoadBalancerTest, LeastConnectionsPrefersLessBusyEndpoint) {
    common::LeastConnectionsLoadBalancer load_balancer;
    std::vector<common::ServiceEndpoint> endpoints = {
        makeEndpoint("127.0.0.1", 5001),
        makeEndpoint("127.0.0.1", 5002)
    };

    load_balancer.updateEndpoints(endpoints);
    load_balancer.incrementConnections("127.0.0.1:5001");
    load_balancer.incrementConnections("127.0.0.1:5001");
    load_balancer.incrementConnections("127.0.0.1:5002");

    auto selected = load_balancer.selectEndpoint(endpoints);
    EXPECT_EQ(endpointId(selected), "127.0.0.1:5002");
}

TEST(LoadBalancerTest, WeightedRoundRobinHonorsMetadataWeights) {
    common::WeightedRoundRobinLoadBalancer load_balancer;
    std::vector<common::ServiceEndpoint> endpoints = {
        makeEndpoint("127.0.0.1", 5001, "rpc", true, {{"weight", "3"}}),
        makeEndpoint("127.0.0.1", 5002, "rpc", true, {{"weight", "1"}})
    };

    load_balancer.updateEndpoints(endpoints);

    int first_count = 0;
    int second_count = 0;
    for (int i = 0; i < 8; ++i) {
        auto selected = load_balancer.selectEndpoint(endpoints);
        if (selected.port == 5001) {
            first_count++;
        } else if (selected.port == 5002) {
            second_count++;
        }
    }

    EXPECT_EQ(first_count, 6);
    EXPECT_EQ(second_count, 2);
}

TEST(LoadBalancerTest, ConsistentHashReturnsSameEndpointForSameKey) {
    common::ConsistentHashLoadBalancer load_balancer;
    std::vector<common::ServiceEndpoint> endpoints = {
        makeEndpoint("127.0.0.1", 5001),
        makeEndpoint("127.0.0.1", 5002)
    };

    load_balancer.updateEndpoints(endpoints);

    auto first = load_balancer.selectEndpointByKey("user-42", endpoints);
    auto second = load_balancer.selectEndpointByKey("user-42", endpoints);

    EXPECT_EQ(endpointId(first), endpointId(second));
}

TEST(LoadBalancerTest, LeastResponseTimePrefersFastEndpoint) {
    common::LeastResponseTimeLoadBalancer load_balancer;
    std::vector<common::ServiceEndpoint> endpoints = {
        makeEndpoint("127.0.0.1", 5001),
        makeEndpoint("127.0.0.1", 5002)
    };

    load_balancer.updateResponseTime("127.0.0.1:5001", std::chrono::milliseconds(50));
    load_balancer.updateResponseTime("127.0.0.1:5002", std::chrono::milliseconds(10));

    auto selected = load_balancer.selectEndpoint(endpoints);
    EXPECT_EQ(endpointId(selected), "127.0.0.1:5002");
}

TEST(MetricsTest, HistogramTracksSumAndCount) {
    common::HistogramMetric histogram("rpc_duration_ms", "RPC duration");

    histogram.observe(12.0);
    histogram.observe(8.0);

    EXPECT_EQ(histogram.getCount(), 2u);
    EXPECT_DOUBLE_EQ(histogram.getSum(), 20.0);
}

TEST(MetricsTest, CollectorExportsPrometheusMetadata) {
    auto& collector = common::MetricsCollector::getInstance();
    collector.removeMetric("test_counter_total");

    auto counter = collector.createCounter("test_counter_total", "Test counter");
    counter->increment();

    const auto exported = collector.exportPrometheus();
    EXPECT_NE(exported.find("# HELP test_counter_total"), std::string::npos);
    EXPECT_NE(exported.find("# TYPE test_counter_total counter"), std::string::npos);
    EXPECT_NE(exported.find("test_counter_total 1"), std::string::npos);
}

}  // namespace agent_rpc::tests
