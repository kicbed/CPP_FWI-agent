#include "agent_rpc/mcp/mcp_client.h"
#include "agent_rpc/common/logger.h"
#include <unistd.h>
#include <sys/wait.h>
#include <fcntl.h>
#include <sstream>
#include <fstream>
#include <json/json.h>
#include <ctime>
#include <curl/curl.h>

namespace agent_rpc {
namespace mcp {

// MCPClient 实现
MCPClient::MCPClient() {
    // Logger 使用全局宏，不需要实例化
}

MCPClient::~MCPClient() {
    disconnect();
}

bool MCPClient::connect(const std::string& server_path, const std::vector<std::string>& args) {
    // 使用 STDIO 模式连接
    MCPConnectionConfig config;
    config.transport = MCPTransportType::STDIO;
    config.server_path = server_path;
    config.server_args = args;
    return connect(config);
}

bool MCPClient::connect(const MCPConnectionConfig& config) {
    if (connected_) {
        LOG_WARN("MCP client already connected");
        return true;
    }
    
    config_ = config;
    transport_type_ = config.transport;
    
    bool success = false;
    
    if (transport_type_ == MCPTransportType::STDIO) {
        server_path_ = config.server_path;
        server_args_ = config.server_args;
        
        if (!startMCPServer()) {
            LOG_ERROR("Failed to start MCP server");
            return false;
        }
        
        connected_ = true;
        running_ = true;
        
        // 启动通知处理线程
        notification_thread_ = std::thread([this]() {
            processNotificationsStdio();
        });
        
        LOG_INFO("MCP client connected to server (STDIO): " + server_path_);
        success = true;
    } else if (transport_type_ == MCPTransportType::SSE) {
        if (!connectSSE()) {
            LOG_ERROR("Failed to connect to MCP server via SSE");
            return false;
        }
        
        connected_ = true;
        running_ = true;
        
        LOG_INFO("MCP client connected to server (SSE): " + config.sse_url);
        success = true;
    }
    
    return success;
}

MCPTransportType MCPClient::getTransportType() const {
    return transport_type_;
}

void MCPClient::disconnect() {
    if (!connected_) {
        return;
    }
    
    running_ = false;
    connected_ = false;
    
    // 停止通知线程
    if (notification_thread_.joinable()) {
        notification_thread_.join();
    }
    
    if (transport_type_ == MCPTransportType::STDIO) {
        stopMCPServer();
    } else if (transport_type_ == MCPTransportType::SSE) {
        disconnectSSE();
    }
    
    LOG_INFO("MCP client disconnected");
}

bool MCPClient::isConnected() const {
    return connected_;
}

std::vector<MCPTool> MCPClient::listTools() {
    std::vector<MCPTool> tools;
    
    if (!connected_) {
        LOG_ERROR("MCP client not connected");
        return tools;
    }
    
    MCPRequest request;
    request.method = "tools/list";
    request.id = "list_tools_" + std::to_string(std::time(nullptr));
    
    if (!sendRequest(request)) {
        LOG_ERROR("Failed to send tools/list request");
        return tools;
    }
    
    MCPResponse response = receiveResponse();
    if (response.is_error) {
        LOG_ERROR("Error listing tools: " + response.error);
        return tools;
    }
    
    // 解析响应
    try {
        Json::Value root;
        Json::Reader reader;
        if (reader.parse(response.result, root)) {
            const Json::Value& tools_array = root["tools"];
            for (const auto& tool : tools_array) {
                MCPTool mcp_tool;
                mcp_tool.name = tool["name"].asString();
                mcp_tool.description = tool["description"].asString();
                mcp_tool.input_schema = tool["inputSchema"].toStyledString();
                tools.push_back(mcp_tool);
            }
        }
    } catch (const std::exception& e) {
        LOG_ERROR("Failed to parse tools list: " + std::string(e.what()));
    }
    
    return tools;
}

MCPResponse MCPClient::callTool(const std::string& tool_name, const std::string& arguments) {
    MCPResponse response;
    response.is_error = true;
    response.error = "Not connected";
    
    if (!connected_) {
        LOG_ERROR("MCP client not connected");
        return response;
    }
    
    MCPRequest request;
    request.method = "tools/call";
    request.id = "call_tool_" + std::to_string(std::time(nullptr));
    
    // 构建参数
    Json::Value params;
    params["name"] = tool_name;
    
    Json::Value args_json;
    Json::Reader reader;
    if (reader.parse(arguments, args_json)) {
        params["arguments"] = args_json;
    } else {
        params["arguments"] = Json::Value(Json::objectValue);
    }
    
    // 只设置 params 部分，不要包含 method 和 id
    Json::StreamWriterBuilder builder;
    builder["indentation"] = "";
    request.params = Json::writeString(builder, params);
    
    if (!sendRequest(request)) {
        LOG_ERROR("Failed to send tools/call request");
        response.error = "Failed to send request";
        return response;
    }
    
    response = receiveResponse();
    if (response.is_error) {
        LOG_ERROR("Error calling tool " + tool_name + ": " + response.error);
    } else {
        LOG_INFO("Successfully called tool: " + tool_name);
    }
    
    return response;
}

std::vector<MCPPrompt> MCPClient::listPrompts() {
    std::vector<MCPPrompt> prompts;
    
    if (!connected_) {
        LOG_ERROR("MCP client not connected");
        return prompts;
    }
    
    MCPRequest request;
    request.method = "prompts/list";
    request.id = "list_prompts_" + std::to_string(std::time(nullptr));
    
    if (!sendRequest(request)) {
        LOG_ERROR("Failed to send prompts/list request");
        return prompts;
    }
    
    MCPResponse response = receiveResponse();
    if (response.is_error) {
        LOG_ERROR("Error listing prompts: " + response.error);
        return prompts;
    }
    
    // 解析响应
    try {
        Json::Value root;
        Json::Reader reader;
        if (reader.parse(response.result, root)) {
            const Json::Value& prompts_array = root["prompts"];
            for (const auto& prompt : prompts_array) {
                MCPPrompt mcp_prompt;
                mcp_prompt.name = prompt["name"].asString();
                mcp_prompt.description = prompt["description"].asString();
                mcp_prompt.arguments = prompt["arguments"].toStyledString();
                prompts.push_back(mcp_prompt);
            }
        }
    } catch (const std::exception& e) {
        LOG_ERROR("Failed to parse prompts list: " + std::string(e.what()));
    }
    
    return prompts;
}

MCPResponse MCPClient::getPrompt(const std::string& prompt_name, const std::string& arguments) {
    MCPResponse response;
    response.is_error = true;
    response.error = "Not connected";
    
    if (!connected_) {
        LOG_ERROR("MCP client not connected");
        return response;
    }
    
    MCPRequest request;
    request.method = "prompts/get";
    request.id = "get_prompt_" + std::to_string(std::time(nullptr));
    
    // 构建参数
    Json::Value params;
    params["name"] = prompt_name;
    
    Json::Value args_json;
    Json::Reader reader;
    if (reader.parse(arguments, args_json)) {
        params["arguments"] = args_json;
    } else {
        params["arguments"] = Json::Value();
    }
    
    Json::Value request_obj;
    request_obj["method"] = request.method;
    request_obj["params"] = params;
    request_obj["id"] = request.id;
    
    request.params = request_obj.toStyledString();
    
    if (!sendRequest(request)) {
        LOG_ERROR("Failed to send prompts/get request");
        response.error = "Failed to send request";
        return response;
    }
    
    response = receiveResponse();
    if (response.is_error) {
        LOG_ERROR("Error getting prompt " + prompt_name + ": " + response.error);
    } else {
        LOG_INFO("Successfully got prompt: " + prompt_name);
    }
    
    return response;
}

std::vector<MCPResource> MCPClient::listResources() {
    std::vector<MCPResource> resources;
    
    if (!connected_) {
        LOG_ERROR("MCP client not connected");
        return resources;
    }
    
    MCPRequest request;
    request.method = "resources/list";
    request.id = "list_resources_" + std::to_string(std::time(nullptr));
    
    if (!sendRequest(request)) {
        LOG_ERROR("Failed to send resources/list request");
        return resources;
    }
    
    MCPResponse response = receiveResponse();
    if (response.is_error) {
        LOG_ERROR("Error listing resources: " + response.error);
        return resources;
    }
    
    // 解析响应
    try {
        Json::Value root;
        Json::Reader reader;
        if (reader.parse(response.result, root)) {
            const Json::Value& resources_array = root["resources"];
            for (const auto& resource : resources_array) {
                MCPResource mcp_resource;
                mcp_resource.name = resource["name"].asString();
                mcp_resource.description = resource["description"].asString();
                mcp_resource.uri = resource["uri"].asString();
                mcp_resource.mime_type = resource["mimeType"].asString();
                resources.push_back(mcp_resource);
            }
        }
    } catch (const std::exception& e) {
        LOG_ERROR("Failed to parse resources list: " + std::string(e.what()));
    }
    
    return resources;
}

MCPResponse MCPClient::readResource(const std::string& uri) {
    MCPResponse response;
    response.is_error = true;
    response.error = "Not connected";
    
    if (!connected_) {
        LOG_ERROR("MCP client not connected");
        return response;
    }
    
    MCPRequest request;
    request.method = "resources/read";
    request.id = "read_resource_" + std::to_string(std::time(nullptr));
    
    // 构建参数
    Json::Value params;
    params["uri"] = uri;
    
    Json::Value request_obj;
    request_obj["method"] = request.method;
    request_obj["params"] = params;
    request_obj["id"] = request.id;
    
    request.params = request_obj.toStyledString();
    
    if (!sendRequest(request)) {
        LOG_ERROR("Failed to send resources/read request");
        response.error = "Failed to send request";
        return response;
    }
    
    response = receiveResponse();
    if (response.is_error) {
        LOG_ERROR("Error reading resource " + uri + ": " + response.error);
    } else {
        LOG_INFO("Successfully read resource: " + uri);
    }
    
    return response;
}

void MCPClient::setNotificationCallback(std::function<void(const std::string&, const std::string&)> callback) {
    notification_callback_ = callback;
}

bool MCPClient::sendRequest(const MCPRequest& request) {
    if (transport_type_ == MCPTransportType::STDIO) {
        return sendRequestStdio(request);
    } else if (transport_type_ == MCPTransportType::SSE) {
        return sendRequestSSE(request);
    }
    return false;
}

bool MCPClient::sendRequestStdio(const MCPRequest& request) {
    if (stdin_pipe_ == -1) {
        LOG_ERROR("MCP server stdin pipe not available");
        return false;
    }
    
    std::string json_request = buildJSONRPCRequest(request);
    json_request += "\n";  // MCP协议使用换行符分隔消息
    
    ssize_t written = write(stdin_pipe_, json_request.c_str(), json_request.length());
    if (written != static_cast<ssize_t>(json_request.length())) {
        LOG_ERROR("Failed to write request to MCP server");
        return false;
    }
    
    return true;
}

MCPResponse MCPClient::receiveResponse() {
    std::unique_lock<std::mutex> lock(queue_mutex_);
    
    // 等待响应，最多等待30秒
    if (queue_cv_.wait_for(lock, std::chrono::seconds(30), [this] { return !response_queue_.empty(); })) {
        MCPResponse response = response_queue_.front();
        response_queue_.pop();
        return response;
    }
    
    MCPResponse timeout_response;
    timeout_response.is_error = true;
    timeout_response.error = "Request timeout";
    return timeout_response;
}

void MCPClient::processNotifications() {
    if (transport_type_ == MCPTransportType::STDIO) {
        processNotificationsStdio();
    } else if (transport_type_ == MCPTransportType::SSE) {
        processNotificationsSSE();
    }
}

void MCPClient::processNotificationsStdio() {
    if (stdout_pipe_ == -1) {
        LOG_ERROR("MCP server stdout pipe not available");
        return;
    }
    
    char buffer[4096];
    std::string line;
    
    while (running_) {
        ssize_t bytes_read = read(stdout_pipe_, buffer, sizeof(buffer) - 1);
        if (bytes_read > 0) {
            buffer[bytes_read] = '\0';
            line += buffer;
            
            // 处理完整的行
            size_t pos = 0;
            while ((pos = line.find('\n')) != std::string::npos) {
                std::string message = line.substr(0, pos);
                line.erase(0, pos + 1);
                
                if (!message.empty()) {
                    MCPResponse response = parseJSONRPCResponse(message);
                    
                    // 检查是否是通知
                    if (response.id.empty() && !response.error.empty()) {
                        // 这是一个通知
                        if (notification_callback_) {
                            // 解析通知内容
                            try {
                                Json::Value root;
                                Json::Reader reader;
                                if (reader.parse(message, root)) {
                                    std::string method = root["method"].asString();
                                    if (method == "notifications/message") {
                                        const Json::Value& params = root["params"];
                                        std::string plugin_name = params["pluginName"].asString();
                                        std::string notification = params["notification"].asString();
                                        notification_callback_(plugin_name, notification);
                                    }
                                }
                            } catch (const std::exception& e) {
                                LOG_WARN("Failed to parse notification: " + std::string(e.what()));
                            }
                        }
                    } else {
                        // 这是一个响应
                        std::lock_guard<std::mutex> lock(queue_mutex_);
                        response_queue_.push(response);
                        queue_cv_.notify_one();
                    }
                }
            }
        } else if (bytes_read == 0) {
            // 管道关闭
            break;
        } else {
            // 读取错误
            if (errno != EAGAIN && errno != EWOULDBLOCK) {
                LOG_ERROR("Error reading from MCP server stdout");
                break;
            }
        }
    }
}

bool MCPClient::startMCPServer() {
    // 创建管道
    int stdin_pipe_fd[2];
    int stdout_pipe_fd[2];
    
    if (pipe(stdin_pipe_fd) == -1 || pipe(stdout_pipe_fd) == -1) {
        LOG_ERROR("Failed to create pipes for MCP server");
        return false;
    }
    
    // 创建子进程
    server_pid_ = fork();
    if (server_pid_ == -1) {
        LOG_ERROR("Failed to fork process for MCP server");
        return false;
    }
    
    if (server_pid_ == 0) {
        // 子进程：运行MCP服务器
        close(stdin_pipe_fd[1]);  // 关闭写端
        close(stdout_pipe_fd[0]); // 关闭读端
        
        // 重定向stdin和stdout
        dup2(stdin_pipe_fd[0], STDIN_FILENO);
        dup2(stdout_pipe_fd[1], STDOUT_FILENO);
        
        // 准备参数
        std::vector<char*> argv;
        argv.push_back(const_cast<char*>(server_path_.c_str()));
        
        for (const auto& arg : server_args_) {
            argv.push_back(const_cast<char*>(arg.c_str()));
        }
        argv.push_back(nullptr);
        
        // 执行MCP服务器
        execv(server_path_.c_str(), argv.data());
        
        // 如果execv失败
        LOG_ERROR("Failed to execute MCP server: " + server_path_);
        exit(1);
    } else {
        // 父进程
        close(stdin_pipe_fd[0]);  // 关闭读端
        close(stdout_pipe_fd[1]); // 关闭写端
        
        stdin_pipe_ = stdin_pipe_fd[1];
        stdout_pipe_ = stdout_pipe_fd[0];
        
        // 设置非阻塞模式
        fcntl(stdout_pipe_, F_SETFL, O_NONBLOCK);
        
        // 等待服务器启动
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
        
        return true;
    }
}

void MCPClient::stopMCPServer() {
    if (server_pid_ > 0) {
        kill(server_pid_, SIGTERM);
        waitpid(server_pid_, nullptr, 0);
        server_pid_ = -1;
    }
    
    if (stdin_pipe_ != -1) {
        close(stdin_pipe_);
        stdin_pipe_ = -1;
    }
    
    if (stdout_pipe_ != -1) {
        close(stdout_pipe_);
        stdout_pipe_ = -1;
    }
}

std::string MCPClient::buildJSONRPCRequest(const MCPRequest& request) {
    Json::Value root;
    root["jsonrpc"] = "2.0";
    root["method"] = request.method;
    root["id"] = request.id;
    
    if (!request.params.empty()) {
        Json::Value params;
        Json::Reader reader;
        if (reader.parse(request.params, params)) {
            root["params"] = params;
        }
    }
    
    Json::StreamWriterBuilder builder;
    builder["indentation"] = "";
    return Json::writeString(builder, root);
}

MCPResponse MCPClient::parseJSONRPCResponse(const std::string& response) {
    MCPResponse mcp_response;
    
    try {
        Json::Value root;
        Json::Reader reader;
        if (reader.parse(response, root)) {
            mcp_response.id = root["id"].asString();
            
            if (root.isMember("error")) {
                mcp_response.is_error = true;
                mcp_response.error = root["error"]["message"].asString();
            } else {
                mcp_response.is_error = false;
                mcp_response.result = root["result"].toStyledString();
            }
        }
    } catch (const std::exception& e) {
        mcp_response.is_error = true;
        mcp_response.error = "Failed to parse response: " + std::string(e.what());
    }
    
    return mcp_response;
}

// ============================================================================
// SSE (Server-Sent Events) 传输实现
// ============================================================================

bool MCPClient::connectSSE() {
    // 初始化 CURL
    curl_global_init(CURL_GLOBAL_ALL);
    curl_handle_ = curl_easy_init();
    
    if (!curl_handle_) {
        LOG_ERROR("Failed to initialize CURL for SSE");
        return false;
    }
    
    // 发送初始化请求获取 session ID
    CURL* curl = static_cast<CURL*>(curl_handle_);
    
    // 构建初始化 URL
    std::string init_url = config_.sse_url;
    if (init_url.back() != '/') {
        init_url += "/";
    }
    init_url += "sse";
    
    curl_easy_setopt(curl, CURLOPT_URL, init_url.c_str());
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, sseWriteCallback);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, this);
    curl_easy_setopt(curl, CURLOPT_HEADERFUNCTION, sseHeaderCallback);
    curl_easy_setopt(curl, CURLOPT_HEADERDATA, this);
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, config_.connect_timeout_ms / 1000);
    curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT, config_.connect_timeout_ms / 1000);
    
    // 设置 API Key（如果有）
    struct curl_slist* headers = nullptr;
    headers = curl_slist_append(headers, "Accept: text/event-stream");
    headers = curl_slist_append(headers, "Cache-Control: no-cache");
    if (!config_.api_key.empty()) {
        std::string auth_header = "Authorization: Bearer " + config_.api_key;
        headers = curl_slist_append(headers, auth_header.c_str());
    }
    curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
    
    // SSL 验证
    if (!config_.verify_ssl) {
        curl_easy_setopt(curl, CURLOPT_SSL_VERIFYPEER, 0L);
        curl_easy_setopt(curl, CURLOPT_SSL_VERIFYHOST, 0L);
    }
    
    // 执行请求获取 session
    sse_response_buffer_.clear();
    CURLcode res = curl_easy_perform(curl);
    
    curl_slist_free_all(headers);
    
    if (res != CURLE_OK) {
        LOG_ERROR("SSE connection failed: " + std::string(curl_easy_strerror(res)));
        curl_easy_cleanup(curl);
        curl_handle_ = nullptr;
        return false;
    }
    
    // 解析 session ID
    if (sse_session_id_.empty()) {
        // 尝试从响应中解析
        try {
            Json::Value root;
            Json::Reader reader;
            if (reader.parse(sse_response_buffer_, root)) {
                if (root.isMember("sessionId")) {
                    sse_session_id_ = root["sessionId"].asString();
                }
            }
        } catch (...) {
            // 忽略解析错误
        }
    }
    
    // 启动 SSE 事件监听线程
    sse_event_thread_ = std::thread([this]() {
        processNotificationsSSE();
    });
    
    LOG_INFO("SSE connection established, session: " + sse_session_id_);
    return true;
}

