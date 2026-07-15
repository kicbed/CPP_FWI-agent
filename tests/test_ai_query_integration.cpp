/**
 * @file test_ai_query_integration.cpp
 * @brief Integration tests for AI Query Service
 * 
 * Task 17: 集成测试
 * Requirements: 2.1, 2.2, 2.3, 2.4, 2.5
 */

#include <gtest/gtest.h>
#include <rapidcheck.h>
#include <rapidcheck/gtest.h>

#include "agent_rpc/server/ai_query_service.h"
#include "agent_rpc/server/http_bridge.h"
#include "agent_rpc/client/ai_query_client.h"
#include "agent_rpc/a2a_adapter/a2a_adapter.h"
#include "agent_rpc/a2a_adapter/a2a_config.h"
#include "agent_rpc/a2a_adapter/a2a_metrics.h"
#include "agent_rpc/a2a_adapter/retry_policy.h"

#include "ai_query.grpc.pb.h"
#include "ai_query.pb.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
#include <thread>
#include <chrono>
#include <atomic>
#include <memory>
#include <mutex>
#include <stdexcept>

namespace agent_rpc {
namespace tests {

// ============================================================================
// Test Fixtures
// ============================================================================

class AIQueryIntegrationTest : public ::testing::Test {
protected:
    void SetUp() override {
        // Reset metrics before each test
        a2a_adapter::A2AMetrics::getInstance().reset();
    }
    
    void TearDown() override {
    }
};

namespace {

int reserveLoopbackPort() {
    const int socket_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (socket_fd < 0) throw std::runtime_error("failed to create test socket");

    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    address.sin_port = htons(0);
    if (bind(socket_fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) < 0) {
        close(socket_fd);
        throw std::runtime_error("failed to reserve test port");
    }

    socklen_t address_length = sizeof(address);
    if (getsockname(socket_fd, reinterpret_cast<sockaddr*>(&address),
                    &address_length) < 0) {
        close(socket_fd);
        throw std::runtime_error("failed to read reserved test port");
    }
    const int port = ntohs(address.sin_port);
    close(socket_fd);
    return port;
}

std::string postJsonToBridge(int port, const std::string& body) {
    const int socket_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (socket_fd < 0) throw std::runtime_error("failed to create HTTP socket");

    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    address.sin_port = htons(static_cast<uint16_t>(port));
    if (connect(socket_fd, reinterpret_cast<sockaddr*>(&address),
                sizeof(address)) < 0) {
        close(socket_fd);
        throw std::runtime_error("failed to connect to HTTP bridge");
    }

    const std::string request =
        "POST /api/query HTTP/1.1\r\n"
        "Host: 127.0.0.1\r\n"
        "Content-Type: application/json\r\n"
        "Connection: close\r\n"
        "Content-Length: " + std::to_string(body.size()) + "\r\n\r\n" + body;
    std::size_t sent = 0;
    while (sent < request.size()) {
        const ssize_t count = send(socket_fd, request.data() + sent,
                                   request.size() - sent, MSG_NOSIGNAL);
        if (count <= 0) {
            close(socket_fd);
            throw std::runtime_error("failed to send HTTP request");
        }
        sent += static_cast<std::size_t>(count);
    }
    shutdown(socket_fd, SHUT_WR);

    std::string response;
    char buffer[4096];
    for (;;) {
        const ssize_t count = recv(socket_fd, buffer, sizeof(buffer), 0);
        if (count < 0) {
            close(socket_fd);
            throw std::runtime_error("failed to read HTTP response");
        }
        if (count == 0) break;
        response.append(buffer, static_cast<std::size_t>(count));
    }
    close(socket_fd);
    return response;
}

class CapturingAIQueryService final
    : public agent_communication::AIQueryService::Service {
public:
    grpc::Status Query(
        grpc::ServerContext*,
        const agent_communication::AIQueryRequest* request,
        agent_communication::AIQueryResponse* response) override {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            last_request_ = *request;
            ++calls_;
        }
        response->set_request_id(request->request_id());
        response->set_answer("ok");
        response->set_context_id(request->context_id());
        response->mutable_status()->set_code(0);
        return grpc::Status::OK;
    }

