/**
 * @file a2a_adapter.h
 * @brief Main A2A adapter class
 * 
 * Requirements: 8.1, 8.2, 8.5
 */

#pragma once

#include "a2a_config.h"
#include "request_adapter.h"
#include "response_adapter.h"
#include "error_mapper.h"

#include <a2a/client/a2a_client.hpp>
#include <memory>
#include <functional>
#include <atomic>

// Forward declarations
namespace agent_communication {
class AIQueryRequest;
class AIQueryResponse;
class AIStreamEvent;
}

namespace agent_rpc {
namespace a2a_adapter {

/**
 * @brief Main adapter class bridging RPC and A2A protocol
 */
class A2AAdapter {
public:
    A2AAdapter();
    ~A2AAdapter();
    
    /**
     * @brief Initialize the adapter with configuration
     * @param config The A2A configuration
     * @return true if initialization successful
     */
    bool initialize(const A2AConfig& config);
    
    /**
     * @brief Shutdown the adapter
     */
    void shutdown();
    
    /**
     * @brief Process a synchronous AI query
     * @param request The RPC request
     * @param response The RPC response to populate
     * @return true if query successful
     */
    bool processQuery(
        const agent_communication::AIQueryRequest& request,
        agent_communication::AIQueryResponse* response);
    
    /**
     * @brief Process an asynchronous AI query
     * @param request The RPC request
     * @param callback Callback to invoke with response
     */
    void processQueryAsync(
        const agent_communication::AIQueryRequest& request,
        std::function<void(const agent_communication::AIQueryResponse&)> callback);
    
    /**
     * @brief Process a streaming AI query
     * @param request The RPC request
     * @param callback Callback to invoke for each stream event
     */
    void processQueryStreaming(
        const agent_communication::AIQueryRequest& request,
        std::function<void(const agent_communication::AIStreamEvent&)> callback);
    
    /**
     * @brief Check if the adapter is available
     * @return true if adapter is initialized and ready
     */
    bool isAvailable() const;
    
    /**
     * @brief Get the current configuration
     * @return Current A2A configuration
     */
    const A2AConfig& getConfig() const { return config_; }
    
    /**
     * @brief Get the request adapter
     */
    RequestAdapter& getRequestAdapter() { return *request_adapter_; }
    
    /**
     * @brief Get the response adapter
     */
    ResponseAdapter& getResponseAdapter() { return *response_adapter_; }

private:
    std::unique_ptr<a2a::A2AClient> a2a_client_;
    std::unique_ptr<RequestAdapter> request_adapter_;
    std::unique_ptr<ResponseAdapter> response_adapter_;
    A2AConfig config_;
    std::atomic<bool> initialized_{false};
};

} // namespace a2a_adapter
} // namespace agent_rpc
