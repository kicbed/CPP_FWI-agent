/**
 * @file mcp_agent_integration.cpp
 * @brief MCP Agent Integration 实现
 * 
 * Requirements: 12.1, 12.4, 12.6
 * Task 19.2: 实现 MCPAgentIntegration 类
 * Task 10: 集成 RAG-MCP
 */

#include "agent_rpc/mcp/mcp_agent_integration.h"
#include "agent_rpc/mcp/rag/tool_retriever.h"
#include "agent_rpc/common/logger.h"

#include <nlohmann/json.hpp>
#include <chrono>
#include <thread>
#include <cstdlib>
#include <sstream>

using json = nlohmann::json;

namespace agent_rpc {
namespace mcp {

MCPAgentIntegration::MCPAgentIntegration() = default;

MCPAgentIntegration::~MCPAgentIntegration() {
    shutdown();
}

bool MCPAgentIntegration::initialize(const MCPAgentConfig& config) {
    if (initialized_) {
        LOG_WARN("MCPAgentIntegration already initialized");
        return true;
    }
    
    config_ = config;
    
    // 如果 MCP 未启用，直接返回成功
    if (!config_.enable_mcp) {
        LOG_INFO("MCP is disabled, skipping initialization");
        initialized_ = true;
        return true;
    }
    
    // 检查 MCP Server 路径
    if (config_.mcp_server_path.empty()) {
        LOG_WARN("MCP Server path is empty, MCP will not be available");
        initialized_ = true;
        return true;
    }
    
    // 尝试连接 MCP Server
    if (!connectToMCPServer()) {
        LOG_WARN("Failed to connect to MCP Server, continuing in degraded mode");
        // 降级模式：初始化成功但 MCP 不可用
        initialized_ = true;
        return true;
    }
    
    // 更新工具缓存
    updateToolCache();
    
    // 初始化 RAG-MCP (如果启用)
    if (config_.rag_config.enabled) {
        initializeRAG();
    }
    
    initialized_ = true;
    LOG_INFO("MCPAgentIntegration initialized successfully with " + 
             std::to_string(tool_cache_.size()) + " tools available");
    return true;
}

void MCPAgentIntegration::shutdown() {
    if (!initialized_) {
        return;
    }
    
    // 关闭 RAG-MCP
    shutdownRAG();
    
    disconnectFromMCPServer();
    
    {
        std::lock_guard<std::mutex> lock(tool_cache_mutex_);
        tool_cache_.clear();
    }
    
    initialized_ = false;
    LOG_INFO("MCPAgentIntegration shutdown");
}

bool MCPAgentIntegration::isAvailable() const {
    return initialized_ && connected_ && mcp_client_ && mcp_client_->isConnected();
}

std::vector<ToolInfo> MCPAgentIntegration::getAvailableTools() const {
    std::lock_guard<std::mutex> lock(tool_cache_mutex_);
    return tool_cache_;
}

std::vector<std::string> MCPAgentIntegration::getToolNames() const {
    std::lock_guard<std::mutex> lock(tool_cache_mutex_);
    std::vector<std::string> names;
    names.reserve(tool_cache_.size());
    for (const auto& tool : tool_cache_) {
        names.push_back(tool.name);
    }
    return names;
}

bool MCPAgentIntegration::hasToolAvailable(const std::string& tool_name) const {
    std::lock_guard<std::mutex> lock(tool_cache_mutex_);
    for (const auto& tool : tool_cache_) {
        if (tool.name == tool_name) {
            return true;
        }
    }
    return false;
}

std::string MCPAgentIntegration::getToolDescription(const std::string& tool_name) const {
    std::lock_guard<std::mutex> lock(tool_cache_mutex_);
    for (const auto& tool : tool_cache_) {
        if (tool.name == tool_name) {
            return tool.description;
        }
    }
    return "";
}

std::string MCPAgentIntegration::getToolInputSchema(const std::string& tool_name) const {
    std::lock_guard<std::mutex> lock(tool_cache_mutex_);
    for (const auto& tool : tool_cache_) {
        if (tool.name == tool_name) {
            return tool.input_schema;
        }
    }
    return "";
}

ToolCallResult MCPAgentIntegration::callTool(const std::string& tool_name,
                                              const std::string& arguments) {
    ToolCallResult result;
    auto start_time = std::chrono::steady_clock::now();
    
    // 检查 MCP 是否可用
    if (!isAvailable()) {
        result.success = false;
        result.error = "MCP is not available";
        LOG_WARN("Tool call failed: MCP is not available");
        return result;
    }
    
    // 检查工具是否存在
    if (!hasToolAvailable(tool_name)) {
        result.success = false;
        result.error = "Tool not found: " + tool_name;
        LOG_WARN("Tool call failed: Tool not found - " + tool_name);
        return result;
    }
    
    // 调用工具
    LOG_INFO("Calling MCP tool: " + tool_name);
    
    int retry_count = 0;
    while (retry_count <= config_.max_retry_count) {
        try {
            MCPResponse response = tool_manager_->executeTool(tool_name, arguments);
            
            auto end_time = std::chrono::steady_clock::now();
            result.duration_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                end_time - start_time).count();
            
            if (response.is_error) {
                result.success = false;
                result.error = response.error;
                LOG_ERROR("Tool call failed: " + tool_name + " - " + response.error);
            } else {
                result.success = true;
                result.result = response.result;
                LOG_INFO("Tool call succeeded: " + tool_name + 
                        " in " + std::to_string(result.duration_ms) + "ms");
            }
            return result;
            
        } catch (const std::exception& e) {
            retry_count++;
            if (retry_count <= config_.max_retry_count) {
                LOG_WARN("Tool call failed, retrying (" + std::to_string(retry_count) + 
                        "/" + std::to_string(config_.max_retry_count) + "): " + e.what());
                std::this_thread::sleep_for(
                    std::chrono::milliseconds(config_.retry_delay_ms * retry_count));
            } else {
                result.success = false;
                result.error = "Tool call failed after retries: " + std::string(e.what());
                LOG_ERROR(result.error);
            }
        }
    }
    