    agent_communication::AIQueryRequest lastRequest() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return last_request_;
    }

    int calls() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return calls_;
    }

private:
    mutable std::mutex mutex_;
    agent_communication::AIQueryRequest last_request_;
    int calls_ = 0;
};

class HttpBridgePolicyTest : public ::testing::Test {
protected:
    void SetUp() override {
        grpc::ServerBuilder builder;
        builder.AddListeningPort(
            "127.0.0.1:0", grpc::InsecureServerCredentials(), &grpc_port_);
        builder.RegisterService(&service_);
        grpc_server_ = builder.BuildAndStart();
        ASSERT_NE(grpc_server_, nullptr);
        ASSERT_GT(grpc_port_, 0);

        http_port_ = reserveLoopbackPort();
        bridge_ = std::make_unique<agent_rpc::server::HttpBridge>();
        ASSERT_TRUE(bridge_->start(
            http_port_, "127.0.0.1:" + std::to_string(grpc_port_)));
    }

    void TearDown() override {
        if (bridge_) bridge_->stop();
        if (grpc_server_) {
            grpc_server_->Shutdown();
            grpc_server_->Wait();
        }
    }

    CapturingAIQueryService service_;
    std::unique_ptr<grpc::Server> grpc_server_;
    std::unique_ptr<agent_rpc::server::HttpBridge> bridge_;
    int grpc_port_ = 0;
    int http_port_ = 0;
};

TEST_F(HttpBridgePolicyTest, FalseSourceSwitchBecomesGrpcMetadata) {
    const std::string response = postJsonToBridge(
        http_port_,
        R"({"question":"ordinary Web chat","context_id":"ctx-web","allow_legacy_fwi_submit":false})");

    EXPECT_NE(response.find("HTTP/1.1 200 OK"), std::string::npos);
    ASSERT_EQ(service_.calls(), 1);
    const auto captured = service_.lastRequest();
    ASSERT_EQ(captured.metadata().count("allow_legacy_fwi_submit"), 1U);
    EXPECT_EQ(captured.metadata().at("allow_legacy_fwi_submit"), "false");
}

TEST_F(HttpBridgePolicyTest, MissingSourceSwitchPreservesOldClientBehavior) {
    const std::string response = postJsonToBridge(
        http_port_,
        R"({"question":"old HTTP client","context_id":"ctx-old"})");

    EXPECT_NE(response.find("HTTP/1.1 200 OK"), std::string::npos);
    ASSERT_EQ(service_.calls(), 1);
    EXPECT_EQ(service_.lastRequest().metadata().count(
                  "allow_legacy_fwi_submit"),
              0U);
}

TEST_F(HttpBridgePolicyTest, ExplicitTrueOrWrongTypeIsRejectedBeforeGrpc) {
    for (const char* body : {
             R"({"question":"run FWI","allow_legacy_fwi_submit":true})",
             R"({"question":"run FWI","allow_legacy_fwi_submit":"false"})",
         }) {
        const std::string response = postJsonToBridge(http_port_, body);
        EXPECT_NE(response.find("HTTP/1.1 400 Bad Request"), std::string::npos);
        EXPECT_NE(response.find("invalid_allow_legacy_fwi_submit"),
                  std::string::npos);
    }
    EXPECT_EQ(service_.calls(), 0);
}

} // namespace

// ============================================================================
// A2A Adapter Tests
// ============================================================================

TEST_F(AIQueryIntegrationTest, A2AAdapterInitialization) {
    a2a_adapter::A2AAdapter adapter;
    a2a_adapter::A2AConfig config;
    config.orchestrator_url = "http://localhost:5000";
    
    // Should initialize successfully
    EXPECT_TRUE(adapter.initialize(config));
    EXPECT_TRUE(adapter.isAvailable());
    
    // Shutdown should work
    adapter.shutdown();
    EXPECT_FALSE(adapter.isAvailable());
}

