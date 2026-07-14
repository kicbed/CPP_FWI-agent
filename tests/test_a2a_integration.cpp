/**
 * @file test_a2a_integration.cpp
 * @brief Unit tests for A2A module integration
 * 
 * Tests A2A type initialization and namespace correctness
 * Requirements: 1.2, 1.5
 */

#include <gtest/gtest.h>

// A2A Core includes
#include <a2a/core/types.hpp>
#include <a2a/core/error_code.hpp>
#include <a2a/core/exception.hpp>

// A2A Models includes
#include <a2a/models/agent_card.hpp>
#include <a2a/models/agent_message.hpp>
#include <a2a/models/agent_task.hpp>
#include <a2a/models/message_part.hpp>
#include <a2a/models/message_send_params.hpp>
#include <a2a/models/task_status.hpp>
#include <a2a/models/a2a_response.hpp>
#include <a2a/models/artifact.hpp>

// A2A Client includes
#include <a2a/client/a2a_client.hpp>
#include <a2a/client/card_resolver.hpp>

// A2A Server includes
#include <a2a/server/task_manager.hpp>
#include <a2a/server/task_store.hpp>
#include <a2a/server/memory_task_store.hpp>

namespace {

/**
 * @brief Test fixture for A2A integration tests
 */
class A2AIntegrationTest : public ::testing::Test {
protected:
    void SetUp() override {}
    void TearDown() override {}
};

// ============================================================================
// Namespace Tests - Verify a2a:: namespace is preserved
// ============================================================================

TEST_F(A2AIntegrationTest, NamespacePreserved_Types) {
    // Test that types are in a2a:: namespace
    a2a::MessageRole role = a2a::MessageRole::User;
    EXPECT_EQ(a2a::to_string(role), "user");
    
    a2a::TaskState state = a2a::TaskState::Submitted;
    EXPECT_EQ(a2a::to_string(state), "submitted");
    
    a2a::PartKind kind = a2a::PartKind::Text;
    EXPECT_TRUE(kind == a2a::PartKind::Text);
}

TEST_F(A2AIntegrationTest, NamespacePreserved_ErrorCodes) {
    // Test error codes in a2a:: namespace
    a2a::ErrorCode code = a2a::ErrorCode::TaskNotFound;
    EXPECT_EQ(static_cast<int32_t>(code), -32001);
    
    const char* desc = a2a::error_code_to_string(code);
    EXPECT_STREQ(desc, "Task not found");
}

TEST_F(A2AIntegrationTest, NamespacePreserved_Exception) {
    // Test exception in a2a:: namespace
    a2a::A2AException ex("Test error", a2a::ErrorCode::InternalError);
    EXPECT_EQ(ex.error_code(), a2a::ErrorCode::InternalError);
    EXPECT_STREQ(ex.what(), "Test error");
}

// ============================================================================
// Type Initialization Tests
// ============================================================================

TEST_F(A2AIntegrationTest, TypeInit_MessageRole) {
    // Test MessageRole enum values
    EXPECT_EQ(a2a::to_string(a2a::MessageRole::User), "user");
    EXPECT_EQ(a2a::to_string(a2a::MessageRole::Agent), "agent");
    EXPECT_EQ(a2a::to_string(a2a::MessageRole::System), "system");
    
    // Test from_string conversion
    EXPECT_EQ(a2a::message_role_from_string("user"), a2a::MessageRole::User);
    EXPECT_EQ(a2a::message_role_from_string("agent"), a2a::MessageRole::Agent);
    EXPECT_EQ(a2a::message_role_from_string("system"), a2a::MessageRole::System);
}

TEST_F(A2AIntegrationTest, TypeInit_TaskState) {
    // Test TaskState enum values
    EXPECT_EQ(a2a::to_string(a2a::TaskState::Submitted), "submitted");
    EXPECT_EQ(a2a::to_string(a2a::TaskState::Running), "running");
    EXPECT_EQ(a2a::to_string(a2a::TaskState::Completed), "completed");
    EXPECT_EQ(a2a::to_string(a2a::TaskState::Failed), "failed");
    EXPECT_EQ(a2a::to_string(a2a::TaskState::Canceled), "canceled");
    
    // Test from_string conversion
    EXPECT_EQ(a2a::task_state_from_string("submitted"), a2a::TaskState::Submitted);
    EXPECT_EQ(a2a::task_state_from_string("running"), a2a::TaskState::Running);
    EXPECT_EQ(a2a::task_state_from_string("completed"), a2a::TaskState::Completed);
}

TEST_F(A2AIntegrationTest, TypeInit_ErrorCodes) {
    // Test all error codes
    EXPECT_EQ(static_cast<int32_t>(a2a::ErrorCode::ParseError), -32700);
    EXPECT_EQ(static_cast<int32_t>(a2a::ErrorCode::InvalidRequest), -32600);
    EXPECT_EQ(static_cast<int32_t>(a2a::ErrorCode::MethodNotFound), -32601);
    EXPECT_EQ(static_cast<int32_t>(a2a::ErrorCode::InvalidParams), -32602);
    EXPECT_EQ(static_cast<int32_t>(a2a::ErrorCode::InternalError), -32603);
    EXPECT_EQ(static_cast<int32_t>(a2a::ErrorCode::TaskNotFound), -32001);
}

// ============================================================================
// Model Class Tests
// ============================================================================

TEST_F(A2AIntegrationTest, ModelInit_AgentCard) {
    a2a::AgentCard card;
    card.set_name("TestAgent");
    card.set_url("http://localhost:5000");
    card.set_version("1.0.0");
    
    EXPECT_EQ(card.name(), "TestAgent");
    EXPECT_EQ(card.url(), "http://localhost:5000");
    EXPECT_EQ(card.version(), "1.0.0");
}

TEST_F(A2AIntegrationTest, ModelInit_AgentMessage) {
    a2a::AgentMessage msg;
    msg.set_message_id("msg-001");
    msg.set_context_id("ctx-001");
    msg.set_role(a2a::MessageRole::User);
    
    EXPECT_EQ(msg.message_id(), "msg-001");
    EXPECT_EQ(msg.context_id(), "ctx-001");
    EXPECT_EQ(msg.role(), a2a::MessageRole::User);
}

TEST_F(A2AIntegrationTest, ModelInit_AgentTaskStatus) {
    a2a::AgentTaskStatus status;
    status.set_state(a2a::TaskState::Running);
    
    EXPECT_EQ(status.state(), a2a::TaskState::Running);
    EXPECT_FALSE(status.is_terminal());
    
    status.set_state(a2a::TaskState::Completed);
    EXPECT_TRUE(status.is_terminal());
}

TEST_F(A2AIntegrationTest, ModelInit_MessagePart) {
    // Test TextPart
    a2a::TextPart text_part("Hello, World!");
    EXPECT_EQ(text_part.text(), "Hello, World!");
    EXPECT_EQ(text_part.kind(), a2a::PartKind::Text);
}

TEST_F(A2AIntegrationTest, MessageJsonPreservesStructuredToolText) {
    const std::string tool_text =
        R"({"content":[{"text":"{\"status\":\"queued\",\"iterations\":50}"}],"isError":false})";
    a2a::AgentMessage source;
    source.set_message_id("msg-fwi");
    source.set_context_id("ctx-fwi");
    source.set_role(a2a::MessageRole::Agent);
    source.add_text_part(tool_text);

    const auto parsed = a2a::AgentMessage::from_json(source.to_json());
    ASSERT_EQ(parsed.parts().size(), 1U);
    const auto* text = dynamic_cast<const a2a::TextPart*>(parsed.parts().front().get());
    ASSERT_NE(text, nullptr);
    EXPECT_EQ(text->text(), tool_text);
    EXPECT_EQ(parsed.context_id().value_or(""), "ctx-fwi");
}

