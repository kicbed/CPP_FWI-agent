#include "agent_rpc/client/rpc_client.h"
#include "agent_rpc/common/logger.h"
#include "agent_rpc/common/message_converter.h"
#include "agent_rpc/common/metrics.h"
#include "agent_rpc/common/serializer.h"
#include "agent_service.grpc.pb.h"
#include "agent_service.pb.h"
#include "common.pb.h"

#include <grpcpp/create_channel.h>
#include <grpcpp/generic/generic_stub.h>
#include <grpcpp/grpcpp.h>
#include <grpcpp/security/credentials.h>

#include <algorithm>
#include <chrono>
#include <set>
#include <sstream>
#include <stdexcept>
#include <thread>

namespace {

constexpr const char* kDefaultServiceName = "rpc_server";
constexpr int kConnectTimeoutSeconds = 3;

std::string endpointId(const agent_rpc::common::ServiceEndpoint& endpoint) {
    return endpoint.host + ":" + std::to_string(endpoint.port);
}

std::string endpointAddress(const agent_rpc::common::ServiceEndpoint& endpoint) {
    auto it = endpoint.metadata.find("address");
    if (it != endpoint.metadata.end() && !it->second.empty()) {
        return it->second;
    }
    return endpointId(endpoint);
}

bool isRetryableTransportStatus(const grpc::Status& status) {
    switch (status.error_code()) {
        case grpc::StatusCode::UNAVAILABLE:
        case grpc::StatusCode::DEADLINE_EXCEEDED:
        case grpc::StatusCode::RESOURCE_EXHAUSTED:
        case grpc::StatusCode::ABORTED:
            return true;
        default:
            return false;
    }
}

std::string stripKnownScheme(const std::string& address) {
    static const std::vector<std::string> schemes = {
        "dns:///",
        "grpc://",
        "http://",
        "https://"
    };

    for (const auto& scheme : schemes) {
        if (address.rfind(scheme, 0) == 0) {
            return address.substr(scheme.size());
        }
    }

    return address;
}

}  // namespace

