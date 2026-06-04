#pragma once

#include "agent_rpc/common/types.h"
#include "agent_rpc/common/logger.h"
#include "agent_rpc/common/metrics.h"
#include "agent_rpc/common/circuit_breaker.h"
#include "agent_rpc/common/load_balancer.h"
#include "agent_rpc/registry/service_registry.h"
#include "agent_rpc/client/ai_query_client.h"
#include "agent_service.grpc.pb.h"
#include <grpcpp/grpcpp.h>
#include <grpcpp/generic/generic_stub.h>
#include <memory>
#include <string>
#include <vector>
#include <map>
#include <mutex>
#include <atomic>
#include <thread>

namespace agent_rpc {
namespace client {

// RPC客户端类
class RpcClient {
public:
    RpcClient();
    ~RpcClient();
    
    // 初始化客户端
    bool initialize(const common::RpcConfig& config);
    
    // 连接到服务器
    bool connect(const std::string& server_address);
    bool connect(const std::vector<std::string>& server_addresses,
                 common::LoadBalanceStrategy strategy = common::LoadBalanceStrategy::ROUND_ROBIN);
    bool connectViaRegistry(const std::string& registry_address,
                            const std::string& service_name,
                            common::LoadBalanceStrategy strategy = common::LoadBalanceStrategy::ROUND_ROBIN);
    void setServiceRegistry(std::shared_ptr<registry::ServiceRegistry> service_registry);
    
    // 断开连接
    void disconnect();
    
    // 发送消息
    bool sendMessage(const std::string& message, 
                    const std::string& target_agent,
                    int timeout_seconds = 30);
    
    // 接收消息
    std::vector<std::string> receiveMessages(const std::string& agent_id,
                                            int max_messages = 10,
                                            int timeout_seconds = 30);
    
    // 广播消息
    int broadcastMessage(const std::string& message,
                        const std::vector<std::string>& target_agents = {},
                        bool exclude_sender = true);
    
    // 获取代理列表
    std::vector<common::ServiceEndpoint> getAgents(const std::string& filter = "",
                                                  int limit = 100,
                                                  int offset = 0);
    
    // 注册代理
    std::string registerAgent(const common::ServiceEndpoint& agent_info,
                             int heartbeat_interval = 30);
    
    // 注销代理
    bool unregisterAgent(const std::string& agent_id,
                        const std::string& reason = "");
    
    // 发送心跳
    bool sendHeartbeat(const std::string& agent_id,
                      const common::ServiceEndpoint& agent_info);
    
    // 监听消息（流式）
    void listenMessages(const std::string& agent_id,
                       common::MessageHandler handler,
                       int max_messages = 10,
                       int timeout_seconds = 30);
    
    // 设置消息处理器
    void setMessageHandler(common::MessageHandler handler);
    
    // 设置错误处理器
    void setErrorHandler(common::ErrorHandler handler);
    
    // 是否连接
    bool isConnected() const { return connected_; }
    
    // 获取连接地址
    std::string getServerAddress() const { return server_address_; }
    
    // ========================================================================
    // AI Query Methods (Requirements: 2.1)
    // ========================================================================
    
    /**
     * @brief Get the AI Query client
     * @return Pointer to AIQueryClient, or nullptr if not initialized
     */
    AIQueryClient* getAIQueryClient() { return ai_query_client_.get(); }
    
    /**
     * @brief Send a synchronous AI query
     * @param question The question to ask
     * @param context_id Optional context ID for multi-turn conversation
     * @param timeout_seconds Query timeout
     * @return AI query response
     */
    agent_communication::AIQueryResponse aiQuery(
        const std::string& question,
        const std::string& context_id = "",
        int timeout_seconds = 30);
    
    /**
     * @brief Send a streaming AI query
     * @param question The question to ask
     * @param callback Callback for stream events
     * @param context_id Optional context ID
     * @param timeout_seconds Query timeout
     * @return true if streaming completed successfully
     */
    bool aiQueryStream(
        const std::string& question,
        StreamEventCallback callback,
        const std::string& context_id = "",
        int timeout_seconds = 60);

private:
    // 内部方法
    void setupChannel();
    void setupSslCredentials();
    bool reconnect();
    bool connectToEndpoint(const common::ServiceEndpoint& endpoint);
    bool handleTransportFailure(const grpc::Status& status);
    common::ServiceEndpoint parseEndpoint(const std::string& server_address) const;
    void startHeartbeat();
    void stopHeartbeat();
    void heartbeatLoop();
    
    // 成员变量
    common::RpcConfig config_;
    std::string server_address_;
    std::atomic<bool> connected_{false};
    
    std::shared_ptr<grpc::Channel> channel_;
    std::unique_ptr<grpc::TemplatedGenericStub<grpc::ByteBuffer, grpc::ByteBuffer>> stub_;
    std::unique_ptr<agent_communication::AgentCommunicationService::Stub> agent_stub_;
    
    common::MessageHandler message_handler_;
    common::ErrorHandler error_handler_;
    
    // 心跳相关
    std::thread heartbeat_thread_;
    std::atomic<bool> heartbeat_running_{false};
    std::string current_agent_id_;
    common::ServiceEndpoint current_agent_info_;
    
    // 连接管理
    mutable std::mutex connection_mutex_;
    std::chrono::steady_clock::time_point last_connection_time_;
    int connection_retry_count_ = 0;
    static constexpr int MAX_RETRY_COUNT = 5;
    static constexpr int RETRY_DELAY_MS = 1000;
    
    // AI Query Client (Requirements: 2.1)
    std::unique_ptr<AIQueryClient> ai_query_client_;

    // Circuit breaker for RPC calls
    std::shared_ptr<common::CircuitBreaker> circuit_breaker_;
    std::unique_ptr<common::LoadBalancer> load_balancer_;
    std::vector<common::ServiceEndpoint> server_endpoints_;
    std::shared_ptr<registry::ServiceRegistry> service_registry_;
    std::string current_endpoint_id_;
    std::string discovered_service_name_;
    std::shared_ptr<std::atomic<bool>> registry_watch_active_;
};

} // namespace client
} // namespace agent_rpc