TEST_F(AIQueryIntegrationTest, A2AConfigValidation) {
    a2a_adapter::A2AConfig config;
    
    // Default config should be valid
    EXPECT_TRUE(config.validate());
    
    // Invalid port should be corrected
    config.orchestrator_port = -1;
    EXPECT_FALSE(config.validate());
    EXPECT_EQ(config.orchestrator_port, 5000);
    
    // Invalid timeout should be corrected
    config.request_timeout_seconds = 0;
    EXPECT_FALSE(config.validate());
    EXPECT_EQ(config.request_timeout_seconds, 30);
}

TEST_F(AIQueryIntegrationTest, A2AConfigDefaults) {
    auto config = a2a_adapter::A2AConfig::getDefault();
    
    EXPECT_EQ(config.orchestrator_url, "http://localhost:5000");
    EXPECT_EQ(config.orchestrator_port, 5000);
    EXPECT_EQ(config.registry_url, "http://localhost:8500");
    EXPECT_EQ(config.request_timeout_seconds, 30);
    EXPECT_EQ(config.max_retries, 3);
}

// ============================================================================
// Metrics Tests
// ============================================================================

TEST_F(AIQueryIntegrationTest, MetricsRecording) {
    auto& metrics = a2a_adapter::A2AMetrics::getInstance();
    
    // Record some queries
    metrics.recordQueryRequest("agent-1", false);
    metrics.recordQueryRequest("agent-2", true);
    metrics.recordQueryComplete("agent-1", 100, true);
    metrics.recordQueryComplete("agent-2", 200, false);
    
    EXPECT_EQ(metrics.getTotalQueries(), 2);
    EXPECT_EQ(metrics.getSuccessfulQueries(), 1);
    EXPECT_EQ(metrics.getFailedQueries(), 1);
}

TEST_F(AIQueryIntegrationTest, MetricsAverageLatency) {
    auto& metrics = a2a_adapter::A2AMetrics::getInstance();
    
    metrics.recordQueryRequest("agent-1", false);
    metrics.recordQueryComplete("agent-1", 100, true);
    
    metrics.recordQueryRequest("agent-2", false);
    metrics.recordQueryComplete("agent-2", 200, true);
    
    // Average should be (100 + 200) / 2 = 150
    EXPECT_DOUBLE_EQ(metrics.getAverageQueryLatency(), 150.0);
}

TEST_F(AIQueryIntegrationTest, MetricsTaskTracking) {
    auto& metrics = a2a_adapter::A2AMetrics::getInstance();
    
    metrics.recordTaskCreated("task-1");
    metrics.recordTaskCreated("task-2");
    
    EXPECT_EQ(metrics.getActiveTasks(), 2);
    
    metrics.recordTaskComplete("task-1", 100, true);
    
    EXPECT_EQ(metrics.getActiveTasks(), 1);
}

TEST_F(AIQueryIntegrationTest, MetricsAgentTracking) {
    auto& metrics = a2a_adapter::A2AMetrics::getInstance();
    
    metrics.recordAgentRegistered("agent-1");
    metrics.recordAgentRegistered("agent-2");
    
    EXPECT_EQ(metrics.getRegisteredAgents(), 2);
    
    metrics.recordAgentUnregistered("agent-1");
    
    EXPECT_EQ(metrics.getRegisteredAgents(), 1);
}

TEST_F(AIQueryIntegrationTest, MetricsJsonExport) {
    auto& metrics = a2a_adapter::A2AMetrics::getInstance();
    
    metrics.recordQueryRequest("agent-1", false);
    metrics.recordQueryComplete("agent-1", 100, true);
    
    std::string json = metrics.exportJson();
    
    // Should contain expected fields
    EXPECT_NE(json.find("queries"), std::string::npos);
    EXPECT_NE(json.find("tasks"), std::string::npos);
    EXPECT_NE(json.find("agents"), std::string::npos);
    EXPECT_NE(json.find("connections"), std::string::npos);
}

// ============================================================================
// Retry Policy Tests
// ============================================================================

TEST_F(AIQueryIntegrationTest, RetryPolicySuccess) {
    a2a_adapter::RetryPolicy policy;
    
    int call_count = 0;
    auto result = policy.execute<int>([&call_count]() {
        call_count++;
        return 42;
    });
    
    EXPECT_TRUE(result.success);
    EXPECT_EQ(result.value, 42);
    EXPECT_EQ(result.attempts, 1);
    EXPECT_EQ(call_count, 1);
}