    auto end_time = std::chrono::steady_clock::now();
    result.duration_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        end_time - start_time).count();
    
    return result;
}

void MCPAgentIntegration::callToolAsync(const std::string& tool_name,
                                         const std::string& arguments,
                                         std::function<void(const ToolCallResult&)> callback) {
    if (!callback) {
        LOG_ERROR("callToolAsync: callback is null");
        return;
    }
    
    // 在新线程中执行工具调用
    std::thread([this, tool_name, arguments, callback]() {
        ToolCallResult result = callTool(tool_name, arguments);
        callback(result);
    }).detach();
}

std::string MCPAgentIntegration::callToolSimple(const std::string& tool_name,
                                                 const std::string& arguments) {
    ToolCallResult result = callTool(tool_name, arguments);
    if (result.success) {
        return result.result;
    } else {
        return "[ERROR] " + result.error;
    }
}

std::string MCPAgentIntegration::getStatusDescription() const {
    if (!initialized_) {
        return "Not initialized";
    }
    if (!config_.enable_mcp) {
        return "MCP disabled";
    }
    if (!connected_) {
        return "Disconnected from MCP Server";
    }
    if (!mcp_client_ || !mcp_client_->isConnected()) {
        return "MCP Client not connected";
    }
    
    std::lock_guard<std::mutex> lock(tool_cache_mutex_);
    return "Connected, " + std::to_string(tool_cache_.size()) + " tools available";
}

bool MCPAgentIntegration::refreshTools() {
    if (!isAvailable()) {
        LOG_WARN("Cannot refresh tools: MCP is not available");
        return false;
    }
    
    updateToolCache();
    return true;
}

bool MCPAgentIntegration::connectToMCPServer() {
    try {
        // 创建 MCP Client
        mcp_client_ = std::make_shared<MCPClient>();
        
        // 连接到 MCP Server
        if (!mcp_client_->connect(config_.mcp_server_path, config_.mcp_args)) {
            LOG_ERROR("Failed to connect to MCP Server: " + config_.mcp_server_path);
            mcp_client_.reset();
            return false;
        }
        
        // 创建工具管理器
        tool_manager_ = std::make_shared<MCPToolManager>(mcp_client_);
        if (!tool_manager_->initialize()) {
            LOG_ERROR("Failed to initialize MCPToolManager");
            mcp_client_->disconnect();
            mcp_client_.reset();
            tool_manager_.reset();
            return false;
        }
        
        connected_ = true;
        LOG_INFO("Connected to MCP Server: " + config_.mcp_server_path);
        return true;
        
    } catch (const std::exception& e) {
        LOG_ERROR("Exception connecting to MCP Server: " + std::string(e.what()));
        mcp_client_.reset();
        tool_manager_.reset();
        return false;
    }
}

void MCPAgentIntegration::disconnectFromMCPServer() {
    if (tool_manager_) {
        tool_manager_->shutdown();
        tool_manager_.reset();
    }
    
    if (mcp_client_) {
        mcp_client_->disconnect();
        mcp_client_.reset();
    }
    
    connected_ = false;
}