void MCPClient::disconnectSSE() {
    running_ = false;
    
    if (sse_event_thread_.joinable()) {
        sse_event_thread_.join();
    }
    
    if (curl_handle_) {
        curl_easy_cleanup(static_cast<CURL*>(curl_handle_));
        curl_handle_ = nullptr;
    }
    
    curl_global_cleanup();
    sse_session_id_.clear();
    
    LOG_INFO("SSE connection closed");
}

bool MCPClient::sendRequestSSE(const MCPRequest& request) {
    if (!curl_handle_) {
        LOG_ERROR("SSE not connected");
        return false;
    }
    
    // 创建新的 CURL handle 用于 POST 请求
    CURL* curl = curl_easy_init();
    if (!curl) {
        LOG_ERROR("Failed to create CURL handle for SSE request");
        return false;
    }
    
    // 构建请求 URL
    std::string post_url = config_.sse_url;
    if (post_url.back() != '/') {
        post_url += "/";
    }
    post_url += "message";
    
    // 如果有 session ID，添加到 URL
    if (!sse_session_id_.empty()) {
        post_url += "?sessionId=" + sse_session_id_;
    }
    
    // 构建 JSON-RPC 请求
    std::string json_request = buildJSONRPCRequest(request);
    
    curl_easy_setopt(curl, CURLOPT_URL, post_url.c_str());
    curl_easy_setopt(curl, CURLOPT_POST, 1L);
    curl_easy_setopt(curl, CURLOPT_POSTFIELDS, json_request.c_str());
    curl_easy_setopt(curl, CURLOPT_POSTFIELDSIZE, json_request.length());
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, config_.request_timeout_ms / 1000);
    
    // 设置响应回调
    std::string response_data;
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, 
        +[](char* ptr, size_t size, size_t nmemb, void* userdata) -> size_t {
            std::string* data = static_cast<std::string*>(userdata);
            data->append(ptr, size * nmemb);
            return size * nmemb;
        });
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response_data);
    
    // 设置请求头
    struct curl_slist* headers = nullptr;
    headers = curl_slist_append(headers, "Content-Type: application/json");
    if (!config_.api_key.empty()) {
        std::string auth_header = "Authorization: Bearer " + config_.api_key;
        headers = curl_slist_append(headers, auth_header.c_str());
    }
    curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
    
    // SSL 验证
    if (!config_.verify_ssl) {
        curl_easy_setopt(curl, CURLOPT_SSL_VERIFYPEER, 0L);
        curl_easy_setopt(curl, CURLOPT_SSL_VERIFYHOST, 0L);
    }
    
    // 执行请求
    CURLcode res = curl_easy_perform(curl);
    
    curl_slist_free_all(headers);
    curl_easy_cleanup(curl);
    
    if (res != CURLE_OK) {
        LOG_ERROR("SSE request failed: " + std::string(curl_easy_strerror(res)));
        return false;
    }
    
    // 解析响应并放入队列
    if (!response_data.empty()) {
        MCPResponse response = parseJSONRPCResponse(response_data);
        std::lock_guard<std::mutex> lock(queue_mutex_);
        response_queue_.push(response);
        queue_cv_.notify_one();
    }
    
    return true;
}

