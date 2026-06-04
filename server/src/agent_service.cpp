#include "agent_rpc/server/agent_service.h"
#include "agent_rpc/common/logger.h"
#include "agent_rpc/common/metrics.h"
#include <grpcpp/grpcpp.h>
#include <sstream>
#include <iomanip>
#include <chrono>

namespace agent_rpc {
namespace server {

// ============================================================================
// AgentCommunicationServiceImpl
// ============================================================================

AgentCommunicationServiceImpl::AgentCommunicationServiceImpl()
    : cleanup_running_(false) {
    cleanup_running_ = true;
    cleanup_thread_ = std::thread([this]() {
        while (cleanup_running_) {
            for (int i = 0; i < 30 && cleanup_running_; ++i) {
                std::this_thread::sleep_for(std::chrono::seconds(1));
            }
            if (cleanup_running_) {
                cleanupOfflineAgents();
            }
        }
    });
}

AgentCommunicationServiceImpl::~AgentCommunicationServiceImpl() {
    cleanup_running_ = false;
    if (cleanup_thread_.joinable()) {
        cleanup_thread_.join();
    }
}

void AgentCommunicationServiceImpl::setMessageHandler(common::MessageHandler handler) {
    message_handler_ = handler;
}

void AgentCommunicationServiceImpl::setErrorHandler(common::ErrorHandler handler) {
    error_handler_ = handler;
}

void AgentCommunicationServiceImpl::setHealthCheckHandler(common::HealthCheckHandler handler) {
    health_check_handler_ = handler;
}

std::string AgentCommunicationServiceImpl::generateMessageId() {
    return std::to_string(++message_id_counter_);
}

bool AgentCommunicationServiceImpl::isAgentOnline(const std::string& agent_id) {
    std::lock_guard<std::mutex> lock(agents_mutex_);
    return agents_.find(agent_id) != agents_.end();
}

void AgentCommunicationServiceImpl::updateAgentHeartbeat(const std::string& agent_id) {
    std::lock_guard<std::mutex> lock(agents_mutex_);
    auto it = agents_.find(agent_id);
    if (it != agents_.end()) {
        it->second.last_heartbeat = std::chrono::steady_clock::now();
    }
}

void AgentCommunicationServiceImpl::cleanupOfflineAgents() {
    std::lock_guard<std::mutex> lock(agents_mutex_);
    auto now = std::chrono::steady_clock::now();
    auto timeout = std::chrono::seconds(60);

    auto it = agents_.begin();
    while (it != agents_.end()) {
        if (now - it->second.last_heartbeat > timeout) {
            LOG_WARN("Agent offline, removing: " + it->first);
            agent_message_queues_.erase(it->first);
            common::Metrics::getInstance().recordDisconnection(it->first);
            it = agents_.erase(it);
        } else {
            ++it;
        }
    }
}

std::vector<common::ServiceEndpoint> AgentCommunicationServiceImpl::getAgentsList() const {
    std::lock_guard<std::mutex> lock(agents_mutex_);
    std::vector<common::ServiceEndpoint> result;
    for (const auto& pair : agents_) {
        result.push_back(pair.second);
    }
    return result;
}

// ========================================================================
// gRPC Handlers
// ========================================================================

grpc::Status AgentCommunicationServiceImpl::SendMessage(
    grpc::ServerContext* /*context*/,
    const agent_communication::SendMessageRequest* request,
    agent_communication::SendMessageResponse* response) {

    auto start = std::chrono::steady_clock::now();
    const auto& target = request->target_agent();

    std::lock_guard<std::mutex> lock(agents_mutex_);
    auto it = agent_message_queues_.find(target);
    if (it != agent_message_queues_.end()) {
        it->second.push(request->message());
        auto* status = response->mutable_status();
        status->set_code(0);
        status->set_message("OK");
        response->set_message_id(generateMessageId());
        response->set_timestamp(std::chrono::system_clock::now().time_since_epoch().count());
    } else {
        auto* status = response->mutable_status();
        status->set_code(1);
        status->set_message("Target agent not found: " + target);
    }

    auto dur = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - start);
    common::Metrics::getInstance().recordRpcRequest("AgentCommunicationService", "SendMessage", dur.count());
    return grpc::Status::OK;
}

grpc::Status AgentCommunicationServiceImpl::ReceiveMessage(
    grpc::ServerContext* /*context*/,
    const agent_communication::ReceiveMessageRequest* request,
    agent_communication::ReceiveMessageResponse* response) {

    const auto& agent_id = request->agent_id();
    int max_messages = request->max_messages();
    if (max_messages <= 0) max_messages = 10;

    std::lock_guard<std::mutex> lock(agents_mutex_);
    auto it = agent_message_queues_.find(agent_id);
    if (it == agent_message_queues_.end()) {
        auto* status = response->mutable_status();
        status->set_code(1);
        status->set_message("Agent not found: " + agent_id);
        return grpc::Status::OK;
    }

    agent_communication::Message msg;
    int count = 0;
    while (count < max_messages && it->second.try_pop(msg, std::chrono::milliseconds(0))) {
        *response->add_messages() = msg;
        count++;
    }

    auto* status = response->mutable_status();
    status->set_code(0);
    status->set_message("OK");
    return grpc::Status::OK;
}

grpc::Status AgentCommunicationServiceImpl::BroadcastMessage(
    grpc::ServerContext* /*context*/,
    const agent_communication::BroadcastMessageRequest* request,
    agent_communication::BroadcastMessageResponse* response) {

    std::lock_guard<std::mutex> lock(agents_mutex_);
    int success_count = 0;
    int failure_count = 0;

    if (request->target_agents_size() == 0) {
        for (auto& pair : agent_message_queues_) {
            pair.second.push(request->message());
            success_count++;
        }
    } else {
        for (const auto& agent_id : request->target_agents()) {
            auto it = agent_message_queues_.find(agent_id);
            if (it != agent_message_queues_.end()) {
                it->second.push(request->message());
                success_count++;
            } else {
                failure_count++;
                response->add_failed_agents(agent_id);
            }
        }
    }

    auto* status = response->mutable_status();
    status->set_code(0);
    status->set_message("OK");
    response->set_success_count(success_count);
    response->set_failure_count(failure_count);
    return grpc::Status::OK;
}

grpc::Status AgentCommunicationServiceImpl::GetAgents(
    grpc::ServerContext* /*context*/,
    const agent_communication::GetAgentsRequest* request,
    agent_communication::GetAgentsResponse* response) {

    std::lock_guard<std::mutex> lock(agents_mutex_);
    int offset = request->offset();
    int limit = request->limit();
    if (limit <= 0) limit = 100;

    int index = 0;
    int added = 0;
    for (const auto& pair : agents_) {
        if (index++ < offset) continue;
        if (added >= limit) break;

        if (!request->filter().empty() &&
            pair.second.service_name.find(request->filter()) == std::string::npos) {
            continue;
        }

        auto* info = response->add_agents();
        info->set_service_name(pair.second.service_name);
        info->set_version(pair.second.version);
        info->set_host(pair.second.host);
        info->set_port(pair.second.port);
        for (const auto& m : pair.second.metadata) {
            (*info->mutable_metadata())[m.first] = m.second;
        }
        added++;
    }

    response->set_total_count(static_cast<int>(agents_.size()));
    auto* status = response->mutable_status();
    status->set_code(0);
    status->set_message("OK");
    return grpc::Status::OK;
}

grpc::Status AgentCommunicationServiceImpl::RegisterAgent(
    grpc::ServerContext* /*context*/,
    const agent_communication::RegisterAgentRequest* request,
    agent_communication::RegisterAgentResponse* response) {

    const auto& info = request->agent_info();
    std::string agent_id = info.service_name() + "-" + info.host() + "-" + std::to_string(info.port());

    common::ServiceEndpoint endpoint;
    endpoint.host = info.host();
    endpoint.port = info.port();
    endpoint.service_name = info.service_name();
    endpoint.version = info.version();
    endpoint.is_healthy = true;
    endpoint.last_heartbeat = std::chrono::steady_clock::now();
    for (const auto& m : info.metadata()) {
        endpoint.metadata[m.first] = m.second;
    }

    {
        std::lock_guard<std::mutex> lock(agents_mutex_);
        agents_[agent_id] = endpoint;
        agent_message_queues_.try_emplace(agent_id);
    }

    common::Metrics::getInstance().recordConnection(agent_id, true);
    LOG_INFO("Agent registered: " + agent_id);

    auto* status = response->mutable_status();
    status->set_code(0);
    status->set_message("OK");
    response->set_agent_id(agent_id);
    response->set_registration_time(
        std::chrono::system_clock::now().time_since_epoch().count());
    return grpc::Status::OK;
}

grpc::Status AgentCommunicationServiceImpl::UnregisterAgent(
    grpc::ServerContext* /*context*/,
    const agent_communication::UnregisterAgentRequest* request,
    agent_communication::UnregisterAgentResponse* response) {

    const auto& agent_id = request->agent_id();
    {
        std::lock_guard<std::mutex> lock(agents_mutex_);
        agents_.erase(agent_id);
        agent_message_queues_.erase(agent_id);
    }

    common::Metrics::getInstance().recordDisconnection(agent_id);
    LOG_INFO("Agent unregistered: " + agent_id + " reason: " + request->reason());

    auto* status = response->mutable_status();
    status->set_code(0);
    status->set_message("OK");
    response->set_unregistration_time(
        std::chrono::system_clock::now().time_since_epoch().count());
    return grpc::Status::OK;
}

grpc::Status AgentCommunicationServiceImpl::Heartbeat(
    grpc::ServerContext* /*context*/,
    const agent_communication::HeartbeatRequest* request,
    agent_communication::HeartbeatResponse* response) {

    updateAgentHeartbeat(request->agent_id());

    auto* status = response->mutable_status();
    status->set_code(0);
    status->set_message("OK");
    response->set_server_time(
        std::chrono::system_clock::now().time_since_epoch().count());
    return grpc::Status::OK;
}

grpc::Status AgentCommunicationServiceImpl::ListenMessages(
    grpc::ServerContext* context,
    const agent_communication::ReceiveMessageRequest* request,
    grpc::ServerWriter<agent_communication::Message>* writer) {

    const auto& agent_id = request->agent_id();
    int timeout_seconds = request->timeout_seconds();
    if (timeout_seconds <= 0) timeout_seconds = 30;

    auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(timeout_seconds);

    while (!context->IsCancelled() && std::chrono::steady_clock::now() < deadline) {
        agent_communication::Message msg;
        {
            std::lock_guard<std::mutex> lock(agents_mutex_);
            auto it = agent_message_queues_.find(agent_id);
            if (it == agent_message_queues_.end()) {
                return grpc::Status(grpc::StatusCode::NOT_FOUND, "Agent not found");
            }
            if (!it->second.try_pop(msg, std::chrono::milliseconds(100))) {
                continue;
            }
        }
        if (!writer->Write(msg)) break;
    }
    return grpc::Status::OK;
}

grpc::Status AgentCommunicationServiceImpl::BatchSendMessages(
    grpc::ServerContext* /*context*/,
    grpc::ServerReader<agent_communication::SendMessageRequest>* reader,
    agent_communication::SendMessageResponse* response) {

    agent_communication::SendMessageRequest req;
    int count = 0;
    while (reader->Read(&req)) {
        std::lock_guard<std::mutex> lock(agents_mutex_);
        auto it = agent_message_queues_.find(req.target_agent());
        if (it != agent_message_queues_.end()) {
            it->second.push(req.message());
            count++;
        }
    }

    auto* status = response->mutable_status();
    status->set_code(0);
    status->set_message("Batch processed " + std::to_string(count) + " messages");
    response->set_message_id(generateMessageId());
    return grpc::Status::OK;
}

grpc::Status AgentCommunicationServiceImpl::RealTimeCommunication(
    grpc::ServerContext* context,
    grpc::ServerReaderWriter<agent_communication::Message,
                             agent_communication::Message>* stream) {

    agent_communication::Message msg;
    while (!context->IsCancelled() && stream->Read(&msg)) {
        // Echo back for now; real implementation would route to target
        if (!stream->Write(msg)) break;
    }
    return grpc::Status::OK;
}

// ============================================================================
// HealthServiceImpl
// ============================================================================

HealthServiceImpl::HealthServiceImpl() = default;

void HealthServiceImpl::setHealthCheckHandler(common::HealthCheckHandler handler) {
    health_check_handler_ = handler;
}

grpc::Status HealthServiceImpl::Check(
    grpc::ServerContext* /*context*/,
    const agent_communication::common::HealthCheckRequest* /*request*/,
    agent_communication::common::HealthCheckResponse* response) {

    bool healthy = health_check_handler_ ? health_check_handler_() : true;
    response->set_status(healthy
        ? agent_communication::common::HealthCheckResponse::SERVING
        : agent_communication::common::HealthCheckResponse::NOT_SERVING);
    return grpc::Status::OK;
}

grpc::Status HealthServiceImpl::Watch(
    grpc::ServerContext* context,
    const agent_communication::common::HealthCheckRequest* /*request*/,
    grpc::ServerWriter<agent_communication::common::HealthCheckResponse>* writer) {

    while (!context->IsCancelled()) {
        agent_communication::common::HealthCheckResponse response;
        bool healthy = health_check_handler_ ? health_check_handler_() : true;
        response.set_status(healthy
            ? agent_communication::common::HealthCheckResponse::SERVING
            : agent_communication::common::HealthCheckResponse::NOT_SERVING);
        if (!writer->Write(response)) break;
        std::this_thread::sleep_for(std::chrono::seconds(5));
    }
    return grpc::Status::OK;
}

} // namespace server
} // namespace agent_rpc