void MCPAgentIntegration::updateToolCache() {
    if (!tool_manager_) {
        return;
    }
    
    std::lock_guard<std::mutex> lock(tool_cache_mutex_);
    tool_cache_.clear();
    
    auto mcp_tools = tool_manager_->getAvailableTools();
    for (const auto& mcp_tool : mcp_tools) {
        ToolInfo info;
        info.name = mcp_tool.name;
        info.description = mcp_tool.description;
        info.input_schema = mcp_tool.input_schema;
        tool_cache_.push_back(info);
    }
    
    LOG_INFO("Updated tool cache: " + std::to_string(tool_cache_.size()) + " tools");
    
    // 更新 RAG 索引
    if (rag_initialized_ && tool_retriever_) {
        tool_retriever_->indexTools(tool_cache_);
    }
}

// ============================================================================
// RAG-MCP 功能实现 (Task 10)
// ============================================================================

bool MCPAgentIntegration::initializeRAG() {
    if (rag_initialized_) {
        return true;
    }
    
    try {
        // 构建 RetrieverConfig
        rag::RetrieverConfig retriever_config;
        
        // Embedding 配置
        retriever_config.embedding_config.api_key = config_.rag_config.api_key;
        if (retriever_config.embedding_config.api_key.empty()) {
            retriever_config.embedding_config.loadApiKeyFromEnv();
        }
        retriever_config.embedding_config.model = config_.rag_config.model;
        
        // 检索配置
        retriever_config.top_k = config_.rag_config.top_k;
        retriever_config.similarity_threshold = config_.rag_config.similarity_threshold;
        retriever_config.index_path = config_.rag_config.index_path;
        
        // 缓存配置
        retriever_config.cache_config.enabled = config_.rag_config.enable_cache;
        retriever_config.cache_config.max_size = config_.rag_config.cache_max_size;
        retriever_config.cache_config.ttl_seconds = config_.rag_config.cache_ttl_seconds;
        
        // 创建检索器
        tool_retriever_ = std::make_unique<rag::ToolRetriever>(retriever_config);
        
        if (!tool_retriever_->initialize()) {
            LOG_ERROR("Failed to initialize ToolRetriever");
            tool_retriever_.reset();
            return false;
        }
        
        // 索引现有工具
        std::lock_guard<std::mutex> lock(tool_cache_mutex_);
        if (!tool_cache_.empty()) {
            tool_retriever_->indexTools(tool_cache_);
        }
        
        rag_initialized_ = true;
        LOG_INFO("RAG-MCP initialized successfully");
        return true;
        
    } catch (const std::exception& e) {
        LOG_ERROR("Failed to initialize RAG-MCP: " + std::string(e.what()));
        tool_retriever_.reset();
        return false;
    }
}

void MCPAgentIntegration::shutdownRAG() {
    if (!rag_initialized_) {
        return;
    }
    
    if (tool_retriever_) {
        tool_retriever_->shutdown();
        tool_retriever_.reset();
    }
    
    rag_initialized_ = false;
    LOG_INFO("RAG-MCP shutdown");
}

bool MCPAgentIntegration::isRAGEnabled() const {
    return rag_initialized_ && tool_retriever_ != nullptr;
}

std::vector<ToolInfo> MCPAgentIntegration::getRelevantTools(const std::string& query) const {
    return getRelevantTools(query, config_.rag_config.top_k);
}

std::vector<ToolInfo> MCPAgentIntegration::getRelevantTools(const std::string& query, int top_k) const {
    // 如果 RAG 未启用，返回所有工具
    if (!isRAGEnabled()) {
        return getAvailableTools();
    }
    
    try {
        auto retrieved = tool_retriever_->retrieve(query, top_k);
        
        std::vector<ToolInfo> result;
        result.reserve(retrieved.size());
        
        for (const auto& rt : retrieved) {
            ToolInfo info;
            info.name = rt.name;
            info.description = rt.description;
            info.input_schema = rt.input_schema;
            result.push_back(info);
        }
        
        return result;
        
    } catch (const std::exception& e) {
        LOG_ERROR("RAG retrieval failed, falling back to all tools: " + std::string(e.what()));
        return getAvailableTools();
    }
}

std::string MCPAgentIntegration::toFunctionCallingFormat(const std::vector<ToolInfo>& tools) {
    json functions = json::array();
    
    for (const auto& tool : tools) {
        json func = {
            {"name", tool.name},
            {"description", tool.description}
        };
        
        // 解析 input_schema
        if (!tool.input_schema.empty()) {
            try {
                func["parameters"] = json::parse(tool.input_schema);
            } catch (...) {
                func["parameters"] = json::object();
            }
        } else {
            func["parameters"] = json::object();
        }
        
        functions.push_back(func);
    }
    
    return functions.dump(2);
}

std::string MCPAgentIntegration::getRelevantToolsAsJson(const std::string& query) const {
    auto tools = getRelevantTools(query);
    return toFunctionCallingFormat(tools);
}


// ============================================================================
// 配置解析辅助函数
// ============================================================================

