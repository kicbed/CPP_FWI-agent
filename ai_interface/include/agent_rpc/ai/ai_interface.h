#pragma once

#include <string>
#include <memory>
#include <vector>
#include <map>
#include <functional>
#include "agent_rpc/mcp/mcp_client.h"
#include "agent_rpc/common/types.h"

namespace agent_rpc {
namespace ai {

// AI工具请求
struct AIToolRequest {
    std::string tool_name;
    std::string arguments;
    std::string request_id;
    std::map<std::string, std::string> metadata;
};

// AI工具响应
struct AIToolResponse {
    std::string request_id;
    std::string result;
    bool is_error = false;
    std::string error_message;
    std::map<std::string, std::string> metadata;
};

// AI模型接口
class IAIInterface {
public:
    virtual ~IAIInterface() = default;
    
    // 工具调用
    virtual AIToolResponse callTool(const AIToolRequest& request) = 0;
    virtual void callToolAsync(const AIToolRequest& request, 
                              std::function<void(const AIToolResponse&)> callback) = 0;
    
    // 工具管理
    virtual std::vector<std::string> getAvailableTools() const = 0;
    virtual bool isToolAvailable(const std::string& tool_name) const = 0;
    
    // 服务管理
    virtual bool initialize() = 0;
    virtual void shutdown() = 0;
    virtual bool isInitialized() const = 0;
};

// AI模型接口实现
class AIInterface : public IAIInterface {
public:
    AIInterface();
    ~AIInterface();
    
    // 工具调用
    AIToolResponse callTool(const AIToolRequest& request) override;
    void callToolAsync(const AIToolRequest& request, 
                      std::function<void(const AIToolResponse&)> callback) override;
    
    // 工具管理
    std::vector<std::string> getAvailableTools() const override;
    bool isToolAvailable(const std::string& tool_name) const override;
    
    // 服务管理
    bool initialize() override;
    void shutdown() override;
    bool isInitialized() const override;
    
    // 配置
    void setMCPServerPath(const std::string& path);
    void setMCPServerArgs(const std::vector<std::string>& args);
    void setLogLevel(common::LogLevel level);

private:
    // 内部方法
    AIToolResponse convertMCPResponse(const mcp::MCPResponse& mcp_response, const std::string& request_id);
    AIToolRequest convertToMCPRequest(const AIToolRequest& ai_request);
    
    // 成员变量
    std::shared_ptr<mcp::MCPServiceIntegrator> mcp_integrator_;
    std::shared_ptr<mcp::MCPToolManager> tool_manager_;
    std::atomic<bool> initialized_{false};
    std::shared_ptr<common::Logger> logger_;
};

// AI服务代理 - 用于RPC服务端
class AIServiceProxy {
public:
    AIServiceProxy();
    ~AIServiceProxy();
    
    // 初始化
    bool initialize(const std::string& mcp_server_path, 
                   const std::vector<std::string>& mcp_args = {});
    void shutdown();
    
    // 工具服务
    std::shared_ptr<IAIInterface> getAIInterface() const;
    
    // 状态检查
    bool isServiceAvailable() const;
    std::vector<std::string> getAvailableServices() const;
    
    // 配置
    void setMCPServerPath(const std::string& path);
    void setMCPServerArgs(const std::vector<std::string>& args);
    void setLogLevel(common::LogLevel level);

private:
    std::shared_ptr<AIInterface> ai_interface_;
    std::atomic<bool> initialized_{false};
    std::shared_ptr<common::Logger> logger_;
};

} // namespace ai
} // namespace agent_rpc


