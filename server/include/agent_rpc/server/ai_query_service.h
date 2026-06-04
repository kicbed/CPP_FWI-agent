/**
 * @file ai_query_service.h
 * @brief AI Query Service implementation for gRPC
 * 
 * Requirements: 2.1, 2.2, 2.5
 * Task 13: RPC服务扩展
 */

#pragma once

#include "agent_rpc/common/types.h"
#include "agent_rpc/common/logger.h"
#include "agent_rpc/common/metrics.h"
#include "agent_rpc/common/circuit_breaker.h"
#include "agent_rpc/a2a_adapter/a2a_adapter.h"
#include "agent_rpc/a2a_adapter/a2a_config.h"

#include "ai_query.grpc.pb.h"
#include "ai_query.pb.h"

#include <grpcpp/grpcpp.h>
#include <memory>
#include <string>
#include <atomic>

namespace agent_rpc {
namespace server {

/**
 * @brief AI Query Service implementation
 * 
 * Implements the AIQueryService gRPC service, bridging RPC requests
 * to the A2A protocol via the A2AAdapter.
 */
class AIQueryServiceImpl final : public agent_communication::AIQueryService::Service {
public:
    AIQueryServiceImpl();
    ~AIQueryServiceImpl();
    
    /**
     * @brief Initialize the service with configuration
     * @param rpc_config RPC configuration
     * @param a2a_config A2A adapter configuration
     * @return true if initialization successful
     */
    bool initialize(const common::RpcConfig& rpc_config,
                   const a2a_adapter::A2AConfig& a2a_config);
    
    /**
     * @brief Shutdown the service
     */
    void shutdown();
    
    /**
     * @brief Check if service is available
     */
    bool isAvailable() const;
    
    // ========================================================================
    // gRPC Service Methods
    // ========================================================================
    
    /**
     * @brief Synchronous AI query
     * @param context gRPC server context
     * @param request AI query request
     * @param response AI query response
     * @return gRPC status
     */
    grpc::Status Query(
        grpc::ServerContext* context,
        const agent_communication::AIQueryRequest* request,
        agent_communication::AIQueryResponse* response) override;
    
    /**
     * @brief Streaming AI query
     * @param context gRPC server context
     * @param request AI query request
     * @param writer Stream writer for events
     * @return gRPC status
     */
    grpc::Status QueryStream(
        grpc::ServerContext* context,
        const agent_communication::AIQueryRequest* request,
        grpc::ServerWriter<agent_communication::AIStreamEvent>* writer) override;
    
    /**
     * @brief Get query status
     * @param context gRPC server context
     * @param request Status request
     * @param response Status response
     * @return gRPC status
     */
    grpc::Status GetQueryStatus(
        grpc::ServerContext* context,
        const agent_communication::QueryStatusRequest* request,
        agent_communication::QueryStatusResponse* response) override;
    
    // ========================================================================
    // Accessors
    // ========================================================================
    
    /**
     * @brief Get the A2A adapter
     */
    a2a_adapter::A2AAdapter* getA2AAdapter() { return a2a_adapter_.get(); }

private:
    std::string generateRequestId();
    void recordMetrics(const std::string& method, int64_t duration_ms, bool success);
    
    std::unique_ptr<a2a_adapter::A2AAdapter> a2a_adapter_;
    std::shared_ptr<common::CircuitBreaker> circuit_breaker_;
    common::RpcConfig rpc_config_;
    std::atomic<bool> initialized_{false};
    std::atomic<uint64_t> request_counter_{0};
};

} // namespace server
} // namespace agent_rpc
