/**
 * @file mcp_agent_integration.h
 * @brief MCP Agent Integration - 简化 AI Agent 与 MCP 的集成
 * 
 * Requirements: 12.1, 12.2, 12.3
 * Task 19.1: 创建 MCPAgentIntegration 辅助类
 * Task 10: 集成 RAG-MCP
 */

#pragma once

#include "mcp_client.h"
#include "agent_rpc/common/logger.h"

#include <string>
#include <vector>
#include <memory>
#include <functional>
#include <atomic>
#include <mutex>

// 前向声明
namespace agent_rpc {
namespace mcp {
namespace rag {
    class ToolRetriever;
    struct RetrieverConfig;
    struct RetrievedTool;
}
}
}

namespace agent_rpc {
namespace mcp {

/**
 * @brief RAG-MCP 配置
 * Task 10.1: 扩展 MCPAgentConfig 添加 RAG 配置
 */
struct RAGConfig {
    bool enabled = false;                  ///< 是否启用 RAG-MCP
    std::string api_key;                   ///< DashScope API Key (可从环境变量读取)
    std::string model = "text-embedding-v2"; ///< Embedding 模型
    int top_k = 5;                         ///< 返回工具数量
    float similarity_threshold = 0.3f;     ///< 相似度阈值
    std::string index_path;                ///< 索引文件路径
    bool enable_cache = true;              ///< 是否启用缓存
    size_t cache_max_size = 1000;          ///< 缓存最大条目
    int cache_ttl_seconds = 3600;          ///< 缓存过期时间
};

/**
 * @brief MCP Agent 集成配置
 */
struct MCPAgentConfig {
    std::string mcp_server_path;           ///< MCP Server 可执行文件路径
    std::vector<std::string> mcp_args;     ///< MCP Server 启动参数
    bool enable_mcp = false;               ///< 是否启用 MCP
    int connection_timeout_ms = 5000;      ///< 连接超时 (毫秒)
    int tool_call_timeout_ms = 30000;      ///< 工具调用超时 (毫秒)
    int max_retry_count = 3;               ///< 最大重试次数
    int retry_delay_ms = 1000;             ///< 重试延迟 (毫秒)
    
    // RAG-MCP 配置
    RAGConfig rag_config;                  ///< RAG-MCP 配置
};

/**
 * @brief 工具调用结果
 */
struct ToolCallResult {
    bool success = false;                  ///< 是否成功
    std::string result;                    ///< 成功时的结果
    std::string error;                     ///< 失败时的错误信息
    int64_t duration_ms = 0;               ///< 调用耗时 (毫秒)
};

/**
 * @brief 工具信息
 */
struct ToolInfo {
    std::string name;                      ///< 工具名称
    std::string description;               ///< 工具描述
    std::string input_schema;              ///< 输入参数 JSON Schema
};

/**
 * @brief MCP Agent 集成辅助类
 * 
 * 封装 MCPClient 和 MCPToolManager，提供简化的 AI Agent 集成接口。
 * 支持：
 * - 自动连接和断开 MCP Server
 * - 工具发现和调用
 * - 错误处理和降级
 * - 异步工具调用
 */
class MCPAgentIntegration {
public:
    MCPAgentIntegration();
    ~MCPAgentIntegration();
    
    // 禁止拷贝
    MCPAgentIntegration(const MCPAgentIntegration&) = delete;
    MCPAgentIntegration& operator=(const MCPAgentIntegration&) = delete;
    
    // ========================================================================
    // 生命周期管理
    // ========================================================================
    
    /**
     * @brief 初始化 MCP 集成
     * @param config MCP 配置
     * @return true 如果初始化成功或 MCP 未启用
     * 
     * 如果 config.enable_mcp 为 false，则直接返回 true 但不连接 MCP Server。
     * 如果连接失败，会记录错误日志但仍返回 true（降级模式）。
     */
    bool initialize(const MCPAgentConfig& config);
    
    /**
     * @brief 关闭 MCP 集成
     * 
     * 断开与 MCP Server 的连接，释放资源。
     */
    void shutdown();
    
    /**
     * @brief 检查 MCP 是否已初始化
     */
    bool isInitialized() const { return initialized_; }
    
    /**
     * @brief 检查 MCP 是否可用（已连接且工具管理器就绪）
     */
    bool isAvailable() const;
    
    // ========================================================================
    // 工具管理
    // ========================================================================
    
    /**
     * @brief 获取可用工具列表
     * @return 工具信息列表
     */
    std::vector<ToolInfo> getAvailableTools() const;
    
    /**
     * @brief 获取可用工具名称列表
     * @return 工具名称列表
     */
    std::vector<std::string> getToolNames() const;
    
    /**
     * @brief 检查工具是否可用
     * @param tool_name 工具名称
     * @return true 如果工具可用
     */
    bool hasToolAvailable(const std::string& tool_name) const;
    
    /**
     * @brief 获取工具描述
     * @param tool_name 工具名称
     * @return 工具描述，如果工具不存在返回空字符串
     */
    std::string getToolDescription(const std::string& tool_name) const;
    