MCPAgentConfig parseMCPConfigFromArgs(int argc, char* argv[]) {
    MCPAgentConfig config;
    
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        
        if (arg == "--mcp-server" && i + 1 < argc) {
            config.mcp_server_path = argv[++i];
            config.enable_mcp = true;
        } else if (arg == "--mcp-args" && i + 1 < argc) {
            std::string args_str = argv[++i];
            // 解析逗号分隔的参数
            std::stringstream ss(args_str);
            std::string item;
            while (std::getline(ss, item, ',')) {
                if (!item.empty()) {
                    config.mcp_args.push_back(item);
                }
            }
        } else if (arg == "--enable-mcp") {
            config.enable_mcp = true;
        } else if (arg == "--mcp-timeout" && i + 1 < argc) {
            config.tool_call_timeout_ms = std::stoi(argv[++i]);
        }
        // RAG-MCP 参数
        else if (arg == "--enable-rag") {
            config.rag_config.enabled = true;
        } else if (arg == "--rag-api-key" && i + 1 < argc) {
            config.rag_config.api_key = argv[++i];
            config.rag_config.enabled = true;
        } else if (arg == "--rag-model" && i + 1 < argc) {
            config.rag_config.model = argv[++i];
        } else if (arg == "--rag-top-k" && i + 1 < argc) {
            config.rag_config.top_k = std::stoi(argv[++i]);
        } else if (arg == "--rag-threshold" && i + 1 < argc) {
            config.rag_config.similarity_threshold = std::stof(argv[++i]);
        } else if (arg == "--rag-index-path" && i + 1 < argc) {
            config.rag_config.index_path = argv[++i];
        }
    }
    
    // 如果启用了 RAG 但没有 API Key，尝试从环境变量获取
    if (config.rag_config.enabled && config.rag_config.api_key.empty()) {
        const char* api_key = std::getenv("DASHSCOPE_API_KEY");
        if (api_key && api_key[0] != '\0') {
            config.rag_config.api_key = api_key;
        }
    }
    
    return config;
}

MCPAgentConfig parseMCPConfigFromEnv() {
    MCPAgentConfig config;
    
    // MCP_SERVER_PATH
    const char* server_path = std::getenv("MCP_SERVER_PATH");
    if (server_path && server_path[0] != '\0') {
        config.mcp_server_path = server_path;
        config.enable_mcp = true;
    }
    
    // MCP_SERVER_ARGS
    const char* server_args = std::getenv("MCP_SERVER_ARGS");
    if (server_args && server_args[0] != '\0') {
        std::stringstream ss(server_args);
        std::string item;
        while (std::getline(ss, item, ',')) {
            if (!item.empty()) {
                config.mcp_args.push_back(item);
            }
        }
    }
    
    // MCP_ENABLED
    const char* enabled = std::getenv("MCP_ENABLED");
    if (enabled) {
        std::string enabled_str = enabled;
        config.enable_mcp = (enabled_str == "true" || enabled_str == "1" || enabled_str == "yes");
    }
    
    // MCP_TIMEOUT_MS
    const char* timeout = std::getenv("MCP_TIMEOUT_MS");
    if (timeout && timeout[0] != '\0') {
        try {
            config.tool_call_timeout_ms = std::stoi(timeout);
        } catch (...) {
            // 忽略解析错误，使用默认值
        }
    }
    
    // RAG-MCP 环境变量
    const char* rag_enabled = std::getenv("ENABLE_RAG");
    if (rag_enabled) {
        std::string rag_str = rag_enabled;
        config.rag_config.enabled = (rag_str == "true" || rag_str == "1" || rag_str == "yes");
    }
    
    // DASHSCOPE_API_KEY (用于 RAG Embedding)
    const char* dashscope_key = std::getenv("DASHSCOPE_API_KEY");
    if (dashscope_key && dashscope_key[0] != '\0') {
        config.rag_config.api_key = dashscope_key;
        // 如果有 API Key，自动启用 RAG（除非显式禁用）
        if (!rag_enabled) {
            config.rag_config.enabled = true;
        }
    }
    
    // RAG_TOP_K
    const char* rag_top_k = std::getenv("RAG_TOP_K");
    if (rag_top_k && rag_top_k[0] != '\0') {
        try {
            config.rag_config.top_k = std::stoi(rag_top_k);
        } catch (...) {}
    }
    
    // RAG_THRESHOLD
    const char* rag_threshold = std::getenv("RAG_THRESHOLD");
    if (rag_threshold && rag_threshold[0] != '\0') {
        try {
            config.rag_config.similarity_threshold = std::stof(rag_threshold);
        } catch (...) {}
    }
    
    // RAG_MODEL
    const char* rag_model = std::getenv("RAG_MODEL");
    if (rag_model && rag_model[0] != '\0') {
        config.rag_config.model = rag_model;
    }
    
    return config;
}

} // namespace mcp
} // namespace agent_rpc
