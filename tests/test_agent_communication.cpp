#include "agent_rpc/client/rpc_client.h"
#include "agent_rpc/common/logger.h"
#include "agent_rpc/registry/service_registry.h"
#include "agent_rpc/server/rpc_server.h"

#include <gtest/gtest.h>

#include <atomic>
#include <chrono>
#include <memory>
#include <string>
#include <thread>

namespace agent_rpc::tests {

namespace {

std::atomic<int> g_port_seed{56100};

common::ServiceEndpoint makeAgent(const std::string& host,
                                  int port,
                                  const std::string& service_name) {
    common::ServiceEndpoint endpoint;
    endpoint.host = host;
    endpoint.port = port;
    endpoint.service_name = service_name;
    endpoint.version = "1.0.0";
    endpoint.is_healthy = true;
    endpoint.last_heartbeat = std::chrono::steady_clock::now();
    return endpoint;
}

class AgentCommunicationIntegrationTest : public ::testing::Test {
protected:
    void SetUp() override {
        common::LogConfig log_config;
        log_config.level = common::LogLevel::Level_ERROR;
        log_config.async_logging = false;
        log_config.color_output = false;
        common::initializeAdvancedLogger(log_config);

        port_ = g_port_seed.fetch_add(1);
        server_address_ = "127.0.0.1:" + std::to_string(port_);

        common::RpcConfig server_config;
        server_config.server_address = server_address_;
        server_config.timeout_seconds = 5;
        server_config.heartbeat_interval = 1;

        server_ = std::make_unique<server::RpcServer>();
        ASSERT_TRUE(server_->initialize(server_config));
        ASSERT_TRUE(server_->start());

        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }

    void TearDown() override {
        if (server_) {
            server_->stop();
        }
    }

    std::unique_ptr<client::RpcClient> makeInitializedClient() {
        auto client = std::make_unique<client::RpcClient>();
        common::RpcConfig config;
        config.timeout_seconds = 5;
        config.heartbeat_interval = 1;
        EXPECT_TRUE(client->initialize(config));
        return client;
    }

    int port_{0};
    std::string server_address_;
    std::unique_ptr<server::RpcServer> server_;
};

}  // namespace

TEST_F(AgentCommunicationIntegrationTest, RegisterSendReceiveAndUnregisterAgents) {
    auto client = makeInitializedClient();
    ASSERT_TRUE(client->connect(server_address_));

    auto sender = makeAgent("127.0.0.1", 7001, "sender");
    auto receiver = makeAgent("127.0.0.1", 7002, "receiver");

    const std::string sender_id = client->registerAgent(sender, 1);
    const std::string receiver_id = client->registerAgent(receiver, 1);

    ASSERT_FALSE(sender_id.empty());
    ASSERT_FALSE(receiver_id.empty());

    auto agents = client->getAgents("", 10, 0);
    ASSERT_EQ(agents.size(), 2u);

    EXPECT_TRUE(client->sendMessage("hello", receiver_id, 5));

    auto messages = client->receiveMessages(receiver_id, 10, 5);
    ASSERT_EQ(messages.size(), 1u);
    EXPECT_EQ(messages.front(), "hello");

    EXPECT_EQ(client->broadcastMessage("broadcast", {}, false), 2);
    auto sender_messages = client->receiveMessages(sender_id, 10, 5);
    auto receiver_messages = client->receiveMessages(receiver_id, 10, 5);
    EXPECT_EQ(sender_messages.size(), 1u);
    EXPECT_EQ(receiver_messages.size(), 1u);
    EXPECT_EQ(sender_messages.front(), "broadcast");
    EXPECT_EQ(receiver_messages.front(), "broadcast");

    EXPECT_TRUE(client->sendHeartbeat(sender_id, sender));
    EXPECT_TRUE(client->unregisterAgent(sender_id, "done"));
    EXPECT_TRUE(client->unregisterAgent(receiver_id, "done"));

    EXPECT_TRUE(client->getAgents("", 10, 0).empty());
}

TEST_F(AgentCommunicationIntegrationTest, CanConnectViaInjectedMemoryRegistry) {
    auto registry = std::make_shared<registry::MemoryServiceRegistry>();
    auto service_endpoint = makeAgent("127.0.0.1", port_, "rpc_server");
    ASSERT_TRUE(registry->registerService(service_endpoint));

    auto client = makeInitializedClient();
    client->setServiceRegistry(registry);
    ASSERT_TRUE(client->connectViaRegistry(
        "memory", "rpc_server", common::LoadBalanceStrategy::ROUND_ROBIN));

    auto agent = makeAgent("127.0.0.1", 7101, "registry-client");
    const std::string agent_id = client->registerAgent(agent, 1);
    ASSERT_FALSE(agent_id.empty());

    auto agents = client->getAgents("", 10, 0);
    ASSERT_EQ(agents.size(), 1u);
    EXPECT_EQ(agents.front().service_name, "registry-client");

    EXPECT_TRUE(client->unregisterAgent(agent_id, "cleanup"));
}

}  // namespace agent_rpc::tests
