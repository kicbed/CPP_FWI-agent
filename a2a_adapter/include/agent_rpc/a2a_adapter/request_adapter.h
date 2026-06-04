/**
 * @file request_adapter.h
 * @brief RPC request to A2A message adapter
 * 
 * Requirements: 8.1, 8.3
 */

#pragma once

#include <string>
#include <memory>
#include <a2a/models/message_send_params.hpp>
#include <a2a/models/agent_message.hpp>
#include <a2a/core/types.hpp>

// Forward declaration for protobuf types
namespace agent_communication {
class AIQueryRequest;
}

namespace agent_rpc {
namespace a2a_adapter {

/**
 * @brief Adapts RPC requests to A2A message format
 */
class RequestAdapter {
public:
    RequestAdapter() = default;
    ~RequestAdapter() = default;
    
    /**
     * @brief Convert RPC AIQueryRequest to A2A MessageSendParams
     * @param rpc_request The RPC request to convert
     * @return A2A MessageSendParams
     */
    a2a::MessageSendParams convertToA2A(const agent_communication::AIQueryRequest& rpc_request);
    
    /**
     * @brief Build an A2A AgentMessage from content
     * @param content The message content
     * @param context_id The context ID for conversation tracking
     * @param role The message role (default: User)
     * @return A2A AgentMessage
     */
    a2a::AgentMessage buildAgentMessage(
        const std::string& content,
        const std::string& context_id,
        a2a::MessageRole role = a2a::MessageRole::User);
    
    /**
     * @brief Extract or generate a context ID from the request
     * @param request The RPC request
     * @return Context ID (existing or newly generated)
     */
    std::string extractOrGenerateContextId(const agent_communication::AIQueryRequest& request);
    
    /**
     * @brief Generate a unique message ID
     * @return Unique message ID
     */
    std::string generateMessageId();
    
    /**
     * @brief Generate a unique context ID
     * @return Unique context ID
     */
    std::string generateContextId();

private:
    uint64_t message_counter_ = 0;
    uint64_t context_counter_ = 0;
};

} // namespace a2a_adapter
} // namespace agent_rpc