MCPResponse MCPClient::receiveResponseSSE() {
    std::unique_lock<std::mutex> lock(queue_mutex_);
    
    // 等待响应
    int timeout_ms = config_.request_timeout_ms > 0 ? config_.request_timeout_ms : 30000;
    if (queue_cv_.wait_for(lock, std::chrono::milliseconds(timeout_ms), 
                          [this] { return !response_queue_.empty(); })) {
        MCPResponse response = response_queue_.front();
        response_queue_.pop();
        return response;
    }
    
    MCPResponse timeout_response;
    timeout_response.is_error = true;
    timeout_response.error = "SSE request timeout";
    return timeout_response;
}

void MCPClient::processNotificationsSSE() {
    if (!curl_handle_) {
        return;
    }
    
    // 创建 SSE 监听连接
    CURL* curl = curl_easy_init();
    if (!curl) {
        LOG_ERROR("Failed to create CURL handle for SSE events");
        return;
    }
    
    // 构建 SSE 事件 URL
    std::string sse_url = config_.sse_url;
    if (sse_url.back() != '/') {
        sse_url += "/";
    }
    sse_url += "sse";
    if (!sse_session_id_.empty()) {
        sse_url += "?sessionId=" + sse_session_id_;
    }
    
    curl_easy_setopt(curl, CURLOPT_URL, sse_url.c_str());
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, sseWriteCallback);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, this);
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, 0L);  // 无超时，持续监听
    
    // 设置请求头
    struct curl_slist* headers = nullptr;
    headers = curl_slist_append(headers, "Accept: text/event-stream");
    headers = curl_slist_append(headers, "Cache-Control: no-cache");
    if (!config_.api_key.empty()) {
        std::string auth_header = "Authorization: Bearer " + config_.api_key;
        headers = curl_slist_append(headers, auth_header.c_str());
    }
    curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
    
    // SSL 验证
    if (!config_.verify_ssl) {
        curl_easy_setopt(curl, CURLOPT_SSL_VERIFYPEER, 0L);
        curl_easy_setopt(curl, CURLOPT_SSL_VERIFYHOST, 0L);
    }
    
    // 执行 SSE 监听（阻塞直到连接关闭或 running_ 变为 false）
    while (running_) {
        CURLcode res = curl_easy_perform(curl);
        if (res != CURLE_OK && running_) {
            LOG_WARN("SSE connection interrupted: " + std::string(curl_easy_strerror(res)));
            // 短暂等待后重连
            std::this_thread::sleep_for(std::chrono::seconds(1));
        }
    }
    
    curl_slist_free_all(headers);
    curl_easy_cleanup(curl);
}

