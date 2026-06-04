/**
 * @file test_mcp_integration.cpp
 * @brief MCP Agent Integration Tests
 * 
 * Tests for:
 * - Task 20.1: MCPAgentIntegration initialization and shutdown
 * - Task 20.2: MCP error handling (server unavailable, timeouts)
 * 
 * **Feature: a2a-integration, Task 20: MCP 集成测试**
 * **Validates: Requirements 12.1, 12.3, 12.5**
 */

#include <gtest/gtest.h>
#include <rapidcheck.h>
#include <rapidcheck/gtest.h>

#include "agent_rpc/mcp/mcp_agent_integration.h"

namespace {

using namespace agent_rpc::mcp;

// ============================================================================
// Task 20.1: MCPAgentIntegration Unit Tests
// **Feature: a2a-integration, Task 20.1: MCP Agent 集成单元测试**
// **Validates: Requirements 12.1, 12.3**
// ============================================================================

class MCPAgentIntegrationTest : public ::testing::Test {
protected:
    void SetUp() override {
        integration_ = std::make_unique<MCPAgentIntegration>();
    }
    
    void TearDown() override {
        if (integration_) {
            integration_->shutdown();
        }
    }
    
    std::unique_ptr<MCPAgentIntegration> integration_;
};

// Test: Default construction
TEST_F(MCPAgentIntegrationTest, DefaultConstruction) {
    EXPECT_FALSE(integration_->isInitialized());
    EXPECT_FALSE(integration_->isAvailable());
}

// Test: Initialize with MCP disabled
TEST_F(MCPAgentIntegrationTest, InitializeWithMCPDisabled) {
    MCPAgentConfig config;
    config.enable_mcp = false;
    
    bool result = integration_->initialize(config);
    
    EXPECT_TRUE(result);
    EXPECT_TRUE(integration_->isInitialized());
    EXPECT_FALSE(integration_->isAvailable());  // MCP not available when disabled
    EXPECT_EQ(integration_->getStatusDescription(), "MCP disabled");
}

// Test: Initialize with empty server path
TEST_F(MCPAgentIntegrationTest, InitializeWithEmptyServerPath) {
    MCPAgentConfig config;
    config.enable_mcp = true;
    config.mcp_server_path = "";  // Empty path
    
    bool result = integration_->initialize(config);
    
    EXPECT_TRUE(result);  // Should succeed in degraded mode
    EXPECT_TRUE(integration_->isInitialized());
    EXPECT_FALSE(integration_->isAvailable());
}

// Test: Double initialization
TEST_F(MCPAgentIntegrationTest, DoubleInitialization) {
    MCPAgentConfig config;
    config.enable_mcp = false;
    
    bool result1 = integration_->initialize(config);
    bool result2 = integration_->initialize(config);
    
    EXPECT_TRUE(result1);
    EXPECT_TRUE(result2);  // Should return true (already initialized)
    EXPECT_TRUE(integration_->isInitialized());
}

// Test: Shutdown without initialization
TEST_F(MCPAgentIntegrationTest, ShutdownWithoutInitialization) {
    // Should not crash
    integration_->shutdown();
    EXPECT_FALSE(integration_->isInitialized());
}

// Test: Shutdown after initialization
TEST_F(MCPAgentIntegrationTest, ShutdownAfterInitialization) {
    MCPAgentConfig config;
    config.enable_mcp = false;
    
    integration_->initialize(config);
    EXPECT_TRUE(integration_->isInitialized());
    
    integration_->shutdown();
    EXPECT_FALSE(integration_->isInitialized());
}

// Test: Double shutdown
TEST_F(MCPAgentIntegrationTest, DoubleShutdown) {
    MCPAgentConfig config;
    config.enable_mcp = false;
    
    integration_->initialize(config);
    integration_->shutdown();
    integration_->shutdown();  // Should not crash
    
    EXPECT_FALSE(integration_->isInitialized());
}

// Test: Get config after initialization (MCP disabled to avoid connection)
TEST_F(MCPAgentIntegrationTest, GetConfigAfterInitialization) {
    MCPAgentConfig config;
    config.enable_mcp = false;  // Disable to avoid connection attempt
    config.mcp_server_path = "/test/path";
    config.tool_call_timeout_ms = 5000;
    config.max_retry_count = 5;
    
    integration_->initialize(config);
    
    const auto& stored_config = integration_->getConfig();
    EXPECT_EQ(stored_config.mcp_server_path, "/test/path");
    EXPECT_EQ(stored_config.tool_call_timeout_ms, 5000);
    EXPECT_EQ(stored_config.max_retry_count, 5);
}

// Test: Get MCP server path (MCP disabled to avoid connection)
TEST_F(MCPAgentIntegrationTest, GetMCPServerPath) {
    MCPAgentConfig config;
    config.enable_mcp = false;  // Disable to avoid connection attempt
    config.mcp_server_path = "/usr/bin/mcp-server";
    
    integration_->initialize(config);
    
    EXPECT_EQ(integration_->getMCPServerPath(), "/usr/bin/mcp-server");
}

// Test: Tool list when MCP disabled
TEST_F(MCPAgentIntegrationTest, ToolListWhenMCPDisabled) {
    MCPAgentConfig config;
    config.enable_mcp = false;
    
    integration_->initialize(config);
    
    auto tools = integration_->getAvailableTools();
    EXPECT_TRUE(tools.empty());
    
    auto names = integration_->getToolNames();
    EXPECT_TRUE(names.empty());
}

// Test: hasToolAvailable when MCP disabled
TEST_F(MCPAgentIntegrationTest, HasToolAvailableWhenMCPDisabled) {
    MCPAgentConfig config;
    config.enable_mcp = false;
    
    integration_->initialize(config);
    
    EXPECT_FALSE(integration_->hasToolAvailable("any_tool"));
    EXPECT_FALSE(integration_->hasToolAvailable("calculator"));
}

// Test: getToolDescription when MCP disabled
TEST_F(MCPAgentIntegrationTest, GetToolDescriptionWhenMCPDisabled) {
    MCPAgentConfig config;
    config.enable_mcp = false;
    
    integration_->initialize(config);
    
    EXPECT_EQ(integration_->getToolDescription("any_tool"), "");
}

// Test: getToolInputSchema when MCP disabled
TEST_F(MCPAgentIntegrationTest, GetToolInputSchemaWhenMCPDisabled) {
    MCPAgentConfig config;
    config.enable_mcp = false;
    
    integration_->initialize(config);
    
    EXPECT_EQ(integration_->getToolInputSchema("any_tool"), "");
}

// ============================================================================
// Task 20.2: MCP Error Handling Tests
// **Feature: a2a-integration, Task 20.2: MCP 错误处理测试**
// **Validates: Requirements 12.5**
// ============================================================================

// Test: Tool call when MCP not available
TEST_F(MCPAgentIntegrationTest, ToolCallWhenMCPNotAvailable) {
    MCPAgentConfig config;
    config.enable_mcp = false;
    
    integration_->initialize(config);
    
    auto result = integration_->callTool("calculator", R"({"a": 1, "b": 2})");
    
    EXPECT_FALSE(result.success);
    EXPECT_FALSE(result.error.empty());
    EXPECT_TRUE(result.error.find("not available") != std::string::npos);
}

// Test: Tool call when not initialized
TEST_F(MCPAgentIntegrationTest, ToolCallWhenNotInitialized) {
    auto result = integration_->callTool("calculator", R"({"a": 1, "b": 2})");
    
    EXPECT_FALSE(result.success);
    EXPECT_FALSE(result.error.empty());
}

// Test: callToolSimple returns error prefix
TEST_F(MCPAgentIntegrationTest, CallToolSimpleReturnsErrorPrefix) {
    MCPAgentConfig config;
    config.enable_mcp = false;
    
    integration_->initialize(config);
    
    std::string result = integration_->callToolSimple("calculator", R"({})");
    
    EXPECT_TRUE(result.find("[ERROR]") == 0);
}

// Test: Async tool call with null callback
TEST_F(MCPAgentIntegrationTest, AsyncToolCallWithNullCallback) {
    MCPAgentConfig config;
    config.enable_mcp = false;
    
    integration_->initialize(config);
    
    // Should not crash with null callback
    integration_->callToolAsync("calculator", R"({})", nullptr);
}

// Test: Refresh tools when MCP not available
TEST_F(MCPAgentIntegrationTest, RefreshToolsWhenMCPNotAvailable) {
    MCPAgentConfig config;
    config.enable_mcp = false;
    
    integration_->initialize(config);
    
    bool result = integration_->refreshTools();
    EXPECT_FALSE(result);
}

// Test: Server unavailable scenario - simulated by empty path with MCP enabled
// Note: We don't test with actual non-existent paths as the MCP client may hang
// trying to start the process. Instead, we test the degraded mode behavior.
TEST_F(MCPAgentIntegrationTest, ServerUnavailableScenario) {
    MCPAgentConfig config;
    config.enable_mcp = true;
    config.mcp_server_path = "";  // Empty path triggers degraded mode
    config.connection_timeout_ms = 1000;
    
    // Should succeed in degraded mode
    bool result = integration_->initialize(config);
    
    EXPECT_TRUE(result);  // Degraded mode
    EXPECT_TRUE(integration_->isInitialized());
    EXPECT_FALSE(integration_->isAvailable());  // But MCP not available
}

// Test: Tool call with server unavailable (simulated via empty path)
TEST_F(MCPAgentIntegrationTest, ToolCallWithServerUnavailable) {
    MCPAgentConfig config;
    config.enable_mcp = true;
    config.mcp_server_path = "";  // Empty path triggers degraded mode
    
    integration_->initialize(config);
    
    auto result = integration_->callTool("calculator", R"({"a": 1, "b": 2})");
    
    EXPECT_FALSE(result.success);
    EXPECT_FALSE(result.error.empty());
}

// ============================================================================
// Property-based Tests
// ============================================================================

// Property: Config parsing preserves values
RC_GTEST_PROP(MCPConfigProperties, ConfigPreservesValues, ()) {
    auto timeout = *rc::gen::inRange(100, 60000);
    auto retry_count = *rc::gen::inRange(0, 10);
    auto retry_delay = *rc::gen::inRange(100, 5000);
    
    MCPAgentConfig config;
    config.enable_mcp = false;
    config.tool_call_timeout_ms = timeout;
    config.max_retry_count = retry_count;
    config.retry_delay_ms = retry_delay;
    
    MCPAgentIntegration integration;
    integration.initialize(config);
    
    const auto& stored = integration.getConfig();
    RC_ASSERT(stored.tool_call_timeout_ms == timeout);
    RC_ASSERT(stored.max_retry_count == retry_count);
    RC_ASSERT(stored.retry_delay_ms == retry_delay);
}

// Property: Status description is never empty
RC_GTEST_PROP(MCPConfigProperties, StatusDescriptionNeverEmpty, ()) {
    auto enable_mcp = *rc::gen::arbitrary<bool>();
    
    MCPAgentConfig config;
    config.enable_mcp = enable_mcp;
    
    MCPAgentIntegration integration;
    
    // Before initialization
    std::string status1 = integration.getStatusDescription();
    RC_ASSERT(!status1.empty());
    
    // After initialization
    integration.initialize(config);
    std::string status2 = integration.getStatusDescription();
    RC_ASSERT(!status2.empty());
    
    // After shutdown
    integration.shutdown();
    std::string status3 = integration.getStatusDescription();
    RC_ASSERT(!status3.empty());
}

// Property: Tool call result always has error message on failure
RC_GTEST_PROP(MCPConfigProperties, ToolCallFailureHasErrorMessage, ()) {
    auto tool_name = *rc::gen::nonEmpty(rc::gen::container<std::string>(
        rc::gen::inRange<char>('a', 'z')
    ));
    
    MCPAgentConfig config;
    config.enable_mcp = false;
    
    MCPAgentIntegration integration;
    integration.initialize(config);
    
    auto result = integration.callTool(tool_name, "{}");
    
    // When MCP is not available, call should fail
    RC_ASSERT(!result.success);
    RC_ASSERT(!result.error.empty());
}

// Property: callToolSimple returns error prefix on failure
RC_GTEST_PROP(MCPConfigProperties, CallToolSimpleErrorPrefix, ()) {
    auto tool_name = *rc::gen::nonEmpty(rc::gen::container<std::string>(
        rc::gen::inRange<char>('a', 'z')
    ));
    
    MCPAgentConfig config;
    config.enable_mcp = false;
    
    MCPAgentIntegration integration;
    integration.initialize(config);
    
    std::string result = integration.callToolSimple(tool_name, "{}");
    
    // Should start with [ERROR] prefix
    RC_ASSERT(result.substr(0, 7) == "[ERROR]");
}

// ============================================================================
// Task 10.4: RAG-MCP Integration Tests
// **Feature: rag-mcp, Task 10.4: RAG-MCP 集成测试**
// **Validates: Requirements 6.1, 6.2, 6.4**
// ============================================================================

// Test: RAG disabled by default
TEST_F(MCPAgentIntegrationTest, RAGDisabledByDefault) {
    MCPAgentConfig config;
    config.enable_mcp = false;
    
    integration_->initialize(config);
    
    EXPECT_FALSE(integration_->isRAGEnabled());
}

// Test: RAG enabled with configuration
TEST_F(MCPAgentIntegrationTest, RAGEnabledWithConfiguration) {
    MCPAgentConfig config;
    config.enable_mcp = false;
    config.rag_config.enabled = true;
    config.rag_config.api_key = "test_api_key";
    config.rag_config.top_k = 5;
    config.rag_config.similarity_threshold = 0.3f;
    
    integration_->initialize(config);
    
    // RAG should be enabled in config, but may not be fully initialized
    // without a valid API key
    EXPECT_EQ(integration_->getConfig().rag_config.enabled, true);
    EXPECT_EQ(integration_->getConfig().rag_config.top_k, 5);
}

// Test: Get relevant tools when RAG disabled returns all tools
TEST_F(MCPAgentIntegrationTest, GetRelevantToolsWhenRAGDisabled) {
    MCPAgentConfig config;
    config.enable_mcp = false;
    config.rag_config.enabled = false;
    
    integration_->initialize(config);
    
    // When RAG is disabled and MCP is disabled, should return empty list
    auto tools = integration_->getRelevantTools("calculate something");
    EXPECT_TRUE(tools.empty());
}

// Test: Get relevant tools with custom top_k
TEST_F(MCPAgentIntegrationTest, GetRelevantToolsWithCustomTopK) {
    MCPAgentConfig config;
    config.enable_mcp = false;
    config.rag_config.enabled = false;
    
    integration_->initialize(config);
    
    // When RAG is disabled, should return empty regardless of top_k
    auto tools = integration_->getRelevantTools("query", 10);
    EXPECT_TRUE(tools.empty());
}

// Test: toFunctionCallingFormat with empty tools
TEST_F(MCPAgentIntegrationTest, ToFunctionCallingFormatEmptyTools) {
    std::vector<ToolInfo> tools;
    
    std::string json = MCPAgentIntegration::toFunctionCallingFormat(tools);
    
    // Should return valid JSON array
    EXPECT_TRUE(json.find("[") != std::string::npos);
    EXPECT_TRUE(json.find("]") != std::string::npos);
}

// Test: toFunctionCallingFormat with tools
TEST_F(MCPAgentIntegrationTest, ToFunctionCallingFormatWithTools) {
    std::vector<ToolInfo> tools;
    
    ToolInfo tool1;
    tool1.name = "calculator";
    tool1.description = "Perform calculations";
    tool1.input_schema = R"({"type": "object", "properties": {"expression": {"type": "string"}}})";
    tools.push_back(tool1);
    
    ToolInfo tool2;
    tool2.name = "weather";
    tool2.description = "Get weather info";
    tool2.input_schema = R"({"type": "object", "properties": {"location": {"type": "string"}}})";
    tools.push_back(tool2);
    
    std::string json = MCPAgentIntegration::toFunctionCallingFormat(tools);
    
    // Should contain tool names
    EXPECT_TRUE(json.find("calculator") != std::string::npos);
    EXPECT_TRUE(json.find("weather") != std::string::npos);
    
    // Should contain descriptions
    EXPECT_TRUE(json.find("Perform calculations") != std::string::npos);
    EXPECT_TRUE(json.find("Get weather info") != std::string::npos);
}

// Test: getRelevantToolsAsJson when RAG disabled
TEST_F(MCPAgentIntegrationTest, GetRelevantToolsAsJsonWhenRAGDisabled) {
    MCPAgentConfig config;
    config.enable_mcp = false;
    config.rag_config.enabled = false;
    
    integration_->initialize(config);
    
    std::string json = integration_->getRelevantToolsAsJson("query");
    
    // Should return valid JSON (empty array)
    EXPECT_TRUE(json.find("[") != std::string::npos);
}

// Test: RAG config preservation
TEST_F(MCPAgentIntegrationTest, RAGConfigPreservation) {
    MCPAgentConfig config;
    config.enable_mcp = false;
    config.rag_config.enabled = true;
    config.rag_config.api_key = "test_key";
    config.rag_config.model = "text-embedding-v2";
    config.rag_config.top_k = 10;
    config.rag_config.similarity_threshold = 0.5f;
    config.rag_config.index_path = "/tmp/test_index.json";
    config.rag_config.enable_cache = true;
    config.rag_config.cache_max_size = 500;
    config.rag_config.cache_ttl_seconds = 1800;
    
    integration_->initialize(config);
    
    const auto& stored = integration_->getConfig().rag_config;
    EXPECT_EQ(stored.enabled, true);
    EXPECT_EQ(stored.api_key, "test_key");
    EXPECT_EQ(stored.model, "text-embedding-v2");
    EXPECT_EQ(stored.top_k, 10);
    EXPECT_FLOAT_EQ(stored.similarity_threshold, 0.5f);
    EXPECT_EQ(stored.index_path, "/tmp/test_index.json");
    EXPECT_EQ(stored.enable_cache, true);
    EXPECT_EQ(stored.cache_max_size, 500u);
    EXPECT_EQ(stored.cache_ttl_seconds, 1800);
}

// Test: Fallback to all tools when RAG unavailable (empty index)
TEST_F(MCPAgentIntegrationTest, FallbackWhenRAGUnavailable) {
    MCPAgentConfig config;
    config.enable_mcp = false;
    config.rag_config.enabled = true;
    config.rag_config.api_key = "";  // Invalid API key
    
    integration_->initialize(config);
    
    // Should not crash, should return empty or fallback
    auto tools = integration_->getRelevantTools("query");
    // When MCP is disabled, no tools available anyway
    EXPECT_TRUE(tools.empty());
}

// Property: RAG config values are preserved
RC_GTEST_PROP(RAGMCPProperties, RAGConfigValuesPreserved, ()) {
    auto top_k = *rc::gen::inRange(1, 20);
    auto threshold = *rc::gen::inRange(0, 100) / 100.0f;
    auto cache_size = *rc::gen::inRange(100, 10000);
    auto cache_ttl = *rc::gen::inRange(60, 7200);
    
    MCPAgentConfig config;
    config.enable_mcp = false;
    config.rag_config.enabled = true;
    config.rag_config.top_k = top_k;
    config.rag_config.similarity_threshold = threshold;
    config.rag_config.cache_max_size = cache_size;
    config.rag_config.cache_ttl_seconds = cache_ttl;
    
    MCPAgentIntegration integration;
    integration.initialize(config);
    
    const auto& stored = integration.getConfig().rag_config;
    RC_ASSERT(stored.top_k == top_k);
    RC_ASSERT(std::abs(stored.similarity_threshold - threshold) < 0.01f);
    RC_ASSERT(stored.cache_max_size == static_cast<size_t>(cache_size));
    RC_ASSERT(stored.cache_ttl_seconds == cache_ttl);
}

// Property: toFunctionCallingFormat produces valid JSON structure
RC_GTEST_PROP(RAGMCPProperties, ToFunctionCallingFormatProducesValidJSON, ()) {
    auto num_tools = *rc::gen::inRange(0, 10);
    
    std::vector<ToolInfo> tools;
    for (int i = 0; i < num_tools; ++i) {
        ToolInfo tool;
        tool.name = "tool_" + std::to_string(i);
        tool.description = "Description " + std::to_string(i);
        tool.input_schema = "{}";
        tools.push_back(tool);
    }
    
    std::string json = MCPAgentIntegration::toFunctionCallingFormat(tools);
    
    // Should start with [ and end with ]
    RC_ASSERT(!json.empty());
    RC_ASSERT(json.front() == '[');
    RC_ASSERT(json.back() == ']');
    
    // Should contain all tool names
    for (int i = 0; i < num_tools; ++i) {
        RC_ASSERT(json.find("tool_" + std::to_string(i)) != std::string::npos);
    }
}

// ============================================================================
// Command Line Argument Parsing Tests
// ============================================================================

TEST(MCPConfigParsingTest, ParseEmptyArgs) {
    char* argv[] = {const_cast<char*>("test")};
    auto config = parseMCPConfigFromArgs(1, argv);
    
    EXPECT_FALSE(config.enable_mcp);
    EXPECT_TRUE(config.mcp_server_path.empty());
}

TEST(MCPConfigParsingTest, ParseMCPServerArg) {
    char* argv[] = {
        const_cast<char*>("test"),
        const_cast<char*>("--mcp-server"),
        const_cast<char*>("/path/to/server")
    };
    auto config = parseMCPConfigFromArgs(3, argv);
    
    EXPECT_TRUE(config.enable_mcp);
    EXPECT_EQ(config.mcp_server_path, "/path/to/server");
}

TEST(MCPConfigParsingTest, ParseEnableMCPArg) {
    char* argv[] = {
        const_cast<char*>("test"),
        const_cast<char*>("--enable-mcp")
    };
    auto config = parseMCPConfigFromArgs(2, argv);
    
    EXPECT_TRUE(config.enable_mcp);
}

TEST(MCPConfigParsingTest, ParseMCPTimeoutArg) {
    char* argv[] = {
        const_cast<char*>("test"),
        const_cast<char*>("--mcp-timeout"),
        const_cast<char*>("5000")
    };
    auto config = parseMCPConfigFromArgs(3, argv);
    
    EXPECT_EQ(config.tool_call_timeout_ms, 5000);
}

TEST(MCPConfigParsingTest, ParseMCPArgsArg) {
    char* argv[] = {
        const_cast<char*>("test"),
        const_cast<char*>("--mcp-args"),
        const_cast<char*>("arg1,arg2,arg3")
    };
    auto config = parseMCPConfigFromArgs(3, argv);
    
    ASSERT_EQ(config.mcp_args.size(), 3);
    EXPECT_EQ(config.mcp_args[0], "arg1");
    EXPECT_EQ(config.mcp_args[1], "arg2");
    EXPECT_EQ(config.mcp_args[2], "arg3");
}

TEST(MCPConfigParsingTest, ParseAllArgs) {
    char* argv[] = {
        const_cast<char*>("test"),
        const_cast<char*>("--mcp-server"),
        const_cast<char*>("/path/to/server"),
        const_cast<char*>("--mcp-args"),
        const_cast<char*>("--port,8080"),
        const_cast<char*>("--mcp-timeout"),
        const_cast<char*>("10000"),
        const_cast<char*>("--enable-mcp")
    };
    auto config = parseMCPConfigFromArgs(9, argv);
    
    EXPECT_TRUE(config.enable_mcp);
    EXPECT_EQ(config.mcp_server_path, "/path/to/server");
    EXPECT_EQ(config.tool_call_timeout_ms, 10000);
    ASSERT_EQ(config.mcp_args.size(), 2);
    EXPECT_EQ(config.mcp_args[0], "--port");
    EXPECT_EQ(config.mcp_args[1], "8080");
}

} // namespace

int main(int argc, char** argv) {
    ::testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}
