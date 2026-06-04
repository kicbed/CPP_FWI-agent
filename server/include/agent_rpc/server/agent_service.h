#pragma once

#include "agent_rpc/common/types.h"
#include "agent_rpc/common/logger.h"
#include "agent_rpc/common/metrics.h"
#include "agent_service.grpc.pb.h"
#include "agent_service.pb.h"
#include "common.pb.h"
#include <grpcpp/grpcpp.h>
#include <memory>
#include <string>
#include <vector>
#include <map>
#include <mutex>
#include <atomic>
#include <thread>

namespace agent_rpc {
namespace server {

// Agent通信服务 gRPC 实现
class AgentCommunicationServiceImpl final
    : public agent_communication::AgentCommunicationService::Service {
public:
    AgentCommunicationServiceImpl();
    ~AgentCommunicationServiceImpl();

    // ========================================================================
    // gRPC Service Methods (10 个 RPC)
    // ========================================================================

    grpc::Status SendMessage(
        grpc::ServerContext* context,
        const agent_communication::SendMessageRequest* request,
        agent_communication::SendMessageResponse* response) override;

    grpc::Status ReceiveMessage(
        grpc::ServerContext* context,
        const agent_communication::ReceiveMessageRequest* request,
        agent_communication::ReceiveMessageResponse* response) override;

    grpc::Status BroadcastMessage(
        grpc::ServerContext* context,
        const agent_communication::BroadcastMessageRequest* request,
        agent_communication::BroadcastMessageResponse* response) override;

    grpc::Status GetAgents(
        grpc::ServerContext* context,
        const agent_communication::GetAgentsRequest* request,
        agent_communication::GetAgentsResponse* response) override;

    grpc::Status RegisterAgent(
        grpc::ServerContext* context,
        const agent_communication::RegisterAgentRequest* request,
        agent_communication::RegisterAgentResponse* response) override;

    grpc::Status UnregisterAgent(
        grpc::ServerContext* context,
        const agent_communication::UnregisterAgentRequest* request,
        agent_communication::UnregisterAgentResponse* response) override;

    grpc::Status Heartbeat(
        grpc::ServerContext* context,
        const agent_communication::HeartbeatRequest* request,
        agent_communication::HeartbeatResponse* response) override;

    grpc::Status ListenMessages(
        grpc::ServerContext* context,
        const agent_communication::ReceiveMessageRequest* request,
        grpc::ServerWriter<agent_communication::Message>* writer) override;

    grpc::Status BatchSendMessages(
        grpc::ServerContext* context,
        grpc::ServerReader<agent_communication::SendMessageRequest>* reader,
        agent_communication::SendMessageResponse* response) override;

    grpc::Status RealTimeCommunication(
        grpc::ServerContext* context,
        grpc::ServerReaderWriter<agent_communication::Message,
                                 agent_communication::Message>* stream) override;

    // ========================================================================
    // Internal methods
    // ========================================================================

    void setMessageHandler(common::MessageHandler handler);
    void setErrorHandler(common::ErrorHandler handler);
    void setHealthCheckHandler(common::HealthCheckHandler handler);

    std::vector<common::ServiceEndpoint> getAgentsList() const;

private:
    std::string generateMessageId();
    bool isAgentOnline(const std::string& agent_id);
    void updateAgentHeartbeat(const std::string& agent_id);
    void cleanupOfflineAgents();

    mutable std::mutex agents_mutex_;
    std::map<std::string, common::ServiceEndpoint> agents_;
    std::map<std::string, common::MessageQueue<agent_communication::Message>> agent_message_queues_;

    common::MessageHandler message_handler_;
    common::ErrorHandler error_handler_;
    common::HealthCheckHandler health_check_handler_;

    std::atomic<int> message_id_counter_{0};
    std::thread cleanup_thread_;
    std::atomic<bool> cleanup_running_{false};
};

// 健康检查服务 gRPC 实现
class HealthServiceImpl final
    : public agent_communication::HealthService::Service {
public:
    HealthServiceImpl();
    ~HealthServiceImpl() = default;

    grpc::Status Check(
        grpc::ServerContext* context,
        const agent_communication::common::HealthCheckRequest* request,
        agent_communication::common::HealthCheckResponse* response) override;

    grpc::Status Watch(
        grpc::ServerContext* context,
        const agent_communication::common::HealthCheckRequest* request,
        grpc::ServerWriter<agent_communication::common::HealthCheckResponse>* writer) override;

    void setHealthCheckHandler(common::HealthCheckHandler handler);

private:
    common::HealthCheckHandler health_check_handler_;
};

} // namespace server
} // namespace agent_rpc