TEST_F(AIQueryIntegrationTest, RetryPolicyRetryOnFailure) {
    a2a_adapter::RetryConfig config;
    config.max_retries = 3;
    config.initial_delay_ms = 10;  // Short delay for testing
    
    a2a_adapter::RetryPolicy policy(config);
    
    int call_count = 0;
    auto result = policy.execute<int>(
        [&call_count]() -> int {
            call_count++;
            if (call_count < 3) {
                throw std::runtime_error("temporary error");
            }
            return 42;
        },
        [](const std::exception&) { return true; }  // Always retry
    );
    
    EXPECT_TRUE(result.success);
    EXPECT_EQ(result.value, 42);
    EXPECT_EQ(result.attempts, 3);
    EXPECT_EQ(call_count, 3);
}

TEST_F(AIQueryIntegrationTest, RetryPolicyMaxRetries) {
    a2a_adapter::RetryConfig config;
    config.max_retries = 2;
    config.initial_delay_ms = 10;
    
    a2a_adapter::RetryPolicy policy(config);
    
    int call_count = 0;
    auto result = policy.execute<int>(
        [&call_count]() -> int {
            call_count++;
            throw std::runtime_error("persistent error");
        },
        [](const std::exception&) { return true; }
    );
    
    EXPECT_FALSE(result.success);
    EXPECT_EQ(result.attempts, 3);  // Initial + 2 retries
    EXPECT_EQ(call_count, 3);
    EXPECT_NE(result.last_error.find("persistent error"), std::string::npos);
}

TEST_F(AIQueryIntegrationTest, RetryPolicyNoRetryOnNonRetryable) {
    a2a_adapter::RetryConfig config;
    config.max_retries = 3;
    config.initial_delay_ms = 10;
    
    a2a_adapter::RetryPolicy policy(config);
    
    int call_count = 0;
    auto result = policy.execute<int>(
        [&call_count]() -> int {
            call_count++;
            throw std::runtime_error("invalid argument");
        },
        [](const std::exception&) { return false; }  // Never retry
    );
    
    EXPECT_FALSE(result.success);
    EXPECT_EQ(result.attempts, 1);
    EXPECT_EQ(call_count, 1);
}

TEST_F(AIQueryIntegrationTest, IsRetryableError) {
    EXPECT_TRUE(a2a_adapter::isRetryableError("connection refused"));
    EXPECT_TRUE(a2a_adapter::isRetryableError("timeout exceeded"));
    EXPECT_TRUE(a2a_adapter::isRetryableError("service unavailable"));
    EXPECT_TRUE(a2a_adapter::isRetryableError("UNAVAILABLE"));
    EXPECT_TRUE(a2a_adapter::isRetryableError("DEADLINE_EXCEEDED"));
    
    EXPECT_FALSE(a2a_adapter::isRetryableError("invalid argument"));
    EXPECT_FALSE(a2a_adapter::isRetryableError("not found"));
}

// ============================================================================
// Protobuf Message Tests
// ============================================================================

TEST_F(AIQueryIntegrationTest, AIQueryRequestSerialization) {
    agent_communication::AIQueryRequest request;
    request.set_request_id("test-123");
    request.set_question("What is 2+2?");
    request.set_context_id("ctx-456");
    request.set_timeout_seconds(30);
    
    // Serialize
    std::string serialized;
    EXPECT_TRUE(request.SerializeToString(&serialized));
    
    // Deserialize
    agent_communication::AIQueryRequest deserialized;
    EXPECT_TRUE(deserialized.ParseFromString(serialized));
    
    EXPECT_EQ(deserialized.request_id(), "test-123");
    EXPECT_EQ(deserialized.question(), "What is 2+2?");
    EXPECT_EQ(deserialized.context_id(), "ctx-456");
    EXPECT_EQ(deserialized.timeout_seconds(), 30);
}

