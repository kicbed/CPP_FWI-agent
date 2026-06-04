#pragma once

#include "types.h"
#include "serializer.h"
#include "agent_service.pb.h"
#include "common.pb.h"
#include <memory>
#include <string>
#include <vector>

namespace agent_rpc {
namespace common {

// 消息转换器 - 在内部类型和protobuf消息之间转换
class MessageConverter {
public:
    // 转换ServiceEndpoint到protobuf ServiceInfo
    static agent_communication::common::ServiceInfo toProtobuf(const ServiceEndpoint& endpoint);
    
    // 转换protobuf ServiceInfo到ServiceEndpoint
    static ServiceEndpoint fromProtobuf(const agent_communication::common::ServiceInfo& service_info);
    
    // 转换内部Message到protobuf Message
    static agent_communication::Message toProtobufMessage(const std::string& content, 
                                                         const std::string& id = "",
                                                         const std::string& type = "text");
    
    // 转换protobuf Message到内部Message
    static std::string fromProtobufMessage(const agent_communication::Message& message);
    
    // 创建发送消息请求
    static agent_communication::SendMessageRequest createSendMessageRequest(
        const std::string& content,
        const std::string& target_agent,
        int32_t timeout_seconds = 30);
    
    // 创建接收消息请求
    static agent_communication::ReceiveMessageRequest createReceiveMessageRequest(
        const std::string& agent_id,
        int32_t max_messages = 10,
        int32_t timeout_seconds = 30);
    
    // 创建广播消息请求
    static agent_communication::BroadcastMessageRequest createBroadcastMessageRequest(
        const std::string& content,
        const std::vector<std::string>& target_agents = {},
        bool exclude_sender = true);
    
    // 创建注册代理请求
    static agent_communication::RegisterAgentRequest createRegisterAgentRequest(
        const ServiceEndpoint& agent_info,
        int32_t heartbeat_interval = 30);
    
    // 创建注销代理请求
    static agent_communication::UnregisterAgentRequest createUnregisterAgentRequest(
        const std::string& agent_id,
        const std::string& reason = "");
    
    // 创建心跳请求
    static agent_communication::HeartbeatRequest createHeartbeatRequest(
        const std::string& agent_id,
        const ServiceEndpoint& agent_info);
    
    // 创建获取代理列表请求
    static agent_communication::GetAgentsRequest createGetAgentsRequest(
        const std::string& filter = "",
        int32_t limit = 100,
        int32_t offset = 0);
    
    // 解析发送消息响应
    static bool parseSendMessageResponse(const agent_communication::SendMessageResponse& response,
                                       std::string& message_id,
                                       int64_t& timestamp);
    
    // 解析接收消息响应
    static bool parseReceiveMessageResponse(const agent_communication::ReceiveMessageResponse& response,
                                          std::vector<std::string>& messages);
    
    // 解析广播消息响应
    static bool parseBroadcastMessageResponse(const agent_communication::BroadcastMessageResponse& response,
                                            int32_t& success_count,
                                            int32_t& failure_count);
    
    // 解析注册代理响应
    static bool parseRegisterAgentResponse(const agent_communication::RegisterAgentResponse& response,
                                         std::string& agent_id,
                                         int64_t& registration_time);
    
    // 解析注销代理响应
    static bool parseUnregisterAgentResponse(const agent_communication::UnregisterAgentResponse& response,
                                           int64_t& unregistration_time);
    
    // 解析心跳响应
    static bool parseHeartbeatResponse(const agent_communication::HeartbeatResponse& response,
                                     int64_t& server_time);
    
    // 解析获取代理列表响应
    static bool parseGetAgentsResponse(const agent_communication::GetAgentsResponse& response,
                                     std::vector<ServiceEndpoint>& agents,
                                     int32_t& total_count);
    
    // 创建成功状态
    static agent_communication::common::Status createSuccessStatus(const std::string& message = "Success");
    
    // 创建错误状态
    static agent_communication::common::Status createErrorStatus(int32_t code, 
                                                               const std::string& message,
                                                               const std::string& details = "");
    
    // 检查状态是否成功
    static bool isStatusSuccess(const agent_communication::common::Status& status);
    
    // 获取状态消息
    static std::string getStatusMessage(const agent_communication::common::Status& status);
};

// 消息构建器 - 用于构建复杂的protobuf消息
class MessageBuilder {
public:
    // 构建Message
    static agent_communication::Message buildMessage(
        const std::string& content,
        const std::string& id = "",
        const std::string& type = "text",
        const std::map<std::string, std::string>& headers = {},
        const std::string& payload = "");
    
    // 构建ServiceInfo
    static agent_communication::common::ServiceInfo buildServiceInfo(
        const std::string& service_name,
        const std::string& version,
        const std::string& host,
        int32_t port,
        const std::vector<std::string>& tags = {},
        const std::map<std::string, std::string>& metadata = {});
    
    // 构建Status
    static agent_communication::common::Status buildStatus(
        int32_t code,
        const std::string& message,
        const std::string& details = "");
    
    // 构建HealthCheckResponse
    static agent_communication::common::HealthCheckResponse buildHealthCheckResponse(
        bool is_healthy);
    
    // 构建LogEntry
    static agent_communication::common::LogEntry buildLogEntry(
        agent_communication::common::LogLevel level,
        const std::string& message,
        const std::string& source = "",
        const std::map<std::string, std::string>& fields = {});
};

// 消息验证器 - 用于验证protobuf消息
class MessageValidator {
public:
    // 验证Message
    static bool validateMessage(const agent_communication::Message& message);
    
    // 验证ServiceInfo
    static bool validateServiceInfo(const agent_communication::common::ServiceInfo& service_info);
    
    // 验证Status
    static bool validateStatus(const agent_communication::common::Status& status);
    
    // 验证SendMessageRequest
    static bool validateSendMessageRequest(const agent_communication::SendMessageRequest& request);
    
    // 验证ReceiveMessageRequest
    static bool validateReceiveMessageRequest(const agent_communication::ReceiveMessageRequest& request);
    
    // 验证BroadcastMessageRequest
    static bool validateBroadcastMessageRequest(const agent_communication::BroadcastMessageRequest& request);
    
    // 验证RegisterAgentRequest
    static bool validateRegisterAgentRequest(const agent_communication::RegisterAgentRequest& request);
    
    // 验证UnregisterAgentRequest
    static bool validateUnregisterAgentRequest(const agent_communication::UnregisterAgentRequest& request);
    
    // 验证HeartbeatRequest
    static bool validateHeartbeatRequest(const agent_communication::HeartbeatRequest& request);
    
    // 验证GetAgentsRequest
    static bool validateGetAgentsRequest(const agent_communication::GetAgentsRequest& request);
};

} // namespace common
} // namespace agent_rpc
