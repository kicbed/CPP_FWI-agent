#pragma once

#include "agent_rpc/common/types.h"
#include "agent_rpc/common/logger.h"
#include "agent_rpc/common/metrics.h"
#include "agent_rpc/a2a_adapter/a2a_config.h"
#include "agent_rpc/registry/service_registry.h"
#include <grpcpp/grpcpp.h>
#include <grpcpp/health_check_service_interface.h>
#include <grpcpp/ext/proto_server_reflection_plugin.h>
#include <memory>
#include <string>
#include <vector>
#include <map>
#include <mutex>
#include <atomic>
#include <thread>

namespace agent_rpc {
namespace server {

// 前向声明
class AgentCommunicationServiceImpl;
class HealthServiceImpl;
class AIQueryServiceImpl;

// RPC服务器类
class RpcServer {
public:
    RpcServer();
    ~RpcServer();
    
    // 初始化服务器
    bool initialize(const common::RpcConfig& config);
    
    // 启动服务器
    bool start();
    
    // 停止服务器
    void stop();
    
    // 等待服务器结束
    void wait();
    
    // 获取服务实现
    std::shared_ptr<AgentCommunicationServiceImpl> getService();
    
    // 获取健康检查服务
    std::shared_ptr<HealthServiceImpl> getHealthService();
    
    // 获取AI查询服务
    std::shared_ptr<AIQueryServiceImpl> getAIQueryService();
    
    // 设置A2A配置
    void setA2AConfig(const a2a_adapter::A2AConfig& config);
    
    // 是否运行中
    bool isRunning() const { return running_; }
    
    // 获取服务器地址
    std::string getAddress() const { return address_; }
    
    // MCP相关配置
    void setMCPServerPath(const std::string& path);
    void setMCPServerArgs(const std::vector<std::string>& args);

private:
    void setupServer();
    void setupSslCredentials();
    void initializeMCPClient();
    void initializeServiceRegistry();
    void unregisterService();
    
    common::RpcConfig config_;
    std::string address_;
    std::atomic<bool> running_{false};
    
    std::unique_ptr<grpc::Server> server_;
    std::thread server_thread_;  // 服务器运行线程
    std::shared_ptr<AgentCommunicationServiceImpl> service_impl_;
    std::shared_ptr<HealthServiceImpl> health_service_impl_;
    std::shared_ptr<AIQueryServiceImpl> ai_query_service_impl_;
    
    // A2A配置
    a2a_adapter::A2AConfig a2a_config_;
    
    // MCP配置 (预留接口，待实现MCP client)
    std::string mcp_server_path_;
    std::vector<std::string> mcp_server_args_;

    std::shared_ptr<registry::ServiceRegistry> service_registry_;
    std::string registered_service_id_;
    
    std::shared_ptr<grpc::ServerCredentials> server_credentials_;
    std::vector<std::unique_ptr<grpc::ServerBuilder>> builders_;
};

} // namespace server
} // namespace agent_rpc