    /**
     * @brief 获取工具输入 Schema
     * @param tool_name 工具名称
     * @return JSON Schema 字符串，如果工具不存在返回空字符串
     */
    std::string getToolInputSchema(const std::string& tool_name) const;
    
    // ========================================================================
    // 工具调用
    // ========================================================================
    
    /**
     * @brief 同步调用 MCP 工具
     * @param tool_name 工具名称
     * @param arguments JSON 格式的参数
     * @return 工具调用结果
     * 
     * 如果 MCP 不可用，返回失败结果但不抛出异常。
     */
    ToolCallResult callTool(const std::string& tool_name, 
                            const std::string& arguments);
    
    /**
     * @brief 异步调用 MCP 工具
     * @param tool_name 工具名称
     * @param arguments JSON 格式的参数
     * @param callback 完成回调
     * 
     * 回调在工具调用完成后被调用，可能在不同线程中执行。
     */
    void callToolAsync(const std::string& tool_name,
                       const std::string& arguments,
                       std::function<void(const ToolCallResult&)> callback);
    
    /**
     * @brief 简化的工具调用（返回结果字符串）
     * @param tool_name 工具名称
     * @param arguments JSON 格式的参数
     * @return 成功时返回结果，失败时返回错误信息（带 [ERROR] 前缀）
     */
    std::string callToolSimple(const std::string& tool_name,
                               const std::string& arguments);
    
    // ========================================================================
    // 配置和状态
    // ========================================================================
    
    /**
     * @brief 获取当前配置
     */
    const MCPAgentConfig& getConfig() const { return config_; }
    
    /**
     * @brief 获取 MCP Server 路径
     */
    const std::string& getMCPServerPath() const { return config_.mcp_server_path; }
    
    /**
     * @brief 获取连接状态描述
     */
    std::string getStatusDescription() const;
    
    /**
     * @brief 刷新工具列表
     * @return true 如果刷新成功
     */
    bool refreshTools();
    
    // ========================================================================
    // RAG-MCP 功能 (Task 10)
    // ========================================================================
    
    /**
     * @brief 检查 RAG-MCP 是否启用
     */
    bool isRAGEnabled() const;
    
    /**
     * @brief 根据查询检索相关工具
     * @param query 用户查询
     * @return 相关工具列表（按相关性排序）
     * 
     * 如果 RAG 未启用，返回所有工具。
     */
    std::vector<ToolInfo> getRelevantTools(const std::string& query) const;
    
    /**
     * @brief 根据查询检索相关工具（自定义 top_k）
     */
    std::vector<ToolInfo> getRelevantTools(const std::string& query, int top_k) const;
    
    /**
     * @brief 获取工具的 LLM 函数调用格式
     * @param tools 工具列表
     * @return JSON 格式的函数定义
     */
    static std::string toFunctionCallingFormat(const std::vector<ToolInfo>& tools);
    
    /**
     * @brief 获取相关工具的 LLM 函数调用格式
     * @param query 用户查询
     * @return JSON 格式的函数定义
     */
    std::string getRelevantToolsAsJson(const std::string& query) const;

private:
    // 内部方法
    bool connectToMCPServer();
    void disconnectFromMCPServer();
    void updateToolCache();
    bool initializeRAG();
    void shutdownRAG();
    
    // 成员变量
    MCPAgentConfig config_;
    std::shared_ptr<MCPClient> mcp_client_;
    std::shared_ptr<MCPToolManager> tool_manager_;
    
    // 工具缓存
    std::vector<ToolInfo> tool_cache_;
    mutable std::mutex tool_cache_mutex_;
    
    // RAG-MCP
    std::unique_ptr<rag::ToolRetriever> tool_retriever_;
    
    // 状态
    std::atomic<bool> initialized_{false};
    std::atomic<bool> connected_{false};
    std::atomic<bool> rag_initialized_{false};
};

/**
 * @brief 从命令行参数解析 MCP 配置
 * @param argc 参数数量
 * @param argv 参数数组
 * @return MCP 配置
 * 
 * 支持的参数：
 * --mcp-server <path>    MCP Server 可执行文件路径
 * --mcp-args <args>      MCP Server 启动参数（逗号分隔）
 * --enable-mcp           启用 MCP
 * --mcp-timeout <ms>     工具调用超时（毫秒）
 */
MCPAgentConfig parseMCPConfigFromArgs(int argc, char* argv[]);

/**
 * @brief 从环境变量解析 MCP 配置
 * @return MCP 配置
 * 
 * 支持的环境变量：
 * MCP_SERVER_PATH        MCP Server 可执行文件路径
 * MCP_SERVER_ARGS        MCP Server 启动参数（逗号分隔）
 * MCP_ENABLED            是否启用 MCP (true/false)
 * MCP_TIMEOUT_MS         工具调用超时（毫秒）
 */
MCPAgentConfig parseMCPConfigFromEnv();

} // namespace mcp
} // namespace agent_rpc
