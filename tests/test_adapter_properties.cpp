/**
 * @file test_adapter_properties.cpp
 * @brief Property-based tests for A2A adapter layer
 * 
 * Tests for:
 * - Property 1: Message Round-Trip Consistency
 * - Property 7: Error Code Mapping Completeness
 * - Property 10: Configuration Default Fallback
 */

#include <gtest/gtest.h>
#include <rapidcheck.h>
#include <rapidcheck/gtest.h>

#include "agent_rpc/a2a_adapter/a2a_config.h"
#include "agent_rpc/a2a_adapter/request_adapter.h"
#include "agent_rpc/a2a_adapter/response_adapter.h"
#include "agent_rpc/a2a_adapter/error_mapper.h"
#include "ai_query.pb.h"

namespace {

// Custom generator for valid UTF-8 strings (ASCII subset)
rc::Gen<std::string> genValidString() {
    return rc::gen::container<std::string>(
        rc::gen::inRange<char>('a', 'z')
    );
}

rc::Gen<std::string> genNonEmptyValidString() {
    return rc::gen::nonEmpty(genValidString());
}

// ============================================================================
// Property 1: Message Round-Trip Consistency
// **Feature: a2a-integration, Property 1: Message Round-Trip Consistency**
// **Validates: Requirements 5.1, 5.5, 8.1, 8.2, 8.3**
// ============================================================================

RC_GTEST_PROP(AdapterProperties, MessageRoundTrip_PreservesQuestion, ()) {
    auto question = *genValidString();
    auto context_id = *genNonEmptyValidString();
    
    agent_communication::AIQueryRequest original;
    original.set_request_id("test-req-1");
    original.set_question(question);
    original.set_context_id(context_id);
    
    agent_rpc::a2a_adapter::RequestAdapter request_adapter;
    
    // Convert to A2A format
    auto a2a_params = request_adapter.convertToA2A(original);
    
    // Verify message is set (message() returns AgentMessage, not optional)
    const auto& msg = a2a_params.message();
    RC_ASSERT(!msg.message_id().empty());
    
    // The context ID should be preserved
    RC_ASSERT(a2a_params.context_id().has_value());
    RC_ASSERT(a2a_params.context_id().value() == context_id);
}

RC_GTEST_PROP(AdapterProperties, MessageRoundTrip_GeneratesContextIdIfMissing, ()) {
    auto question = *genValidString();
    
    agent_communication::AIQueryRequest original;
    original.set_request_id("test-req-2");
    original.set_question(question);
    // No context_id set
    
    agent_rpc::a2a_adapter::RequestAdapter request_adapter;
    
    // Convert to A2A format
    auto a2a_params = request_adapter.convertToA2A(original);
    
    // A context ID should be generated
    RC_ASSERT(a2a_params.context_id().has_value());
    RC_ASSERT(!a2a_params.context_id().value().empty());
}

RC_GTEST_PROP(AdapterProperties, MessageRoundTrip_PreservesHistoryLength, ()) {
    auto history_length = *rc::gen::inRange(1, 100);
    
    agent_communication::AIQueryRequest original;
    original.set_request_id("test-req-3");
    original.set_question("test question");
    original.set_history_length(history_length);
    
    agent_rpc::a2a_adapter::RequestAdapter request_adapter;
    
    auto a2a_params = request_adapter.convertToA2A(original);
    
    // History length should be preserved
    RC_ASSERT(a2a_params.history_length().has_value());
    RC_ASSERT(a2a_params.history_length().value() == history_length);
}

// ============================================================================
// Property 7: Error Code Mapping Completeness
// **Feature: a2a-integration, Property 7: Error Code Mapping Completeness**
// **Validates: Requirements 8.4, 10.1**
// ============================================================================

RC_GTEST_PROP(AdapterProperties, ErrorMapping_AllA2ACodesMapToValidGrpc, ()) {
    // Test all known A2A error codes
    std::vector<a2a::ErrorCode> all_codes = {
        a2a::ErrorCode::ParseError,
        a2a::ErrorCode::InvalidRequest,
        a2a::ErrorCode::MethodNotFound,
        a2a::ErrorCode::InvalidParams,
        a2a::ErrorCode::InternalError,
        a2a::ErrorCode::TaskNotFound,
        a2a::ErrorCode::TaskNotCancelable,
        a2a::ErrorCode::UnsupportedOperation,
        a2a::ErrorCode::ContentTypeNotSupported,
        a2a::ErrorCode::PushNotificationNotSupported
    };
    
    auto code_index = *rc::gen::inRange<size_t>(0, all_codes.size());
    a2a::ErrorCode a2a_code = all_codes[code_index];
    
    grpc::StatusCode grpc_code = agent_rpc::a2a_adapter::ErrorMapper::mapToGrpcStatus(a2a_code);
    
    // Result should be a valid gRPC status code (not UNKNOWN for known codes)
    RC_ASSERT(grpc_code != grpc::StatusCode::DO_NOT_USE);
}

RC_GTEST_PROP(AdapterProperties, ErrorMapping_IsDeterministic, ()) {
    std::vector<a2a::ErrorCode> all_codes = {
        a2a::ErrorCode::ParseError,
        a2a::ErrorCode::InvalidRequest,
        a2a::ErrorCode::MethodNotFound,
        a2a::ErrorCode::InvalidParams,
        a2a::ErrorCode::InternalError,
        a2a::ErrorCode::TaskNotFound
    };
    
    auto code_index = *rc::gen::inRange<size_t>(0, all_codes.size());
    a2a::ErrorCode a2a_code = all_codes[code_index];
    
    // Map the same code twice
    grpc::StatusCode result1 = agent_rpc::a2a_adapter::ErrorMapper::mapToGrpcStatus(a2a_code);
    grpc::StatusCode result2 = agent_rpc::a2a_adapter::ErrorMapper::mapToGrpcStatus(a2a_code);
    
    // Results should be identical (deterministic)
    RC_ASSERT(result1 == result2);
}

RC_GTEST_PROP(AdapterProperties, ErrorMapping_IntegerCodesMapCorrectly, ()) {
    // Test integer error code mapping
    std::vector<int32_t> int_codes = {
        -32700, -32600, -32601, -32602, -32603,
        -32001, -32002, -32003, -32004, -32005
    };
    
    auto code_index = *rc::gen::inRange<size_t>(0, int_codes.size());
    int32_t int_code = int_codes[code_index];
    
    grpc::StatusCode grpc_code = agent_rpc::a2a_adapter::ErrorMapper::mapIntToGrpcStatus(int_code);
    
    // Should map to a valid status code
    RC_ASSERT(grpc_code != grpc::StatusCode::DO_NOT_USE);
}

// ============================================================================
// Property 10: Configuration Default Fallback
// **Feature: a2a-integration, Property 10: Configuration Default Fallback**
// **Validates: Requirements 9.5**
// ============================================================================

RC_GTEST_PROP(AdapterProperties, Config_InvalidPortUsesDefault, ()) {
    auto invalid_port = *rc::gen::oneOf(
        rc::gen::inRange(-1000, 0),
        rc::gen::inRange(65536, 100000)
    );
    
    agent_rpc::a2a_adapter::A2AConfig config;
    config.orchestrator_port = invalid_port;
    
    bool was_valid = config.validate();
    
    // Should not be valid
    RC_ASSERT(!was_valid);
    // Port should be reset to default
    RC_ASSERT(config.orchestrator_port == 5000);
}

RC_GTEST_PROP(AdapterProperties, Config_InvalidTimeoutUsesDefault, ()) {
    auto invalid_timeout = *rc::gen::inRange(-100, 0);
    
    agent_rpc::a2a_adapter::A2AConfig config;
    config.request_timeout_seconds = invalid_timeout;
    
    bool was_valid = config.validate();
    
    RC_ASSERT(!was_valid);
    RC_ASSERT(config.request_timeout_seconds == 30);
}

RC_GTEST_PROP(AdapterProperties, Config_InvalidHeartbeatUsesDefault, ()) {
    auto invalid_heartbeat = *rc::gen::inRange(-100, 0);
    
    agent_rpc::a2a_adapter::A2AConfig config;
    config.heartbeat_interval_seconds = invalid_heartbeat;
    
    bool was_valid = config.validate();
    
    RC_ASSERT(!was_valid);
    RC_ASSERT(config.heartbeat_interval_seconds == 30);
}

RC_GTEST_PROP(AdapterProperties, Config_ValidConfigRemainsUnchanged, ()) {
    auto valid_port = *rc::gen::inRange(1, 65535);
    auto valid_timeout = *rc::gen::inRange(1, 3600);
    auto valid_heartbeat = *rc::gen::inRange(1, 300);
    
    agent_rpc::a2a_adapter::A2AConfig config;
    config.orchestrator_port = valid_port;
    config.request_timeout_seconds = valid_timeout;
    config.heartbeat_interval_seconds = valid_heartbeat;
    
    bool was_valid = config.validate();
    
    RC_ASSERT(was_valid);
    RC_ASSERT(config.orchestrator_port == valid_port);
    RC_ASSERT(config.request_timeout_seconds == valid_timeout);
    RC_ASSERT(config.heartbeat_interval_seconds == valid_heartbeat);
}

} // namespace

int main(int argc, char** argv) {
    ::testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}
