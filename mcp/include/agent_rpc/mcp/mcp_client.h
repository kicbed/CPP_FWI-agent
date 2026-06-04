#pragma once

#include <string>
#include <memory>
#include <vector>
#include <map>
#include <functional>
#include <thread>
#include <mutex>
#include <condition_variable>
#include <queue>
#include <atomic>
#include "agent_rpc/common/types.h"
#include "agent_rpc/common/logger.h"

namespace agent_rpc {
namespace mcp {

// MCP 传输类型
enum class MCPTransportType {
    STDIO,      // 标准输入输出（本地进程）
    SSE         // Server-Sent Events（HTTP 远程）
};

// MCP工具定义
struct MCPTool {
    std::string name;
    std::string description;
    std::string input_schema;  // JSON schema
};

// MCP提示定义
struct MCPPrompt {
    std::string name;
    std::string description;
    std::string arguments;  // JSON arguments
};

// MCP资源定义
struct MCPResource {
    std::string name;
    std::string description;
    std::string uri;
    std::string mime_type;
};

// MCP请求/响应
struct MCPRequest {
    std::string method;
    std::string params;
    std::string id;
};

struct MCPResponse {
    std::string id;
    std::string result;
    std::string error;
    bool is_error = false;
};

// MCP 连接配置
struct MCPConnectionConfig {
    MCPTransportType transport = MCPTransportType::STDIO;
    
    // STDIO 模式配置
    std::string server_path;                    // MCP Server 可执行文件路径
    std::vector<std::string> server_args;       // 启动参数
    
    // SSE 模式配置
    std::string sse_url;                        // SSE 服务器 URL (如 http://localhost:8080/mcp)
    std::string api_key;                        // API Key（可选）
    int connect_timeout_ms = 5000;              // 连接超时
    int request_timeout_ms = 30000;             // 请求超时
    bool verify_ssl = true;                     // 是否验证 SSL 证书
};

// MCP客户端接口
class IMCPClient {
public:
    virtual ~IMCPClient() = default;
    
    // 连接管理
    virtual bool connect(const std::string& server_path, const std::vector<std::string>& args = {}) = 0;
    virtual bool connect(const MCPConnectionConfig& config) = 0;
    virtual void disconnect() = 0;
    virtual bool isConnected() const = 0;
    virtual MCPTransportType getTransportType() const = 0;
    
    // 工具相关
    virtual std::vector<MCPTool> listTools() = 0;
    virtual MCPResponse callTool(const std::string& tool_name, const std::string& arguments) = 0;
    
    // 提示相关
    virtual std::vector<MCPPrompt> listPrompts() = 0;
    virtual MCPResponse getPrompt(const std::string& prompt_name, const std::string& arguments) = 0;
    
    // 资源相关
    virtual std::vector<MCPResource> listResources() = 0;
    virtual MCPResponse readResource(const std::string& uri) = 0;
    
    // 通知回调
    virtual void setNotificationCallback(std::function<void(const std::string&, const std::string&)> callback) = 0;
};

// MCP客户端实现（支持 STDIO 和 SSE 两种传输方式）
class MCPClient : public IMCPClient {
public:
    MCPClient();
    ~MCPClient();
    
    // 连接管理
    bool connect(const std::string& server_path, const std::vector<std::string>& args = {}) override;
    bool connect(const MCPConnectionConfig& config) override;
    void disconnect() override;
    bool isConnected() const override;
    MCPTransportType getTransportType() const override;
    
    // 工具相关
    std::vector<MCPTool> listTools() override;
    MCPResponse callTool(const std::string& tool_name, const std::string& arguments) override;
    
    // 提示相关
    std::vector<MCPPrompt> listPrompts() override;
    MCPResponse getPrompt(const std::string& prompt_name, const std::string& arguments) override;
    
    // 资源相关
    std::vector<MCPResource> listResources() override;
    MCPResponse readResource(const std::string& uri) override;
    
    // 通知回调
    void setNotificationCallback(std::function<void(const std::string&, const std::string&)> callback) override;

private:
    // 内部通信（通用）
    bool sendRequest(const MCPRequest& request);
    MCPResponse receiveResponse();
    void processNotifications();
    
