#include "agent_rpc/ai/ai_interface.h"
#include "agent_rpc/common/logger.h"
#include <json/json.h>

namespace agent_rpc {
namespace ai {

// AIInterface 实现
AIInterface::AIInterface() {
    logger_ = std::make_shared<common::Logger>();
}

AIInterface::~AIInterface() {
    shutdown();
}

bool AIInterface::initialize() {
    if (initialized_) {
        LOG_WARN("AI interface already initialized");
        return true;
    }
    
    // 创建MCP服务集成器
    mcp_integrator_ = std::make_shared<mcp::MCPServiceIntegrator>();
    
    // 初始化MCP服务集成器
    if (!mcp_integrator_->initialize("/root/mcp_server/build/mcp_server", 
                                    {"-n", "ai-server", "-l", "/tmp/mcp_logs", "-p", "/root/mcp_server/plugins"})) {
        LOG_ERROR("Failed to initialize MCP service integrator");
        return false;
    }
    
    // 获取工具管理器
    tool_manager_ = mcp_integrator_->getToolManager();
    if (!tool_manager_) {
        LOG_ERROR("Failed to get MCP tool manager");
        return false;
    }
    
    initialized_ = true;
    LOG_INFO("AI interface initialized successfully");
    return true;
}

void AIInterface::shutdown() {
    if (!initialized_) {
        return;
    }
    
    if (mcp_integrator_) {
        mcp_integrator_->shutdown();
        mcp_integrator_.reset();
    }
    
    tool_manager_.reset();
    initialized_ = false;
    
    LOG_INFO("AI interface shutdown");
}

bool AIInterface::isInitialized() const {
    return initialized_;
}

AIToolResponse AIInterface::callTool(const AIToolRequest& request) {
    AIToolResponse response;
    response.request_id = request.request_id;
    
    if (!initialized_) {
        response.is_error = true;
        response.error_message = "AI interface not initialized";
        return response;
    }
    
    if (!tool_manager_) {
        response.is_error = true;
        response.error_message = "Tool manager not available";
        return response;
    }
    
    // 验证工具是否可用
    if (!tool_manager_->isToolAvailable(request.tool_name)) {
        response.is_error = true;
        response.error_message = "Tool not available: " + request.tool_name;
        return response;
    }
    
    // 验证参数
    if (!tool_manager_->validateToolArguments(request.tool_name, request.arguments)) {
        response.is_error = true;
        response.error_message = "Invalid arguments for tool: " + request.tool_name;
        return response;
    }
    
    LOG_INFO("Calling AI tool: " + request.tool_name);
    
    // 调用MCP工具
    mcp::MCPResponse mcp_response = tool_manager_->executeTool(request.tool_name, request.arguments);
    
    // 转换响应
    response = convertMCPResponse(mcp_response, request.request_id);
    
    if (response.is_error) {
        LOG_ERROR("AI tool call failed: " + response.error_message);
    } else {
        LOG_INFO("AI tool call successful: " + request.tool_name);
    }
    
    return response;
}

void AIInterface::callToolAsync(const AIToolRequest& request, 
                               std::function<void(const AIToolResponse&)> callback) {
    if (!initialized_) {
        AIToolResponse error_response;
        error_response.request_id = request.request_id;
        error_response.is_error = true;
        error_response.error_message = "AI interface not initialized";
        callback(error_response);
        return;
    }
    
    if (!tool_manager_) {
        AIToolResponse error_response;
        error_response.request_id = request.request_id;
        error_response.is_error = true;
        error_response.error_message = "Tool manager not available";
        callback(error_response);
        return;
    }
    
    // 验证工具是否可用
    if (!tool_manager_->isToolAvailable(request.tool_name)) {
        AIToolResponse error_response;
        error_response.request_id = request.request_id;
        error_response.is_error = true;
        error_response.error_message = "Tool not available: " + request.tool_name;
        callback(error_response);
        return;
    }
    
    // 验证参数
    if (!tool_manager_->validateToolArguments(request.tool_name, request.arguments)) {
        AIToolResponse error_response;
        error_response.request_id = request.request_id;
        error_response.is_error = true;
        error_response.error_message = "Invalid arguments for tool: " + request.tool_name;
        callback(error_response);
        return;
    }
    
    LOG_INFO("Calling AI tool asynchronously: " + request.tool_name);
    
    // 异步调用MCP工具
    tool_manager_->executeToolAsync(request.tool_name, request.arguments, 
        [this, request, callback](const mcp::MCPResponse& mcp_response) {
            AIToolResponse response = convertMCPResponse(mcp_response, request.request_id);
            callback(response);
        });
}

std::vector<std::string> AIInterface::getAvailableTools() const {
    std::vector<std::string> tools;
    
    if (!initialized_ || !tool_manager_) {
        return tools;
    }
    
    auto mcp_tools = tool_manager_->getAvailableTools();
    for (const auto& tool : mcp_tools) {
        tools.push_back(tool.name);
    }
    
    return tools;
}

bool AIInterface::isToolAvailable(const std::string& tool_name) const {
    if (!initialized_ || !tool_manager_) {
        return false;
    }
    
    return tool_manager_->isToolAvailable(tool_name);
}

void AIInterface::setMCPServerPath(const std::string& path) {
    if (mcp_integrator_) {
        mcp_integrator_->setMCPServerPath(path);
    }
}

void AIInterface::setMCPServerArgs(const std::vector<std::string>& args) {
    if (mcp_integrator_) {
        mcp_integrator_->setMCPServerArgs(args);
    }
}

void AIInterface::setLogLevel(common::LogLevel level) {
    if (logger_) {
        logger_->setLogLevel(level);
    }
}

AIToolResponse AIInterface::convertMCPResponse(const mcp::MCPResponse& mcp_response, const std::string& request_id) {
    AIToolResponse response;
    response.request_id = request_id;
    response.is_error = mcp_response.is_error;
    
    if (mcp_response.is_error) {
        response.error_message = mcp_response.error;
    } else {
        // 解析MCP响应结果
        try {
            Json::Value root;
            Json::Reader reader;
            if (reader.parse(mcp_response.result, root)) {
                // 检查是否有错误标志
                if (root.isMember("isError") && root["isError"].asBool()) {
                    response.is_error = true;
                    if (root.isMember("content")) {
                        const Json::Value& content = root["content"];
                        if (content.isArray() && content.size() > 0) {
                            const Json::Value& first_content = content[0];
                            if (first_content.isMember("text")) {
                                response.error_message = first_content["text"].asString();
                            }
                        }
                    }
                } else {
                    // 提取内容
                    if (root.isMember("content")) {
                        const Json::Value& content = root["content"];
                        if (content.isArray()) {
                            std::string result_text;
                            for (const auto& item : content) {
                                if (item.isMember("text")) {
                                    if (!result_text.empty()) {
                                        result_text += "\n";
                                    }
                                    result_text += item["text"].asString();
                                }
                            }
                            response.result = result_text;
                        }
                    } else {
                        response.result = mcp_response.result;
                    }
                }
            } else {
                response.result = mcp_response.result;
            }
        } catch (const std::exception& e) {
            LOG_WARN("Failed to parse MCP response: " + std::string(e.what()));
            response.result = mcp_response.result;
        }
    }
    
    return response;
}

AIToolRequest AIInterface::convertToMCPRequest(const AIToolRequest& ai_request) {
    // 对于我们的实现，AI请求和MCP请求格式相同
    return ai_request;
}

// AIServiceProxy 实现
AIServiceProxy::AIServiceProxy() {
    logger_ = std::make_shared<common::Logger>();
}

AIServiceProxy::~AIServiceProxy() {
    shutdown();
}

bool AIServiceProxy::initialize(const std::string& mcp_server_path, 
                               const std::vector<std::string>& mcp_args) {
    if (initialized_) {
        LOG_WARN("AI service proxy already initialized");
        return true;
    }
    
    // 创建AI接口
    ai_interface_ = std::make_shared<AIInterface>();
    
    // 配置MCP服务器路径和参数
    ai_interface_->setMCPServerPath(mcp_server_path);
    ai_interface_->setMCPServerArgs(mcp_args);
    
    // 初始化AI接口
    if (!ai_interface_->initialize()) {
        LOG_ERROR("Failed to initialize AI interface");
        return false;
    }
    
    initialized_ = true;
    LOG_INFO("AI service proxy initialized successfully");
    return true;
}

void AIServiceProxy::shutdown() {
    if (!initialized_) {
        return;
    }
    
    if (ai_interface_) {
        ai_interface_->shutdown();
        ai_interface_.reset();
    }
    
    initialized_ = false;
    LOG_INFO("AI service proxy shutdown");
}

std::shared_ptr<IAIInterface> AIServiceProxy::getAIInterface() const {
    return ai_interface_;
}

bool AIServiceProxy::isServiceAvailable() const {
    return initialized_ && ai_interface_ && ai_interface_->isInitialized();
}

std::vector<std::string> AIServiceProxy::getAvailableServices() const {
    std::vector<std::string> services;
    
    if (!isServiceAvailable()) {
        return services;
    }
    
    auto tools = ai_interface_->getAvailableTools();
    for (const auto& tool : tools) {
        services.push_back("ai_tool:" + tool);
    }
    
    return services;
}

void AIServiceProxy::setMCPServerPath(const std::string& path) {
    if (ai_interface_) {
        ai_interface_->setMCPServerPath(path);
    }
}

void AIServiceProxy::setMCPServerArgs(const std::vector<std::string>& args) {
    if (ai_interface_) {
        ai_interface_->setMCPServerArgs(args);
    }
}

void AIServiceProxy::setLogLevel(common::LogLevel level) {
    if (logger_) {
        logger_->setLogLevel(level);
    }
}

} // namespace ai
} // namespace agent_rpc


