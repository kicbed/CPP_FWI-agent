#include "agent_rpc/common/message_converter.h"
#include "agent_rpc/common/logger.h"
#include <chrono>

namespace agent_rpc {
namespace common {

// MessageConverter 实现

agent_communication::common::ServiceInfo MessageConverter::toProtobuf(const ServiceEndpoint& endpoint) {
    agent_communication::common::ServiceInfo service_info;
    service_info.set_service_name(endpoint.service_name);
    service_info.set_version(endpoint.version);
    service_info.set_host(endpoint.host);
    service_info.set_port(endpoint.port);
    
    // 添加标签
    for (const auto& tag : endpoint.metadata) {
        if (tag.first == "tags") {
            // 如果metadata中有tags字段，解析为标签列表
            std::stringstream ss(tag.second);
            std::string tag_item;
            while (std::getline(ss, tag_item, ',')) {
                service_info.add_tags(tag_item);
            }
        }
    }
    
    // 添加元数据
    for (const auto& pair : endpoint.metadata) {
        if (pair.first != "tags") {
            (*service_info.mutable_metadata())[pair.first] = pair.second;
        }
    }
    
    return service_info;
}

ServiceEndpoint MessageConverter::fromProtobuf(const agent_communication::common::ServiceInfo& service_info) {
    ServiceEndpoint endpoint;
    endpoint.service_name = service_info.service_name();
    endpoint.version = service_info.version();
    endpoint.host = service_info.host();
    endpoint.port = service_info.port();
    endpoint.is_healthy = true; // 默认健康
    endpoint.last_heartbeat = std::chrono::steady_clock::now();
    
    // 添加标签到元数据
    if (service_info.tags_size() > 0) {
        std::string tags_str;
        for (int i = 0; i < service_info.tags_size(); ++i) {
            if (i > 0) tags_str += ",";
            tags_str += service_info.tags(i);
        }
        endpoint.metadata["tags"] = tags_str;
    }
    
    // 添加元数据
    for (const auto& pair : service_info.metadata()) {
        endpoint.metadata[pair.first] = pair.second;
    }
    
    return endpoint;
}

agent_communication::Message MessageConverter::toProtobufMessage(const std::string& content, 
                                                               const std::string& id,
                                                               const std::string& type) {
    agent_communication::Message message;
    message.set_id(id.empty() ? std::to_string(std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count()) : id);
    message.set_type(type);
    message.set_content(content);
    message.set_timestamp(std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count());
    
    return message;
}

std::string MessageConverter::fromProtobufMessage(const agent_communication::Message& message) {
    return message.content();
}

agent_communication::SendMessageRequest MessageConverter::createSendMessageRequest(
    const std::string& content,
    const std::string& target_agent,
    int32_t timeout_seconds) {
    agent_communication::SendMessageRequest request;
    *request.mutable_message() = toProtobufMessage(content);
    request.set_target_agent(target_agent);
    request.set_timeout_seconds(timeout_seconds);
    
    return request;
}

agent_communication::ReceiveMessageRequest MessageConverter::createReceiveMessageRequest(
    const std::string& agent_id,
    int32_t max_messages,
    int32_t timeout_seconds) {
    agent_communication::ReceiveMessageRequest request;
    request.set_agent_id(agent_id);
    request.set_max_messages(max_messages);
    request.set_timeout_seconds(timeout_seconds);
    
    return request;
}

agent_communication::BroadcastMessageRequest MessageConverter::createBroadcastMessageRequest(
    const std::string& content,
    const std::vector<std::string>& target_agents,
    bool exclude_sender) {
    agent_communication::BroadcastMessageRequest request;
    *request.mutable_message() = toProtobufMessage(content);
    request.set_exclude_sender(exclude_sender);
    
    for (const auto& agent : target_agents) {
        request.add_target_agents(agent);
    }
    
    return request;
}

agent_communication::RegisterAgentRequest MessageConverter::createRegisterAgentRequest(
    const ServiceEndpoint& agent_info,
    int32_t heartbeat_interval) {
    agent_communication::RegisterAgentRequest request;
    *request.mutable_agent_info() = toProtobuf(agent_info);
    request.set_heartbeat_interval(heartbeat_interval);
    
    return request;
}

agent_communication::UnregisterAgentRequest MessageConverter::createUnregisterAgentRequest(
    const std::string& agent_id,
    const std::string& reason) {
    agent_communication::UnregisterAgentRequest request;
    request.set_agent_id(agent_id);
    request.set_reason(reason);
    
    return request;
}

agent_communication::HeartbeatRequest MessageConverter::createHeartbeatRequest(
    const std::string& agent_id,
    const ServiceEndpoint& agent_info) {
    agent_communication::HeartbeatRequest request;
    request.set_agent_id(agent_id);
    *request.mutable_agent_info() = toProtobuf(agent_info);
    
    return request;
}

agent_communication::GetAgentsRequest MessageConverter::createGetAgentsRequest(
    const std::string& filter,
    int32_t limit,
    int32_t offset) {
    agent_communication::GetAgentsRequest request;
    request.set_filter(filter);
    request.set_limit(limit);
    request.set_offset(offset);
    
    return request;
}

bool MessageConverter::parseSendMessageResponse(const agent_communication::SendMessageResponse& response,
                                              std::string& message_id,
                                              int64_t& timestamp) {
    if (!isStatusSuccess(response.status())) {
        return false;
    }
    
    message_id = response.message_id();
    timestamp = response.timestamp();
    return true;
}

bool MessageConverter::parseReceiveMessageResponse(const agent_communication::ReceiveMessageResponse& response,
                                                 std::vector<std::string>& messages) {
    if (!isStatusSuccess(response.status())) {
        return false;
    }
    
    messages.clear();
    for (const auto& msg : response.messages()) {
        messages.push_back(fromProtobufMessage(msg));
    }
    
    return true;
}

bool MessageConverter::parseBroadcastMessageResponse(const agent_communication::BroadcastMessageResponse& response,
                                                   int32_t& success_count,
                                                   int32_t& failure_count) {
    if (!isStatusSuccess(response.status())) {
        return false;
    }
    
    success_count = response.success_count();
    failure_count = response.failure_count();
    return true;
}

bool MessageConverter::parseRegisterAgentResponse(const agent_communication::RegisterAgentResponse& response,
                                                std::string& agent_id,
                                                int64_t& registration_time) {
    if (!isStatusSuccess(response.status())) {
        return false;
    }
    
    agent_id = response.agent_id();
    registration_time = response.registration_time();
    return true;
}

bool MessageConverter::parseUnregisterAgentResponse(const agent_communication::UnregisterAgentResponse& response,
                                                  int64_t& unregistration_time) {
    if (!isStatusSuccess(response.status())) {
        return false;
    }
    
    unregistration_time = response.unregistration_time();
    return true;
}

bool MessageConverter::parseHeartbeatResponse(const agent_communication::HeartbeatResponse& response,
                                            int64_t& server_time) {
    if (!isStatusSuccess(response.status())) {
        return false;
    }
    
    server_time = response.server_time();
    return true;
}

bool MessageConverter::parseGetAgentsResponse(const agent_communication::GetAgentsResponse& response,
                                            std::vector<ServiceEndpoint>& agents,
                                            int32_t& total_count) {
    if (!isStatusSuccess(response.status())) {
        return false;
    }
    
    agents.clear();
    for (const auto& agent_info : response.agents()) {
        agents.push_back(fromProtobuf(agent_info));
    }
    
    total_count = response.total_count();
    return true;
}

agent_communication::common::Status MessageConverter::createSuccessStatus(const std::string& message) {
    agent_communication::common::Status status;
    status.set_code(0);
    status.set_message(message);
    return status;
}

agent_communication::common::Status MessageConverter::createErrorStatus(int32_t code, 
                                                                       const std::string& message,
                                                                       const std::string& details) {
    agent_communication::common::Status status;
    status.set_code(code);
    status.set_message(message);
    status.set_details(details);
    return status;
}

bool MessageConverter::isStatusSuccess(const agent_communication::common::Status& status) {
    return status.code() == 0;
}

std::string MessageConverter::getStatusMessage(const agent_communication::common::Status& status) {
    return status.message();
}

// MessageBuilder 实现

agent_communication::Message MessageBuilder::buildMessage(
    const std::string& content,
    const std::string& id,
    const std::string& type,
    const std::map<std::string, std::string>& headers,
    const std::string& payload) {
    agent_communication::Message message;
    message.set_id(id.empty() ? std::to_string(std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count()) : id);
    message.set_type(type);
    message.set_content(content);
    message.set_timestamp(std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count());
    
    // 添加头部
    for (const auto& pair : headers) {
        (*message.mutable_headers())[pair.first] = pair.second;
    }
    
    // 添加负载
    if (!payload.empty()) {
        message.set_payload(payload);
    }
    
    return message;
}

agent_communication::common::ServiceInfo MessageBuilder::buildServiceInfo(
    const std::string& service_name,
    const std::string& version,
    const std::string& host,
    int32_t port,
    const std::vector<std::string>& tags,
    const std::map<std::string, std::string>& metadata) {
    agent_communication::common::ServiceInfo service_info;
    service_info.set_service_name(service_name);
    service_info.set_version(version);
    service_info.set_host(host);
    service_info.set_port(port);
    
    // 添加标签
    for (const auto& tag : tags) {
        service_info.add_tags(tag);
    }
    
    // 添加元数据
    for (const auto& pair : metadata) {
        (*service_info.mutable_metadata())[pair.first] = pair.second;
    }
    
    return service_info;
}

agent_communication::common::Status MessageBuilder::buildStatus(
    int32_t code,
    const std::string& message,
    const std::string& details) {
    agent_communication::common::Status status;
    status.set_code(code);
    status.set_message(message);
    status.set_details(details);
    return status;
}

agent_communication::common::HealthCheckResponse MessageBuilder::buildHealthCheckResponse(bool is_healthy) {
    agent_communication::common::HealthCheckResponse response;
    response.set_status(is_healthy ? 
        agent_communication::common::HealthCheckResponse::SERVING :
        agent_communication::common::HealthCheckResponse::NOT_SERVING);
    return response;
}

agent_communication::common::LogEntry MessageBuilder::buildLogEntry(
    agent_communication::common::LogLevel level,
    const std::string& message,
    const std::string& source,
    const std::map<std::string, std::string>& fields) {
    agent_communication::common::LogEntry entry;
    entry.set_level(level);
    entry.set_message(message);
    entry.set_source(source);
    entry.set_timestamp(std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count());
    
    // 添加字段
    for (const auto& pair : fields) {
        (*entry.mutable_fields())[pair.first] = pair.second;
    }
    
    return entry;
}

// MessageValidator 实现

bool MessageValidator::validateMessage(const agent_communication::Message& message) {
    return !message.id().empty() && !message.content().empty();
}

bool MessageValidator::validateServiceInfo(const agent_communication::common::ServiceInfo& service_info) {
    return !service_info.service_name().empty() && 
           !service_info.host().empty() && 
           service_info.port() > 0;
}

bool MessageValidator::validateStatus(const agent_communication::common::Status& status) {
    return true; // 状态总是有效的
}

bool MessageValidator::validateSendMessageRequest(const agent_communication::SendMessageRequest& request) {
    return !request.target_agent().empty() && 
           request.has_message() &&
           validateMessage(request.message());
}

bool MessageValidator::validateReceiveMessageRequest(const agent_communication::ReceiveMessageRequest& request) {
    return !request.agent_id().empty() && 
           request.max_messages() > 0;
}

bool MessageValidator::validateBroadcastMessageRequest(const agent_communication::BroadcastMessageRequest& request) {
    return request.has_message() && validateMessage(request.message());
}

bool MessageValidator::validateRegisterAgentRequest(const agent_communication::RegisterAgentRequest& request) {
    return request.has_agent_info() && 
           validateServiceInfo(request.agent_info());
}

bool MessageValidator::validateUnregisterAgentRequest(const agent_communication::UnregisterAgentRequest& request) {
    return !request.agent_id().empty();
}

bool MessageValidator::validateHeartbeatRequest(const agent_communication::HeartbeatRequest& request) {
    return !request.agent_id().empty() && 
           request.has_agent_info() &&
           validateServiceInfo(request.agent_info());
}

bool MessageValidator::validateGetAgentsRequest(const agent_communication::GetAgentsRequest& request) {
    return request.limit() > 0 && request.offset() >= 0;
}

} // namespace common
} // namespace agent_rpc