    // STDIO 模式
    bool startMCPServer();
    void stopMCPServer();
    bool sendRequestStdio(const MCPRequest& request);
    void processNotificationsStdio();
    
    // SSE 模式
    bool connectSSE();
    void disconnectSSE();
    bool sendRequestSSE(const MCPRequest& request);
    MCPResponse receiveResponseSSE();
    void processNotificationsSSE();
    static size_t sseWriteCallback(char* ptr, size_t size, size_t nmemb, void* userdata);
    static size_t sseHeaderCallback(char* buffer, size_t size, size_t nitems, void* userdata);
    
    // 消息处理
    std::string buildJSONRPCRequest(const MCPRequest& request);
    MCPResponse parseJSONRPCResponse(const std::string& response);
    
    // 配置
    MCPConnectionConfig config_;
    MCPTransportType transport_type_{MCPTransportType::STDIO};
    
    // 通用状态
    std::atomic<bool> connected_{false};
    std::atomic<bool> running_{false};
    
    // STDIO 模式相关
    std::string server_path_;
    std::vector<std::string> server_args_;
    int server_pid_{-1};
    int stdin_pipe_{-1};
    int stdout_pipe_{-1};
    
    // SSE 模式相关
    void* curl_handle_{nullptr};        // CURL handle
    void* curl_multi_{nullptr};         // CURL multi handle for SSE
    std::string sse_session_id_;        // SSE 会话 ID
    std::string sse_response_buffer_;   // SSE 响应缓冲
    std::thread sse_event_thread_;      // SSE 事件监听线程
    
    // 消息队列
    std::queue<MCPResponse> response_queue_;
    std::mutex queue_mutex_;
    std::condition_variable queue_cv_;
    
    // 通知回调
    std::function<void(const std::string&, const std::string&)> notification_callback_;
    std::thread notification_thread_;
    
    // 日志
    std::shared_ptr<common::Logger> logger_;
};

// MCP工具管理器
class MCPToolManager {
public:
    MCPToolManager(std::shared_ptr<IMCPClient> mcp_client);
    ~MCPToolManager();
    
    // 初始化
    bool initialize();
    void shutdown();
    
    // 工具管理
    std::vector<MCPTool> getAvailableTools() const;
    bool isToolAvailable(const std::string& tool_name) const;
    
    // 工具调用
    MCPResponse executeTool(const std::string& tool_name, const std::string& arguments);
    
    // 异步工具调用
    void executeToolAsync(const std::string& tool_name, 
                         const std::string& arguments,
                         std::function<void(const MCPResponse&)> callback);
    
    // 工具验证
    bool validateToolArguments(const std::string& tool_name, const std::string& arguments) const;

    // 内部方法（供 MCPServiceIntegrator 使用）
    void refreshTools();
    void processNotification(const std::string& plugin_name, const std::string& notification);

private:
    std::shared_ptr<IMCPClient> mcp_client_;
    std::vector<MCPTool> available_tools_;
    std::map<std::string, MCPTool> tool_map_;
    mutable std::mutex tools_mutex_;
    std::atomic<bool> initialized_{false};
};

// MCP服务集成器
class MCPServiceIntegrator {
public:
    MCPServiceIntegrator();
    ~MCPServiceIntegrator();
    
    // 初始化
    bool initialize(const std::string& mcp_server_path, 
                   const std::vector<std::string>& mcp_args = {});
    void shutdown();
    
    // 服务管理
    bool isServiceAvailable() const;
    std::vector<std::string> getAvailableServices() const;
    
    // 工具服务
    std::shared_ptr<MCPToolManager> getToolManager() const;
    
    // 配置管理
    void setMCPServerPath(const std::string& path);
    void setMCPServerArgs(const std::vector<std::string>& args);
    void setLogLevel(common::LogLevel level);

private:
    std::shared_ptr<MCPClient> mcp_client_;
    std::shared_ptr<MCPToolManager> tool_manager_;
    std::string mcp_server_path_;
    std::vector<std::string> mcp_args_;
    std::atomic<bool> initialized_{false};
    std::shared_ptr<common::Logger> logger_;
};

} // namespace mcp
} // namespace agent_rpc