namespace agent_rpc {
namespace client {

RpcClient::RpcClient()
    : heartbeat_running_(false),
      connection_retry_count_(0),
      ai_query_client_(std::make_unique<AIQueryClient>()),
      registry_watch_active_(std::make_shared<std::atomic<bool>>(true)) {}

RpcClient::~RpcClient() {
    if (registry_watch_active_) {
        registry_watch_active_->store(false);
    }
    disconnect();
    stopHeartbeat();
}

bool RpcClient::initialize(const common::RpcConfig& config) {
    config_ = config;

    common::MessageSerializer::getInstance().initialize(common::SerializerFactory::PROTOBUF_BINARY);
    circuit_breaker_ = common::CircuitBreakerManager::getInstance().getCircuitBreaker("rpc_client");

    if (!load_balancer_) {
        load_balancer_ = common::LoadBalancerFactory::createLoadBalancer(
            common::LoadBalanceStrategy::ROUND_ROBIN);
    }

    LOG_INFO("RPC client initialized");
    return true;
}

bool RpcClient::connect(const std::string& server_address) {
    return connect(std::vector<std::string>{server_address},
                   common::LoadBalanceStrategy::ROUND_ROBIN);
}

bool RpcClient::connect(const std::vector<std::string>& server_addresses,
                        common::LoadBalanceStrategy strategy) {
    if (server_addresses.empty()) {
        LOG_ERROR("No server addresses provided");
        return false;
    }

    std::vector<common::ServiceEndpoint> endpoints;
    endpoints.reserve(server_addresses.size());
    for (const auto& address : server_addresses) {
        if (address.empty()) {
            continue;
        }
        endpoints.push_back(parseEndpoint(address));
    }

    if (endpoints.empty()) {
        LOG_ERROR("No valid server endpoints provided");
        return false;
    }

    auto load_balancer = common::LoadBalancerFactory::createLoadBalancer(strategy);
    load_balancer->updateEndpoints(endpoints);

    common::ServiceEndpoint selected_endpoint;
    try {
        selected_endpoint = load_balancer->selectEndpoint(endpoints);
    } catch (const std::exception& e) {
        LOG_ERROR("Failed to select endpoint: " + std::string(e.what()));
        return false;
    }

    {
        std::lock_guard<std::mutex> lock(connection_mutex_);
        server_endpoints_ = endpoints;
        load_balancer_ = std::move(load_balancer);
        discovered_service_name_.clear();
    }

    return connectToEndpoint(selected_endpoint);
}

bool RpcClient::connectViaRegistry(const std::string& registry_address,
                                   const std::string& service_name,
                                   common::LoadBalanceStrategy strategy) {
    if (service_name.empty()) {
        LOG_ERROR("Service name is required for registry discovery");
        return false;
    }

    if (!service_registry_) {
        if (registry_address == "memory" || registry_address.rfind("memory://", 0) == 0) {
            service_registry_ = std::make_shared<registry::MemoryServiceRegistry>();
        } else if (registry_address.rfind("consul://", 0) == 0) {
            auto consul = std::make_shared<registry::ConsulServiceRegistry>();
            consul->initialize(registry_address.substr(std::string("consul://").size()));
            service_registry_ = consul;
        } else if (registry_address.rfind("etcd://", 0) == 0) {
            auto etcd = std::make_shared<registry::EtcdServiceRegistry>();
            etcd->initialize(registry_address.substr(std::string("etcd://").size()));
            service_registry_ = etcd;
        } else {
            auto consul = std::make_shared<registry::ConsulServiceRegistry>();
            consul->initialize(registry_address);
            service_registry_ = consul;
        }
    }

    auto discovered_endpoints = service_registry_->discoverServices(service_name);
    if (discovered_endpoints.empty()) {
        LOG_WARN("No endpoints discovered for service: " + service_name);
        return false;
    }

    auto load_balancer = common::LoadBalancerFactory::createLoadBalancer(strategy);
    load_balancer->updateEndpoints(discovered_endpoints);

    common::ServiceEndpoint selected_endpoint;
    try {
        selected_endpoint = load_balancer->selectEndpoint(discovered_endpoints);
    } catch (const std::exception& e) {
        LOG_ERROR("Failed to select registry endpoint: " + std::string(e.what()));
        return false;
    }

    {
        std::lock_guard<std::mutex> lock(connection_mutex_);
        discovered_service_name_ = service_name;
        server_endpoints_ = discovered_endpoints;
        load_balancer_ = std::move(load_balancer);
    }

    const auto watch_active = registry_watch_active_;
    service_registry_->watchServices(
        service_name,
        [this, service_name, watch_active](const std::vector<common::ServiceEndpoint>& endpoints) {
            if (!watch_active || !watch_active->load()) {
                return;
            }

            {
                std::lock_guard<std::mutex> lock(connection_mutex_);
                server_endpoints_ = endpoints;
                discovered_service_name_ = service_name;
            }

            if (load_balancer_) {
                load_balancer_->updateEndpoints(endpoints);
            }

            LOG_INFO("Service registry updated " + service_name + " endpoints: " +
                     std::to_string(endpoints.size()));
        });

    return connectToEndpoint(selected_endpoint);
}

void RpcClient::setServiceRegistry(std::shared_ptr<registry::ServiceRegistry> service_registry) {
    service_registry_ = std::move(service_registry);
}

void RpcClient::disconnect() {
    stopHeartbeat();

    if (ai_query_client_) {
        ai_query_client_->disconnect();
    }

    std::lock_guard<std::mutex> lock(connection_mutex_);
    channel_.reset();
    stub_.reset();
    agent_stub_.reset();
    connected_ = false;
    current_endpoint_id_.clear();

    LOG_INFO("Disconnected from RPC server");
}

bool RpcClient::sendMessage(const std::string& message,
                            const std::string& target_agent,
                            int timeout_seconds) {
    if (!connected_) {
        LOG_ERROR("Client not connected");
        return false;
    }
    if (circuit_breaker_ && !circuit_breaker_->isRequestAllowed()) {
        LOG_WARN("Circuit breaker open, rejecting sendMessage");
        return false;
    }

    auto start = std::chrono::steady_clock::now();

    agent_communication::SendMessageRequest request;
    request.mutable_message()->set_content(message);
    request.mutable_message()->set_id(
        std::to_string(std::chrono::system_clock::now().time_since_epoch().count()));
    request.set_target_agent(target_agent);
    request.set_timeout_seconds(timeout_seconds);

    agent_communication::SendMessageResponse response;
    grpc::ClientContext context;
    context.set_deadline(std::chrono::system_clock::now() + std::chrono::seconds(timeout_seconds));
    grpc::Status status = agent_stub_->SendMessage(&context, request, &response);

    auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - start);
    auto& metrics = common::Metrics::getInstance();

