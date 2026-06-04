#include "agent_rpc/registry/service_registry.h"
#include "agent_rpc/common/load_balancer.h"

#include <gtest/gtest.h>

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <mutex>
#include <vector>

namespace agent_rpc::tests {

namespace {

common::ServiceEndpoint makeEndpoint(const std::string& host,
                                     int port,
                                     const std::string& service_name = "rpc") {
    common::ServiceEndpoint endpoint;
    endpoint.host = host;
    endpoint.port = port;
    endpoint.service_name = service_name;
    endpoint.version = "1.0.0";
    endpoint.is_healthy = true;
    return endpoint;
}

}  // namespace

TEST(ServiceRegistryTest, MemoryRegistrySupportsRegisterDiscoverHeartbeatUnregister) {
    registry::MemoryServiceRegistry registry;
    auto endpoint = makeEndpoint("127.0.0.1", 5001, "rpc_server");
    const std::string service_id = endpoint.host + ":" + std::to_string(endpoint.port);

    EXPECT_TRUE(registry.registerService(endpoint));

    auto discovered = registry.discoverServices("rpc_server");
    ASSERT_EQ(discovered.size(), 1u);
    EXPECT_EQ(discovered.front().port, 5001);
    EXPECT_TRUE(registry.isServiceHealthy(service_id));
    EXPECT_TRUE(registry.updateHeartbeat(service_id));

    EXPECT_TRUE(registry.unregisterService(service_id));
    EXPECT_TRUE(registry.discoverServices("rpc_server").empty());
}

TEST(ServiceRegistryTest, MemoryRegistryNotifiesWatcherOnChanges) {
    registry::MemoryServiceRegistry registry;
    std::mutex mutex;
    std::condition_variable cv;
    std::vector<size_t> callback_sizes;

    registry.watchServices("rpc_server", [&](const std::vector<common::ServiceEndpoint>& endpoints) {
        std::lock_guard<std::mutex> lock(mutex);
        callback_sizes.push_back(endpoints.size());
        cv.notify_all();
    });

    auto endpoint = makeEndpoint("127.0.0.1", 5001, "rpc_server");
    const std::string service_id = endpoint.host + ":" + std::to_string(endpoint.port);

    EXPECT_TRUE(registry.registerService(endpoint));
    {
        std::unique_lock<std::mutex> lock(mutex);
        ASSERT_TRUE(cv.wait_for(lock, std::chrono::seconds(1), [&] {
            return callback_sizes.size() >= 1;
        }));
    }

    EXPECT_TRUE(registry.unregisterService(service_id));
    {
        std::unique_lock<std::mutex> lock(mutex);
        ASSERT_TRUE(cv.wait_for(lock, std::chrono::seconds(1), [&] {
            return callback_sizes.size() >= 2;
        }));
    }

    ASSERT_EQ(callback_sizes.size(), 2u);
    EXPECT_EQ(callback_sizes[0], 1u);
    EXPECT_EQ(callback_sizes[1], 0u);
}

TEST(ServiceRegistryTest, RegistryResultsCanFeedLoadBalancer) {
    registry::MemoryServiceRegistry registry;
    registry.registerService(makeEndpoint("127.0.0.1", 5001, "rpc_server"));
    registry.registerService(makeEndpoint("127.0.0.1", 5002, "rpc_server"));

    auto discovered = registry.discoverServices("rpc_server");
    ASSERT_EQ(discovered.size(), 2u);

    auto load_balancer =
        common::LoadBalancerFactory::createLoadBalancer(common::LoadBalanceStrategy::ROUND_ROBIN);

    auto first = load_balancer->selectEndpoint(discovered);
    auto second = load_balancer->selectEndpoint(discovered);

    EXPECT_NE(first.port, second.port);
}

}  // namespace agent_rpc::tests
