/**
 * @file ai_query_client.cpp
 * @brief AI Query Client implementation
 * 
 * Requirements: 2.1
 * Task 14: RPC客户端扩展
 */

#include "agent_rpc/client/ai_query_client.h"
#include "agent_rpc/common/logger.h"
#include "agent_rpc/common/metrics.h"

#include <chrono>
#include <uuid/uuid.h>

namespace agent_rpc {
namespace client {

AIQueryClient::AIQueryClient() = default;

AIQueryClient::~AIQueryClient() {
    disconnect();
}

bool AIQueryClient::connect(const std::string& server_address) {
    if (connected_) {
        if (server_address_ == server_address) {
            return true;
        }
        disconnect();
    }
    
    server_address_ = server_address;
    
    try {
        // Create channel with default options
        grpc::ChannelArguments args;
        args.SetMaxReceiveMessageSize(64 * 1024 * 1024);  // 64MB
        args.SetMaxSendMessageSize(64 * 1024 * 1024);
        
        channel_ = grpc::CreateCustomChannel(
            server_address,
            grpc::InsecureChannelCredentials(),
            args);
        
        if (!channel_) {
            LOG_ERROR("Failed to create gRPC channel to " + server_address);
            return false;
        }
        
        stub_ = agent_communication::AIQueryService::NewStub(channel_);
        
        if (!stub_) {
            LOG_ERROR("Failed to create AIQueryService stub");
            return false;
        }
        
        connected_ = true;
        LOG_INFO("AIQueryClient connected to " + server_address);
        return true;
        
    } catch (const std::exception& e) {
        LOG_ERROR("Failed to connect AIQueryClient: " + std::string(e.what()));
        return false;
    }
}

void AIQueryClient::disconnect() {
    if (!connected_) {
        return;
    }
    
    stub_.reset();
    channel_.reset();
    connected_ = false;
    
    LOG_INFO("AIQueryClient disconnected");
}

agent_communication::AIQueryResponse AIQueryClient::query(
    const std::string& question,
    const std::string& context_id,
    int timeout_seconds) {
    
    agent_communication::AIQueryRequest request;
    request.set_request_id(generateRequestId());
    request.set_question(question);
    request.set_context_id(context_id);
    request.set_timeout_seconds(timeout_seconds);
    
    return query(request);
}

agent_communication::AIQueryResponse AIQueryClient::query(
    const agent_communication::AIQueryRequest& request) {
    
    agent_communication::AIQueryResponse response;
    
    if (!connected_) {
        LOG_ERROR("AIQueryClient not connected");
        auto* status = response.mutable_status();
        status->set_code(-1);
        status->set_message("Client not connected");
        return response;
    }
    
    auto start_time = std::chrono::steady_clock::now();
    
    grpc::ClientContext context;
    
    // Set deadline
    int timeout = request.timeout_seconds() > 0 ? request.timeout_seconds() : 30;
    auto deadline = std::chrono::system_clock::now() + 
                   std::chrono::seconds(timeout);
    context.set_deadline(deadline);
    
    LOG_INFO("Sending AI query: " + request.request_id());
    
    grpc::Status status = stub_->Query(&context, request, &response);
    
    auto end_time = std::chrono::steady_clock::now();
    auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(
        end_time - start_time);
    
    if (status.ok()) {
        // gRPC 调用成功，检查响应状态
        if (response.status().code() == 0) {
            LOG_INFO("AI query completed: " + request.request_id() +
                    " in " + std::to_string(duration.count()) + "ms");
        } else {
            // 服务端返回了错误
            std::string error_msg = response.status().message();
            if (error_msg.empty()) {
                error_msg = "Unknown server error (code: " + 
                           std::to_string(response.status().code()) + ")";
            }
            LOG_ERROR("AI query failed: " + request.request_id() +
                    " - " + error_msg);
        }
    } else {
        // gRPC 调用本身失败
        std::string error_msg = status.error_message();
        if (error_msg.empty()) {
            error_msg = "gRPC error code: " + std::to_string(static_cast<int>(status.error_code()));
        }
        LOG_ERROR("AI query gRPC failed: " + request.request_id() +
                 " - " + error_msg);
        auto* resp_status = response.mutable_status();
        resp_status->set_code(static_cast<int>(status.error_code()));
        resp_status->set_message(error_msg);
    }
    
    return response;
}

bool AIQueryClient::queryStream(
    const std::string& question,
    StreamEventCallback callback,
    const std::string& context_id,
    int timeout_seconds) {
    
    agent_communication::AIQueryRequest request;
    request.set_request_id(generateRequestId());
    request.set_question(question);
    request.set_context_id(context_id);
    request.set_timeout_seconds(timeout_seconds);
    
    return queryStream(request, callback);
}

bool AIQueryClient::queryStream(
    const agent_communication::AIQueryRequest& request,
    StreamEventCallback callback) {
    
    if (!connected_) {
        LOG_ERROR("AIQueryClient not connected");
        return false;
    }
    
    if (!callback) {
        LOG_ERROR("No callback provided for streaming query");
        return false;
    }
    
    auto start_time = std::chrono::steady_clock::now();
    
    grpc::ClientContext context;
    
    // Set deadline
    int timeout = request.timeout_seconds() > 0 ? request.timeout_seconds() : 60;
    auto deadline = std::chrono::system_clock::now() +
                   std::chrono::seconds(timeout);
    context.set_deadline(deadline);
    
    LOG_INFO("Starting streaming AI query: " + request.request_id());
    
    std::unique_ptr<grpc::ClientReader<agent_communication::AIStreamEvent>> reader(
        stub_->QueryStream(&context, request));
    
    if (!reader) {
        LOG_ERROR("Failed to create stream reader");
        return false;
    }
    
    agent_communication::AIStreamEvent event;
    int event_count = 0;
    
    while (reader->Read(&event)) {
        event_count++;
        callback(event);
        
        // Check for completion or error
        if (event.event_type() == "complete" || event.event_type() == "error") {
            break;
        }
    }
    
    grpc::Status status = reader->Finish();
    
    auto end_time = std::chrono::steady_clock::now();
    auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(
        end_time - start_time);
    
    if (status.ok()) {
        LOG_INFO("Streaming AI query completed: " + request.request_id() +
                " with " + std::to_string(event_count) + " events" +
                " in " + std::to_string(duration.count()) + "ms");
        return true;
    } else {
        LOG_ERROR("Streaming AI query failed: " + request.request_id() +
                 " - " + status.error_message());
        return false;
    }
}

agent_communication::QueryStatusResponse AIQueryClient::getQueryStatus(
    const std::string& task_id,
    const std::string& context_id) {
    
    agent_communication::QueryStatusResponse response;
    
    if (!connected_) {
        LOG_ERROR("AIQueryClient not connected");
        auto* status = response.mutable_status();
        status->set_code(-1);
        status->set_message("Client not connected");
        return response;
    }
    
    grpc::ClientContext context;
    auto deadline = std::chrono::system_clock::now() + std::chrono::seconds(10);
    context.set_deadline(deadline);
    
    agent_communication::QueryStatusRequest request;
    request.set_task_id(task_id);
    request.set_context_id(context_id);
    
    LOG_INFO("Getting query status for task: " + task_id);
    
    grpc::Status status = stub_->GetQueryStatus(&context, request, &response);
    
    if (!status.ok()) {
        LOG_ERROR("Failed to get query status: " + status.error_message());
        auto* resp_status = response.mutable_status();
        resp_status->set_code(static_cast<int>(status.error_code()));
        resp_status->set_message(status.error_message());
    }
    
    return response;
}

std::string AIQueryClient::generateRequestId() {
    uuid_t uuid;
    uuid_generate(uuid);
    
    char uuid_str[37];
    uuid_unparse_lower(uuid, uuid_str);
    
    return std::string(uuid_str);
}

} // namespace client
} // namespace agent_rpc
