/**
 * @file ai_query_service.cpp
 * @brief AI Query Service implementation
 * 
 * Requirements: 2.1, 2.2, 2.5
 * Task 13: RPC服务扩展
 */

#include "agent_rpc/server/ai_query_service.h"
#include "agent_rpc/common/logger.h"
#include "agent_rpc/common/metrics.h"

#include <chrono>
#include <sstream>
#include <iomanip>
#include <uuid/uuid.h>
#include <algorithm>
#include <cctype>

namespace {

bool valid_context_id(const std::string& value) {
    if (value.empty()) return true;
    if (value.size() > 128 ||
        !std::isalnum(static_cast<unsigned char>(value.front()))) {
        return false;
    }
    return std::all_of(value.begin(), value.end(), [](unsigned char c) {
        return std::isalnum(c) || c == '-' || c == '_';
    });
}

bool valid_request_id(const std::string& value) {
    if (value.empty()) return true;
    if (value.size() > 128 ||
        !std::isalnum(static_cast<unsigned char>(value.front()))) {
        return false;
    }
    return std::all_of(value.begin(), value.end(), [](unsigned char c) {
        return std::isalnum(c) || c == '-' || c == '_';
    });
}

bool valid_question(const std::string& value) {
    if (value.empty() || value.size() > 8192 ||
        value.find('\0') != std::string::npos) {
        return false;
    }
    return !std::all_of(value.begin(), value.end(), [](unsigned char c) {
        return std::isspace(c) != 0;
    });
}

}  // namespace

namespace agent_rpc {
namespace server {

AIQueryServiceImpl::AIQueryServiceImpl()
    : a2a_adapter_(std::make_unique<a2a_adapter::A2AAdapter>()) {
}

AIQueryServiceImpl::~AIQueryServiceImpl() {
    shutdown();
}

bool AIQueryServiceImpl::initialize(
    const common::RpcConfig& rpc_config,
    const a2a_adapter::A2AConfig& a2a_config) {
    
    if (initialized_) {
        return true;
    }
    
    rpc_config_ = rpc_config;
    
    // Initialize A2A adapter
    if (!a2a_adapter_->initialize(a2a_config)) {
        LOG_ERROR("Failed to initialize A2A adapter");
        return false;
    }

    // Initialize circuit breaker for A2A backend
    circuit_breaker_ = common::CircuitBreakerManager::getInstance()
        .getCircuitBreaker("a2a_backend");

    initialized_ = true;
    LOG_INFO("AIQueryService initialized successfully");
    return true;
}

void AIQueryServiceImpl::shutdown() {
    if (!initialized_) {
        return;
    }
    
    if (a2a_adapter_) {
        a2a_adapter_->shutdown();
    }
    
    initialized_ = false;
    LOG_INFO("AIQueryService shutdown");
}

bool AIQueryServiceImpl::isAvailable() const {
    return initialized_ && a2a_adapter_ && a2a_adapter_->isAvailable();
}

grpc::Status AIQueryServiceImpl::Query(
    grpc::ServerContext* context,
    const agent_communication::AIQueryRequest* request,
    agent_communication::AIQueryResponse* response) {
    
    if (!isAvailable()) {
        return grpc::Status(grpc::StatusCode::UNAVAILABLE, 
                           "AI Query Service not available");
    }
    
    if (!request || !response) {
        return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                           "Invalid request or response");
    }

    if (!valid_request_id(request->request_id()) ||
        !valid_question(request->question()) ||
        !valid_context_id(request->context_id()) ||
        request->history_length() < 0 || request->history_length() > 1000) {
        return grpc::Status(
            grpc::StatusCode::INVALID_ARGUMENT,
            "request_id, question, context_id, or history_length is invalid");
    }
    
    auto start_time = std::chrono::steady_clock::now();
    
    // Generate request ID if not provided
    std::string request_id = request->request_id();
    if (request_id.empty()) {
        request_id = generateRequestId();
    }
    
    LOG_INFO("Processing AI query: " + request_id);
    
    // Check for cancellation
    if (context->IsCancelled()) {
        return grpc::Status(grpc::StatusCode::CANCELLED, "Request cancelled");
    }
    
    // Check circuit breaker before calling A2A backend
    if (circuit_breaker_ && !circuit_breaker_->isRequestAllowed()) {
        LOG_WARN("A2A backend circuit breaker open, rejecting query: " + request_id);
        auto* status = response->mutable_status();
        status->set_code(-1);
        status->set_message("A2A backend temporarily unavailable (circuit breaker open)");
        return grpc::Status(grpc::StatusCode::UNAVAILABLE, "A2A backend circuit breaker open");
    }

    // Process query via A2A adapter
    bool success = a2a_adapter_->processQuery(*request, response);

    // Record circuit breaker result
    if (circuit_breaker_) {
        if (success) circuit_breaker_->recordSuccess();
        else circuit_breaker_->recordFailure();
    }
    
    // Ensure request_id is set in response
    response->set_request_id(request_id);
    
    // Calculate duration
    auto end_time = std::chrono::steady_clock::now();
    auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(
        end_time - start_time);
    
