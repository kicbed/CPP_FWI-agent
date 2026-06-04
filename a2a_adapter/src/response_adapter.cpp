/**
 * @file response_adapter.cpp
 * @brief Implementation of A2A to RPC response adapter
 * 
 * Requirements: 8.2, 8.3
 */

#include "agent_rpc/a2a_adapter/response_adapter.h"
#include "ai_query.pb.h"
#include <a2a/models/message_part.hpp>
#include <chrono>
#include <sstream>

namespace agent_rpc {
namespace a2a_adapter {

void ResponseAdapter::convertFromA2A(
    const a2a::A2AResponse& a2a_response,
    const std::string& request_id,
    agent_communication::AIQueryResponse* rpc_response) {
    
    if (!rpc_response) return;
    
    // Set request ID
    rpc_response->set_request_id(request_id);
    
    // Set status - assume success if we got a response
    auto* status = rpc_response->mutable_status();
    status->set_code(0);
    status->set_message("Success");
    
    if (a2a_response.is_task()) {
        // Extract answer from task result
        const auto& task = a2a_response.as_task();
        
        // Set task ID
        rpc_response->set_task_id(task.id());
        
        // Set context ID
        rpc_response->set_context_id(task.context_id());
        
        // Extract answer from the last agent message in history
        const auto& history = task.history();
        for (auto it = history.rbegin(); it != history.rend(); ++it) {
            if (it->role() == a2a::MessageRole::Agent) {
                rpc_response->set_answer(extractTextContent(*it));
                break;
            }
        }
        
        // Copy artifacts
        for (const auto& artifact : task.artifacts()) {
            auto* proto_artifact = rpc_response->add_artifacts();
            proto_artifact->set_name(artifact.name());
            if (artifact.mime_type().has_value()) {
                proto_artifact->set_mime_type(artifact.mime_type().value());
            }
            // Note: artifact data would need proper handling
        }
    } else if (a2a_response.is_message()) {
        // Extract answer from message response
        const auto& message = a2a_response.as_message();
        
        // Set context ID if available
        if (message.context_id().has_value()) {
            rpc_response->set_context_id(message.context_id().value());
        }
        
        // Set task ID if available
        if (message.task_id().has_value()) {
            rpc_response->set_task_id(message.task_id().value());
        }
        
        // Extract text content from message parts
        rpc_response->set_answer(extractTextContent(message));
    }
}

std::string ResponseAdapter::extractTextContent(const a2a::AgentMessage& message) {
    std::string content;
    
    for (const auto& part : message.parts()) {
        if (part->kind() == a2a::PartKind::Text) {
            auto* text_part = dynamic_cast<const a2a::TextPart*>(part.get());
            if (text_part) {
                if (!content.empty()) {
                    content += "\n";
                }
                content += text_part->text();
            }
        }
    }
    
    return content;
}

std::string ResponseAdapter::convertTaskState(a2a::TaskState state) {
    return a2a::to_string(state);
}

void ResponseAdapter::buildStreamEvent(
    const std::string& event_data,
    const std::string& context_id,
    const std::string& event_type,
    agent_communication::AIStreamEvent* event) {
    
    if (!event) return;
    
    event->set_event_id(generateEventId());
    event->set_event_type(event_type);
    event->set_content(event_data);
    event->set_context_id(context_id);
    
    auto now = std::chrono::system_clock::now();
    auto timestamp = std::chrono::duration_cast<std::chrono::milliseconds>(
        now.time_since_epoch()).count();
    event->set_timestamp(timestamp);
}

std::string ResponseAdapter::generateEventId() {
    auto now = std::chrono::system_clock::now();
    auto timestamp = std::chrono::duration_cast<std::chrono::milliseconds>(
        now.time_since_epoch()).count();
    
    std::ostringstream oss;
    oss << "evt-" << std::hex << timestamp << "-" << (++event_counter_);
    return oss.str();
}

} // namespace a2a_adapter
} // namespace agent_rpc