size_t MCPClient::sseWriteCallback(char* ptr, size_t size, size_t nmemb, void* userdata) {
    MCPClient* client = static_cast<MCPClient*>(userdata);
    size_t total_size = size * nmemb;
    
    std::string data(ptr, total_size);
    client->sse_response_buffer_ += data;
    
    // 解析 SSE 事件
    size_t pos = 0;
    while ((pos = client->sse_response_buffer_.find("\n\n")) != std::string::npos) {
        std::string event = client->sse_response_buffer_.substr(0, pos);
        client->sse_response_buffer_.erase(0, pos + 2);
        
        // 解析事件数据
        std::string event_data;
        std::istringstream iss(event);
        std::string line;
        while (std::getline(iss, line)) {
            if (line.substr(0, 5) == "data:") {
                event_data = line.substr(5);
                // 去除前导空格
                size_t start = event_data.find_first_not_of(" ");
                if (start != std::string::npos) {
                    event_data = event_data.substr(start);
                }
            } else if (line.substr(0, 3) == "id:") {
                // 更新 session ID
                client->sse_session_id_ = line.substr(3);
                size_t start = client->sse_session_id_.find_first_not_of(" ");
                if (start != std::string::npos) {
                    client->sse_session_id_ = client->sse_session_id_.substr(start);
                }
            }
        }
        
        if (!event_data.empty()) {
            // 解析 JSON-RPC 响应或通知
            MCPResponse response = client->parseJSONRPCResponse(event_data);
            
            if (response.id.empty()) {
                // 这是一个通知
                if (client->notification_callback_) {
                    try {
                        Json::Value root;
                        Json::Reader reader;
                        if (reader.parse(event_data, root)) {
                            std::string method = root["method"].asString();
                            if (method == "notifications/message") {
                                const Json::Value& params = root["params"];
                                std::string plugin_name = params["pluginName"].asString();
                                std::string notification = params["notification"].asString();
                                client->notification_callback_(plugin_name, notification);
                            }
                        }
                    } catch (const std::exception& e) {
                        LOG_WARN("Failed to parse SSE notification: " + std::string(e.what()));
                    }
                }
            } else {
                // 这是一个响应
                std::lock_guard<std::mutex> lock(client->queue_mutex_);
                client->response_queue_.push(response);
                client->queue_cv_.notify_one();
            }
        }
    }
    
    return total_size;
}

size_t MCPClient::sseHeaderCallback(char* buffer, size_t size, size_t nitems, void* userdata) {
    // 可以在这里解析响应头
    return size * nitems;
}

} // namespace mcp
} // namespace agent_rpc