TEST_F(AIQueryIntegrationTest, AIQueryResponseSerialization) {
    agent_communication::AIQueryResponse response;
    response.set_request_id("test-123");
    response.set_answer("The answer is 4");
    response.set_agent_id("math-agent");
    response.set_agent_name("Math Expert");
    response.set_task_id("task-789");
    response.set_context_id("ctx-456");
    response.set_processing_time_ms(150);
    
    auto* status = response.mutable_status();
    status->set_code(0);
    status->set_message("OK");
    
    // Serialize
    std::string serialized;
    EXPECT_TRUE(response.SerializeToString(&serialized));
    
    // Deserialize
    agent_communication::AIQueryResponse deserialized;
    EXPECT_TRUE(deserialized.ParseFromString(serialized));
    
    EXPECT_EQ(deserialized.request_id(), "test-123");
    EXPECT_EQ(deserialized.answer(), "The answer is 4");
    EXPECT_EQ(deserialized.agent_id(), "math-agent");
    EXPECT_EQ(deserialized.processing_time_ms(), 150);
}

TEST_F(AIQueryIntegrationTest, AIStreamEventSerialization) {
    agent_communication::AIStreamEvent event;
    event.set_event_id("evt-001");
    event.set_event_type("partial");
    event.set_content("The answer");
    event.set_task_state("running");
    event.set_context_id("ctx-456");
    event.set_timestamp(1234567890);
    
    // Serialize
    std::string serialized;
    EXPECT_TRUE(event.SerializeToString(&serialized));
    
    // Deserialize
    agent_communication::AIStreamEvent deserialized;
    EXPECT_TRUE(deserialized.ParseFromString(serialized));
    
    EXPECT_EQ(deserialized.event_id(), "evt-001");
    EXPECT_EQ(deserialized.event_type(), "partial");
    EXPECT_EQ(deserialized.content(), "The answer");
}

// ============================================================================
// Property-Based Tests
// ============================================================================

/**
 * Property: Metrics counters are monotonically increasing
 * Validates: Requirements 10.5
 */
RC_GTEST_PROP(AIQueryIntegration, MetricsMonotonicallyIncreasing, ()) {
    auto& metrics = a2a_adapter::A2AMetrics::getInstance();
    metrics.reset();
    
    uint64_t prev_total = 0;
    
    for (int i = 0; i < 10; ++i) {
        metrics.recordQueryRequest("agent", false);
        uint64_t current = metrics.getTotalQueries();
        RC_ASSERT(current >= prev_total);
        prev_total = current;
    }
}

/**
 * Property: Retry policy respects max retries
 * Validates: Requirements 10.2
 */
RC_GTEST_PROP(AIQueryIntegration, RetryPolicyRespectsMaxRetries, ()) {
    int max_retries = *rc::gen::inRange(0, 5);
    
    a2a_adapter::RetryConfig config;
    config.max_retries = max_retries;
    config.initial_delay_ms = 1;  // Minimal delay for testing
    
    a2a_adapter::RetryPolicy policy(config);
    
    int call_count = 0;
    auto result = policy.execute<int>(
        [&call_count]() -> int {
            call_count++;
            throw std::runtime_error("error");
        },
        [](const std::exception&) { return true; }
    );
    
    RC_ASSERT(!result.success);
    RC_ASSERT(result.attempts == max_retries + 1);
    RC_ASSERT(call_count == max_retries + 1);
}

/**
 * Property: Config validation always produces valid config
 * Validates: Requirements 9.5
 */
RC_GTEST_PROP(AIQueryIntegration, ConfigValidationProducesValidConfig, ()) {
    a2a_adapter::A2AConfig config;
    
    // Set potentially invalid values
    config.orchestrator_port = *rc::gen::inRange(-100, 100000);
    config.request_timeout_seconds = *rc::gen::inRange(-100, 1000);
    config.max_retries = *rc::gen::inRange(-10, 100);
    
    // Validate (may modify config)
    config.validate();
    
    // After validation, all values should be valid
    RC_ASSERT(config.orchestrator_port > 0);
    RC_ASSERT(config.orchestrator_port <= 65535);
    RC_ASSERT(config.request_timeout_seconds > 0);
    RC_ASSERT(config.max_retries >= 0);
}

} // namespace tests
} // namespace agent_rpc

int main(int argc, char** argv) {
    ::testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}