    if (status.ok() && response.status().code() == 0) {
        if (circuit_breaker_) {
            circuit_breaker_->recordSuccess();
        }
        metrics.recordRpcRequest("AgentCommunicationService", "SendMessage", duration.count());
        return true;
    }

    if (circuit_breaker_) {
        circuit_breaker_->recordFailure();
    }
    metrics.recordRpcError("AgentCommunicationService",
                           "SendMessage",
                           status.ok() ? response.status().message() : status.error_message());

    if (!status.ok()) {
        handleTransportFailure(status);
    }

    return false;
}

std::vector<std::string> RpcClient::receiveMessages(const std::string& agent_id,
                                                    int max_messages,
                                                    int timeout_seconds) {
    std::vector<std::string> messages;
    if (!connected_) {
        LOG_ERROR("Client not connected");
        return messages;
    }
    if (circuit_breaker_ && !circuit_breaker_->isRequestAllowed()) {
        LOG_WARN("Circuit breaker open, rejecting receiveMessages");
        return messages;
    }

    agent_communication::ReceiveMessageRequest request;
    request.set_agent_id(agent_id);
    request.set_max_messages(max_messages);
    request.set_timeout_seconds(timeout_seconds);

    agent_communication::ReceiveMessageResponse response;
    grpc::ClientContext context;
    context.set_deadline(std::chrono::system_clock::now() + std::chrono::seconds(timeout_seconds));
    grpc::Status status = agent_stub_->ReceiveMessage(&context, request, &response);

    if (status.ok() && response.status().code() == 0) {
        if (circuit_breaker_) {
            circuit_breaker_->recordSuccess();
        }
        for (const auto& msg : response.messages()) {
            messages.push_back(msg.content());
        }
        return messages;
    }

    if (circuit_breaker_) {
        circuit_breaker_->recordFailure();
    }

    if (!status.ok()) {
        common::Metrics::getInstance().recordRpcError(
            "AgentCommunicationService", "ReceiveMessage", status.error_message());
        handleTransportFailure(status);
    } else {
        LOG_ERROR("receiveMessages failed: " + response.status().message());
    }

    return messages;
}

int RpcClient::broadcastMessage(const std::string& message,
                                const std::vector<std::string>& target_agents,
                                bool exclude_sender) {
    if (!connected_) {
        LOG_ERROR("Client not connected");
        return 0;
    }
    if (circuit_breaker_ && !circuit_breaker_->isRequestAllowed()) {
        return 0;
    }

    agent_communication::BroadcastMessageRequest request;
    request.mutable_message()->set_content(message);
    request.set_exclude_sender(exclude_sender);
    for (const auto& agent : target_agents) {
        request.add_target_agents(agent);
    }

    agent_communication::BroadcastMessageResponse response;
    grpc::ClientContext context;
    context.set_deadline(std::chrono::system_clock::now() + std::chrono::seconds(30));
    grpc::Status status = agent_stub_->BroadcastMessage(&context, request, &response);

    if (status.ok() && response.status().code() == 0) {
        if (circuit_breaker_) {
            circuit_breaker_->recordSuccess();
        }
        return response.success_count();
    }

    if (circuit_breaker_) {
        circuit_breaker_->recordFailure();
    }
    if (!status.ok()) {
        common::Metrics::getInstance().recordRpcError(
            "AgentCommunicationService", "BroadcastMessage", status.error_message());
        handleTransportFailure(status);
    }
    return 0;
}

std::vector<common::ServiceEndpoint> RpcClient::getAgents(const std::string& filter,
                                                          int limit,
                                                          int offset) {
    std::vector<common::ServiceEndpoint> agents;
    if (!connected_) {
        LOG_ERROR("Client not connected");
        return agents;
    }
    if (circuit_breaker_ && !circuit_breaker_->isRequestAllowed()) {
        return agents;
    }

    agent_communication::GetAgentsRequest request;
    request.set_filter(filter);
    request.set_limit(limit);
    request.set_offset(offset);

    agent_communication::GetAgentsResponse response;
    grpc::ClientContext context;
    context.set_deadline(std::chrono::system_clock::now() + std::chrono::seconds(30));
    grpc::Status status = agent_stub_->GetAgents(&context, request, &response);

    if (status.ok() && response.status().code() == 0) {
        if (circuit_breaker_) {
            circuit_breaker_->recordSuccess();
        }
        for (const auto& info : response.agents()) {
            common::ServiceEndpoint endpoint;
            endpoint.host = info.host();
            endpoint.port = info.port();
            endpoint.service_name = info.service_name();
            endpoint.version = info.version();
            for (const auto& item : info.metadata()) {
                endpoint.metadata[item.first] = item.second;
            }
            agents.push_back(endpoint);
        }
        return agents;
    }

    if (circuit_breaker_) {
        circuit_breaker_->recordFailure();
    }
    if (!status.ok()) {
        common::Metrics::getInstance().recordRpcError(
            "AgentCommunicationService", "GetAgents", status.error_message());
        handleTransportFailure(status);
    }
    return agents;
}

std::string RpcClient::registerAgent(const common::ServiceEndpoint& agent_info,
                                     int heartbeat_interval) {
    if (!connected_) {
        LOG_ERROR("Client not connected");
        return "";
    }
    if (circuit_breaker_ && !circuit_breaker_->isRequestAllowed()) {
        return "";
    }

    agent_communication::RegisterAgentRequest request;
    auto* info = request.mutable_agent_info();
    info->set_host(agent_info.host);
    info->set_port(agent_info.port);
    info->set_service_name(agent_info.service_name);
    info->set_version(agent_info.version);
    for (const auto& metadata : agent_info.metadata) {
        (*info->mutable_metadata())[metadata.first] = metadata.second;
    }
    request.set_heartbeat_interval(heartbeat_interval);

    agent_communication::RegisterAgentResponse response;
    grpc::ClientContext context;
    context.set_deadline(std::chrono::system_clock::now() + std::chrono::seconds(30));
    grpc::Status status = agent_stub_->RegisterAgent(&context, request, &response);

    if (status.ok() && response.status().code() == 0) {
        if (circuit_breaker_) {
            circuit_breaker_->recordSuccess();
        }
        current_agent_id_ = response.agent_id();
        current_agent_info_ = agent_info;
        startHeartbeat();
        LOG_INFO("Agent registered: " + current_agent_id_);
        return current_agent_id_;
    }

    if (circuit_breaker_) {
        circuit_breaker_->recordFailure();
    }
    if (!status.ok()) {
        common::Metrics::getInstance().recordRpcError(
            "AgentCommunicationService", "RegisterAgent", status.error_message());
        handleTransportFailure(status);
    }
    return "";
}

bool RpcClient::unregisterAgent(const std::string& agent_id, const std::string& reason) {
    if (!connected_) {
        LOG_ERROR("Client not connected");
        return false;
    }

    stopHeartbeat();

    agent_communication::UnregisterAgentRequest request;
    request.set_agent_id(agent_id);
    request.set_reason(reason);

    agent_communication::UnregisterAgentResponse response;
    grpc::ClientContext context;
    context.set_deadline(std::chrono::system_clock::now() + std::chrono::seconds(30));
    grpc::Status status = agent_stub_->UnregisterAgent(&context, request, &response);

    if (status.ok() && response.status().code() == 0) {
        if (circuit_breaker_) {
            circuit_breaker_->recordSuccess();
        }
        current_agent_id_.clear();
        LOG_INFO("Agent unregistered: " + agent_id);
        return true;
    }

    if (circuit_breaker_) {
        circuit_breaker_->recordFailure();
    }
    if (!status.ok()) {
        common::Metrics::getInstance().recordRpcError(
            "AgentCommunicationService", "UnregisterAgent", status.error_message());
        handleTransportFailure(status);
    }
    return false;
}

bool RpcClient::sendHeartbeat(const std::string& agent_id,
                              const common::ServiceEndpoint& agent_info) {
    if (!connected_) {
        return false;
    }

    agent_communication::HeartbeatRequest request;
    request.set_agent_id(agent_id);
    auto* info = request.mutable_agent_info();
    info->set_host(agent_info.host);
    info->set_port(agent_info.port);
    info->set_service_name(agent_info.service_name);
    info->set_version(agent_info.version);

    agent_communication::HeartbeatResponse response;
    grpc::ClientContext context;
    context.set_deadline(std::chrono::system_clock::now() + std::chrono::seconds(10));
    grpc::Status status = agent_stub_->Heartbeat(&context, request, &response);

    if (status.ok() && response.status().code() == 0) {
        return true;
    }

    if (!status.ok()) {
        common::Metrics::getInstance().recordRpcError(
            "AgentCommunicationService", "Heartbeat", status.error_message());
        handleTransportFailure(status);
    }
    return false;
}

void RpcClient::listenMessages(const std::string& agent_id,
                               common::MessageHandler handler,
                               int max_messages,
                               int timeout_seconds) {
    if (!connected_) {
        LOG_ERROR("Client not connected");
        return;
    }
    if (circuit_breaker_ && !circuit_breaker_->isRequestAllowed()) {
        LOG_WARN("Circuit breaker open, rejecting listenMessages");
        return;
    }

    auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(timeout_seconds);
    int received = 0;
    while (received < max_messages && std::chrono::steady_clock::now() < deadline) {
        auto messages = receiveMessages(agent_id, max_messages - received, 1);
        for (const auto& message : messages) {
            if (handler) {
                handler(message);
            }
            received++;
        }
        if (messages.empty()) {
            std::this_thread::sleep_for(std::chrono::milliseconds(200));
        }
    }
}

void RpcClient::setMessageHandler(common::MessageHandler handler) {
    message_handler_ = std::move(handler);
}

void RpcClient::setErrorHandler(common::ErrorHandler handler) {
    error_handler_ = std::move(handler);
}

void RpcClient::setupChannel() {
    grpc::ChannelArguments args;
    args.SetMaxReceiveMessageSize(config_.max_receive_message_size);
    args.SetMaxSendMessageSize(config_.max_message_size);

    if (config_.enable_ssl) {
        setupSslCredentials();
        channel_ = grpc::CreateCustomChannel(
            server_address_, grpc::SslCredentials(grpc::SslCredentialsOptions()), args);
    } else {
        channel_ = grpc::CreateCustomChannel(
            server_address_, grpc::InsecureChannelCredentials(), args);
    }

    if (!channel_) {
        throw std::runtime_error("Failed to create gRPC channel");
    }

    const auto deadline =
        std::chrono::system_clock::now() + std::chrono::seconds(kConnectTimeoutSeconds);
    if (!channel_->WaitForConnected(deadline)) {
        throw std::runtime_error("Timed out connecting to " + server_address_);
    }

    stub_ = std::make_unique<grpc::TemplatedGenericStub<grpc::ByteBuffer, grpc::ByteBuffer>>(channel_);
    agent_stub_ = agent_communication::AgentCommunicationService::NewStub(channel_);

    if (!stub_ || !agent_stub_) {
        throw std::runtime_error("Failed to create gRPC stub");
    }
}

void RpcClient::setupSslCredentials() {
    // SSL证书配置逻辑预留
}

bool RpcClient::reconnect() {
    if (connection_retry_count_ >= MAX_RETRY_COUNT) {
        LOG_ERROR("Max reconnection attempts reached");
        return false;
    }

    std::this_thread::sleep_for(
        std::chrono::milliseconds(RETRY_DELAY_MS * (connection_retry_count_ + 1)));

    if (service_registry_ && !discovered_service_name_.empty()) {
        auto refreshed_endpoints = service_registry_->discoverServices(discovered_service_name_);
        if (!refreshed_endpoints.empty()) {
            std::lock_guard<std::mutex> lock(connection_mutex_);
            server_endpoints_ = refreshed_endpoints;
            if (load_balancer_) {
                load_balancer_->updateEndpoints(server_endpoints_);
            }
        }
    }

    std::vector<common::ServiceEndpoint> endpoints_snapshot;
    {
        std::lock_guard<std::mutex> lock(connection_mutex_);
        endpoints_snapshot = server_endpoints_;
    }

    if (endpoints_snapshot.empty()) {
        try {
            setupChannel();
            connected_ = true;
            connection_retry_count_ = 0;
            last_connection_time_ = std::chrono::steady_clock::now();
            LOG_INFO("Reconnected to server successfully");
            return true;
        } catch (const std::exception& e) {
            connection_retry_count_++;
            LOG_ERROR("Reconnection failed: " + std::string(e.what()));
            return false;
        }
    }

    bool has_healthy_endpoint =
        std::any_of(endpoints_snapshot.begin(), endpoints_snapshot.end(), [](const auto& endpoint) {
            return endpoint.is_healthy;
        });
    if (!has_healthy_endpoint) {
        for (auto& endpoint : endpoints_snapshot) {
            endpoint.is_healthy = true;
        }
        std::lock_guard<std::mutex> lock(connection_mutex_);
        server_endpoints_ = endpoints_snapshot;
        if (load_balancer_) {
            load_balancer_->updateEndpoints(server_endpoints_);
        }
    }

    std::vector<common::ServiceEndpoint> candidates;
    try {
        if (load_balancer_) {
            candidates.push_back(load_balancer_->selectEndpoint(endpoints_snapshot));
        }
    } catch (const std::exception&) {
    }

    std::set<std::string> seen_ids;
    for (const auto& endpoint : candidates) {
        seen_ids.insert(endpointId(endpoint));
    }
    for (const auto& endpoint : endpoints_snapshot) {
        if (seen_ids.insert(endpointId(endpoint)).second && endpoint.is_healthy) {
            candidates.push_back(endpoint);
        }
    }

    for (const auto& endpoint : candidates) {
        if (connectToEndpoint(endpoint)) {
            LOG_INFO("Reconnected to endpoint: " + endpointId(endpoint));
            return true;
        }
    }

    connection_retry_count_++;
    LOG_ERROR("Reconnection failed after trying " + std::to_string(candidates.size()) + " endpoints");
    return false;
}

bool RpcClient::connectToEndpoint(const common::ServiceEndpoint& endpoint) {
    const std::string address = endpointAddress(endpoint);

    try {
        {
            std::lock_guard<std::mutex> lock(connection_mutex_);
            server_address_ = address;
            current_endpoint_id_ = endpointId(endpoint);
        }

        setupChannel();
        connected_ = true;
        last_connection_time_ = std::chrono::steady_clock::now();
        connection_retry_count_ = 0;

        if (load_balancer_) {
            load_balancer_->markEndpointStatus(current_endpoint_id_, true);
        }

        {
            std::lock_guard<std::mutex> lock(connection_mutex_);
            for (auto& known_endpoint : server_endpoints_) {
                if (endpointId(known_endpoint) == current_endpoint_id_) {
                    known_endpoint.is_healthy = true;
                }
            }
        }

        if (ai_query_client_ && !ai_query_client_->connect(address)) {
            LOG_WARN("Failed to connect AIQueryClient, AI queries will not be available");
        }

        LOG_INFO("Connected to RPC server endpoint: " + address);
        return true;
    } catch (const std::exception& e) {
        connected_ = false;
        LOG_ERROR("Failed to connect to RPC server endpoint " + address + ": " + e.what());
        return false;
    }
}

bool RpcClient::handleTransportFailure(const grpc::Status& status) {
    if (status.ok() || !isRetryableTransportStatus(status)) {
        return false;
    }

    std::string failed_endpoint_id;
    {
        std::lock_guard<std::mutex> lock(connection_mutex_);
        failed_endpoint_id = current_endpoint_id_;
        connected_ = false;
        for (auto& endpoint : server_endpoints_) {
            if (endpointId(endpoint) == failed_endpoint_id) {
                endpoint.is_healthy = false;
                break;
            }
        }
    }

    if (load_balancer_ && !failed_endpoint_id.empty()) {
        load_balancer_->markEndpointStatus(failed_endpoint_id, false);
    }

    LOG_WARN("Transport failure on endpoint " + failed_endpoint_id + ": " + status.error_message());
    return reconnect();
}

common::ServiceEndpoint RpcClient::parseEndpoint(const std::string& server_address) const {
    common::ServiceEndpoint endpoint;
    endpoint.service_name = kDefaultServiceName;
    endpoint.version = "1.0.0";
    endpoint.is_healthy = true;
    endpoint.last_heartbeat = std::chrono::steady_clock::now();
    endpoint.metadata["address"] = server_address;

    const auto normalized_address = stripKnownScheme(server_address);
    const auto separator = normalized_address.rfind(':');
    if (separator == std::string::npos) {
        endpoint.host = normalized_address;
        endpoint.port = 50051;
        return endpoint;
    }

    endpoint.host = normalized_address.substr(0, separator);
    endpoint.port = std::stoi(normalized_address.substr(separator + 1));
    return endpoint;
}

void RpcClient::startHeartbeat() {
    if (heartbeat_running_) {
        return;
    }

    heartbeat_running_ = true;
    heartbeat_thread_ = std::thread([this]() { heartbeatLoop(); });
}

void RpcClient::stopHeartbeat() {
    if (!heartbeat_running_) {
        return;
    }

    heartbeat_running_ = false;
    if (heartbeat_thread_.joinable()) {
        heartbeat_thread_.join();
    }
}

void RpcClient::heartbeatLoop() {
    while (heartbeat_running_) {
        if (connected_ && !current_agent_id_.empty()) {
            if (!sendHeartbeat(current_agent_id_, current_agent_info_)) {
                if (circuit_breaker_) {
                    circuit_breaker_->recordFailure();
                }
                LOG_WARN("Heartbeat failed, attempting reconnection");
                connected_ = false;
                if (!reconnect()) {
                    LOG_ERROR("Failed to reconnect, stopping heartbeat");
                    break;
                }
            } else if (circuit_breaker_) {
                circuit_breaker_->recordSuccess();
            }
        }

        std::this_thread::sleep_for(std::chrono::seconds(config_.heartbeat_interval));
    }
}

agent_communication::AIQueryResponse RpcClient::aiQuery(const std::string& question,
                                                        const std::string& context_id,
                                                        int timeout_seconds) {
    if (!ai_query_client_ || !ai_query_client_->isConnected()) {
        agent_communication::AIQueryResponse response;
        auto* status = response.mutable_status();
        status->set_code(-1);
        status->set_message("AIQueryClient not connected");
        LOG_ERROR("AIQueryClient not connected");
        return response;
    }

    if (circuit_breaker_ && !circuit_breaker_->isRequestAllowed()) {
        agent_communication::AIQueryResponse response;
        auto* status = response.mutable_status();
        status->set_code(-1);
        status->set_message("Circuit breaker open");
        LOG_WARN("Circuit breaker open, rejecting AI query");
        return response;
    }

    auto response = ai_query_client_->query(question, context_id, timeout_seconds);

    if (circuit_breaker_) {
        if (response.status().code() == 0) {
            circuit_breaker_->recordSuccess();
        } else {
            circuit_breaker_->recordFailure();
        }
    }

    return response;
}

bool RpcClient::aiQueryStream(const std::string& question,
                              StreamEventCallback callback,
                              const std::string& context_id,
                              int timeout_seconds) {
    if (!ai_query_client_ || !ai_query_client_->isConnected()) {
        LOG_ERROR("AIQueryClient not connected");
        return false;
    }

    if (circuit_breaker_ && !circuit_breaker_->isRequestAllowed()) {
        LOG_WARN("Circuit breaker open, rejecting streaming AI query");
        return false;
    }

    bool success = ai_query_client_->queryStream(question, callback, context_id, timeout_seconds);

    if (circuit_breaker_) {
        if (success) {
            circuit_breaker_->recordSuccess();
        } else {
            circuit_breaker_->recordFailure();
        }
    }

    return success;
}

}  // namespace client
}  // namespace agent_rpc