    // Record metrics
    recordMetrics("Query", duration.count(), success);
    
    if (success) {
        LOG_INFO("AI query completed: " + request_id + 
                " in " + std::to_string(duration.count()) + "ms");
        return grpc::Status::OK;
    } else {
        LOG_ERROR("AI query failed: " + request_id);
        return grpc::Status(grpc::StatusCode::INTERNAL,
                           response->status().message());
    }
}

grpc::Status AIQueryServiceImpl::QueryStream(
    grpc::ServerContext* context,
    const agent_communication::AIQueryRequest* request,
    grpc::ServerWriter<agent_communication::AIStreamEvent>* writer) {
    
    if (!isAvailable()) {
        return grpc::Status(grpc::StatusCode::UNAVAILABLE,
                           "AI Query Service not available");
    }
    
    if (!request || !writer) {
        return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                           "Invalid request or writer");
    }


    if (!valid_request_id(request->request_id()) ||
        !valid_question(request->question()) ||
        !valid_context_id(request->context_id()) ||
        request->history_length() < 0 || request->history_length() > 1000) {
        return grpc::Status(
            grpc::StatusCode::INVALID_ARGUMENT,
            "request_id, question, context_id, or history_length is invalid");
    }
    
    auto start_time = std::chrono::steady_clock::now();
    
    std::string request_id = request->request_id();
    if (request_id.empty()) {
        request_id = generateRequestId();
    }
    
    LOG_INFO("Processing streaming AI query: " + request_id);

    // Check circuit breaker before calling A2A backend
    if (circuit_breaker_ && !circuit_breaker_->isRequestAllowed()) {
        LOG_WARN("A2A backend circuit breaker open, rejecting streaming query: " + request_id);
        return grpc::Status(grpc::StatusCode::UNAVAILABLE, "A2A backend circuit breaker open");
    }

    bool success = true;
    std::string error_message;
    
    // Process streaming query
    a2a_adapter_->processQueryStreaming(*request,
        [&context, &writer, &success, &error_message](
            const agent_communication::AIStreamEvent& event) {
            
            // Check for cancellation
            if (context->IsCancelled()) {
                success = false;
                error_message = "Request cancelled";
                return;
            }
            
            // Write event to stream
            if (!writer->Write(event)) {
                success = false;
                error_message = "Failed to write stream event";
            }
        });
    
    // Calculate duration
    auto end_time = std::chrono::steady_clock::now();
    auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(
        end_time - start_time);
    
    // Record metrics
    recordMetrics("QueryStream", duration.count(), success);

    // Record circuit breaker result
    if (circuit_breaker_) {
        if (success) circuit_breaker_->recordSuccess();
        else circuit_breaker_->recordFailure();
    }

    if (success) {
        LOG_INFO("Streaming AI query completed: " + request_id +
                " in " + std::to_string(duration.count()) + "ms");
        return grpc::Status::OK;
    } else {
        LOG_ERROR("Streaming AI query failed: " + request_id + 
                 " - " + error_message);
        return grpc::Status(grpc::StatusCode::INTERNAL, error_message);
    }
}

grpc::Status AIQueryServiceImpl::GetQueryStatus(
    grpc::ServerContext* context,
    const agent_communication::QueryStatusRequest* request,
    agent_communication::QueryStatusResponse* response) {
    
    if (!isAvailable()) {
        return grpc::Status(grpc::StatusCode::UNAVAILABLE,
                           "AI Query Service not available");
    }
    
    if (!request || !response) {
        return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                           "Invalid request or response");
    }
    
    // Check for cancellation
    if (context->IsCancelled()) {
        return grpc::Status(grpc::StatusCode::CANCELLED, "Request cancelled");
    }
    
    if (request->task_id().empty() && request->context_id().empty()) {
        return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                           "task_id or context_id is required");
    }

    LOG_INFO("Getting query status for task: " + request->task_id());

    // 当前 A2AAdapter 仅透传同步/流式查询，不维护可查询的任务状态仓库。
    // 显式返回“未实现”比返回 unknown stub 更清晰，也避免误导调用方。
    auto* status = response->mutable_status();
    status->set_code(static_cast<int>(grpc::StatusCode::UNIMPLEMENTED));
    status->set_message("Query status is not tracked by the current A2A adapter implementation");
    response->set_task_state("unavailable");

    return grpc::Status::OK;
}

std::string AIQueryServiceImpl::generateRequestId() {
    uuid_t uuid;
    uuid_generate(uuid);
    
    char uuid_str[37];
    uuid_unparse_lower(uuid, uuid_str);
    
    return std::string(uuid_str);
}

void AIQueryServiceImpl::recordMetrics(
    const std::string& method, 
    int64_t duration_ms, 
    bool success) {
    
    auto& metrics = common::Metrics::getInstance();
    metrics.recordRpcRequest("AIQueryService", method, duration_ms);
    
    if (success) {
        metrics.recordRpcResponse("AIQueryService", method, 0);
    } else {
        metrics.recordRpcError("AIQueryService", method, "Error");
    }
}

} // namespace server
} // namespace agent_rpc
