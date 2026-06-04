/**
 * @file response_adapter.h
 * @brief A2A response to RPC response adapter
 * 
 * Requirements: 8.2, 8.3
 */

#pragma once

#include <string>
#include <a2a/models/a2a_response.hpp>
#include <a2a/models/agent_message.hpp>
#include <a2a/models/agent_task.hpp>
#include <a2a/core/types.hpp>

// Forward declaration for protobuf types
namespace agent_communication {
class AIQueryResponse;
class AIStreamEvent;
}

namespace agent_rpc {
namespace a2a_adapter {

/**
 * @brief Adapts A2A responses to RPC response format
 */
class ResponseAdapter {
public:
    ResponseAdapter() = default;
    ~ResponseAdapter() = default;
    
    /**
     * @brief Convert A2A response to RPC AIQueryResponse
     * @param a2a_response The A2A response
     * @param request_id The original request ID
     * @return Populated AIQueryResponse
     */
    void convertFromA2A(
        const a2a::A2AResponse& a2a_response,
        const std::string& request_id,
        agent_communication::AIQueryResponse* rpc_response);
    
    /**
     * @brief Extract text content from an AgentMessage
     * @param message The A2A message
     * @return Extracted text content
     */
    std::string extractTextContent(const a2a::AgentMessage& message);
    
    /**
     * @brief Convert A2A TaskState to string representation
     * @param state The task state
     * @return String representation
     */
    std::string convertTaskState(a2a::TaskState state);
    
    /**
     * @brief Build a stream event from A2A data
     * @param event_data The event data
     * @param context_id The context ID
     * @param event_type The event type
     * @return Populated AIStreamEvent
     */
    void buildStreamEvent(
        const std::string& event_data,
        const std::string& context_id,
        const std::string& event_type,
        agent_communication::AIStreamEvent* event);

private:
    uint64_t event_counter_ = 0;
    
    std::string generateEventId();
};

} // namespace a2a_adapter
} // namespace agent_rpc
