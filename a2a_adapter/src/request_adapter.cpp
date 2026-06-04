/**
 * @file request_adapter.cpp
 * @brief Implementation of RPC to A2A request adapter
 * 
 * Requirements: 8.1, 8.3
 */

#include "agent_rpc/a2a_adapter/request_adapter.h"
#include "ai_query.pb.h"
#include <a2a/models/message_part.hpp>
#include <chrono>
#include <sstream>
#include <iomanip>

namespace agent_rpc {
namespace a2a_adapter {

a2a::MessageSendParams RequestAdapter::convertToA2A(
    const agent_communication::AIQueryRequest& rpc_request) {
    
    a2a::MessageSendParams params;
    
    // Extract or generate context ID
    std::string context_id = extractOrGenerateContextId(rpc_request);
    params.set_context_id(context_id);
    
    // Build the agent message
    a2a::AgentMessage message = buildAgentMessage(
        rpc_request.question(),
        context_id,
        a2a::MessageRole::User
    );
    
    params.set_message(message);
    
    // Set history length if specified
    if (rpc_request.history_length() > 0) {
        params.set_history_length(rpc_request.history_length());
    }
    
    return params;
}

a2a::AgentMessage RequestAdapter::buildAgentMessage(
    const std::string& content,
    const std::string& context_id,
    a2a::MessageRole role) {
    
    a2a::AgentMessage message;
    
    // Set message ID
    message.set_message_id(generateMessageId());
    
    // Set context ID
    message.set_context_id(context_id);
    
    // Set role
    message.set_role(role);
    
    // Add text content as a part
    message.add_part(std::make_unique<a2a::TextPart>(content));
    
    return message;
}

std::string RequestAdapter::extractOrGenerateContextId(
    const agent_communication::AIQueryRequest& request) {
    
    if (!request.context_id().empty()) {
        return request.context_id();
    }
    
    return generateContextId();
}

std::string RequestAdapter::generateMessageId() {
    auto now = std::chrono::system_clock::now();
    auto timestamp = std::chrono::duration_cast<std::chrono::milliseconds>(
        now.time_since_epoch()).count();
    
    std::ostringstream oss;
    oss << "msg-" << std::hex << timestamp << "-" << (++message_counter_);
    return oss.str();
}

std::string RequestAdapter::generateContextId() {
    auto now = std::chrono::system_clock::now();
    auto timestamp = std::chrono::duration_cast<std::chrono::milliseconds>(
        now.time_since_epoch()).count();
    
    std::ostringstream oss;
    oss << "ctx-" << std::hex << timestamp << "-" << (++context_counter_);
    return oss.str();
}

} // namespace a2a_adapter
} // namespace agent_rpc
