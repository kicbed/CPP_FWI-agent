/**
 * @file ai_query_client.h
 * @brief AI Query Client for gRPC
 * 
 * Requirements: 2.1
 * Task 14: RPC客户端扩展
 */

#pragma once

#include "agent_rpc/common/types.h"
#include "agent_rpc/common/logger.h"

#include "ai_query.grpc.pb.h"
#include "ai_query.pb.h"

#include <grpcpp/grpcpp.h>
#include <memory>
#include <string>
#include <functional>
#include <atomic>

namespace agent_rpc {
namespace client {

/**
 * @brief Callback type for streaming events
 */
using StreamEventCallback = std::function<void(const agent_communication::AIStreamEvent&)>;

/**
 * @brief AI Query Client
 * 
 * Client for the AIQueryService gRPC service.
 */
class AIQueryClient {
public:
    AIQueryClient();
    ~AIQueryClient();
    
    /**
     * @brief Connect to the AI Query service
     * @param server_address Server address (host:port)
     * @return true if connection successful
     */
    bool connect(const std::string& server_address);
    
    /**
     * @brief Disconnect from the service
     */
    void disconnect();
    
    /**
     * @brief Check if connected
     */
    bool isConnected() const { return connected_; }
    
    /**
     * @brief Get server address
     */
    const std::string& getServerAddress() const { return server_address_; }
    
    // ========================================================================
    // Query Methods
    // ========================================================================
    
    /**
     * @brief Send a synchronous AI query
     * @param question The question to ask
     * @param context_id Optional context ID for multi-turn conversation
     * @param timeout_seconds Query timeout
     * @return AI query response
     */
    agent_communication::AIQueryResponse query(
        const std::string& question,
        const std::string& context_id = "",
        int timeout_seconds = 30);
    
    /**
     * @brief Send a synchronous AI query with full request
     * @param request The full query request
     * @return AI query response
     */
    agent_communication::AIQueryResponse query(
        const agent_communication::AIQueryRequest& request);
    
    /**
     * @brief Send a streaming AI query
     * @param question The question to ask
     * @param callback Callback for stream events
     * @param context_id Optional context ID
     * @param timeout_seconds Query timeout
     * @return true if streaming completed successfully
     */
    bool queryStream(
        const std::string& question,
        StreamEventCallback callback,
        const std::string& context_id = "",
        int timeout_seconds = 60);
    
    /**
     * @brief Send a streaming AI query with full request
     * @param request The full query request
     * @param callback Callback for stream events
     * @return true if streaming completed successfully
     */
    bool queryStream(
        const agent_communication::AIQueryRequest& request,
        StreamEventCallback callback);
    
    /**
     * @brief Get query status
     * @param task_id Task ID to query
     * @param context_id Optional context ID
     * @return Query status response
     */
    agent_communication::QueryStatusResponse getQueryStatus(
        const std::string& task_id,
        const std::string& context_id = "");

private:
    std::string generateRequestId();
    
    std::shared_ptr<grpc::Channel> channel_;
    std::unique_ptr<agent_communication::AIQueryService::Stub> stub_;
    std::string server_address_;
    std::atomic<bool> connected_{false};
    std::atomic<uint64_t> request_counter_{0};
};

} // namespace client
} // namespace agent_rpc