TEST_F(A2AIntegrationTest, ModelInit_Artifact) {
    a2a::Artifact artifact;
    artifact.set_name("result.txt");
    artifact.set_mime_type("text/plain");
    
    EXPECT_EQ(artifact.name(), "result.txt");
    EXPECT_EQ(artifact.mime_type(), "text/plain");
}

// ============================================================================
// Server Component Tests
// ============================================================================

TEST_F(A2AIntegrationTest, ServerInit_MemoryTaskStore) {
    auto store = std::make_unique<a2a::MemoryTaskStore>();
    EXPECT_NE(store, nullptr);
}

TEST_F(A2AIntegrationTest, ServerInit_TaskManager) {
    auto store = std::make_shared<a2a::MemoryTaskStore>();
    a2a::TaskManager manager(store);
    
    // TaskManager should be initialized
    EXPECT_NE(&manager, nullptr);
}

// ============================================================================
// Exception Tests
// ============================================================================

TEST_F(A2AIntegrationTest, ExceptionInit_WithMessage) {
    a2a::A2AException ex("Test error message", a2a::ErrorCode::InvalidRequest);
    
    EXPECT_STREQ(ex.what(), "Test error message");
    EXPECT_EQ(ex.error_code(), a2a::ErrorCode::InvalidRequest);
    EXPECT_EQ(ex.error_code_value(), -32600);
}

TEST_F(A2AIntegrationTest, ExceptionInit_WithRequestId) {
    a2a::A2AException ex("Error", a2a::ErrorCode::TaskNotFound, "req-123");
    
    EXPECT_EQ(ex.request_id(), "req-123");
    EXPECT_EQ(ex.error_code(), a2a::ErrorCode::TaskNotFound);
}

TEST_F(A2AIntegrationTest, ExceptionThrowCatch) {
    bool caught = false;
    try {
        throw a2a::A2AException("Test throw", a2a::ErrorCode::InternalError);
    } catch (const a2a::A2AException& e) {
        caught = true;
        EXPECT_EQ(e.error_code(), a2a::ErrorCode::InternalError);
    }
    EXPECT_TRUE(caught);
}

} // namespace

int main(int argc, char** argv) {
    ::testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}
