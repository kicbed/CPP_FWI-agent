/**
 * @file grpc_server_main.cpp
 * @brief gRPC AI Server - 集成 AI 模型的 gRPC 服务端
 * 
 * 这是项目的核心示例：
 * - 使用 gRPC 提供 AI 查询服务
 * - 直接集成 Qwen 等 AI 模型
 * - 可选集成 MCP 工具
 */

#include "agent_rpc/server/rpc_server.h"
#include "agent_rpc/common/rpc_framework.h"
#include "agent_rpc/common/logger.h"
#include "ai_query.grpc.pb.h"

#include <grpcpp/grpcpp.h>
#include <iostream>
#include <signal.h>
#include <thread>
#include <chrono>
#include <curl/curl.h>
#include <json/json.h>

using grpc::Server;
using grpc::ServerBuilder;
using grpc::ServerContext;
using grpc::Status;
using grpc::ServerWriter;

// 全局变量用于优雅关闭
std::atomic<bool> g_shutdown_requested{false};
std::unique_ptr<Server> g_server;

// 信号处理函数 - 只设置标志，不直接调用 Shutdown
void signalHandler(int /*signal*/) {
    // 信号处理函数中不能调用 Shutdown()，会导致 mutex 死锁
    // 只设置标志，让主线程处理关闭
    g_shutdown_requested.store(true);
}

// CURL 回调函数
static size_t WriteCallback(void* contents, size_t size, size_t nmemb, std::string* userp) {
    userp->append((char*)contents, size * nmemb);
    return size * nmemb;
}

/**
 * @brief AI 查询服务实现
 * 
 * 直接调用 Qwen API，不依赖 A2A 适配器
 */
class DirectAIQueryServiceImpl final : public agent_communication::AIQueryService::Service {
public:
    DirectAIQueryServiceImpl(const std::string& api_key, const std::string& model = "qwen-plus")
        : api_key_(api_key), model_(model) {
        curl_global_init(CURL_GLOBAL_DEFAULT);
        std::cout << "[AIService] 初始化完成，使用模型: " << model_ << std::endl;
    }
    
    ~DirectAIQueryServiceImpl() {
        curl_global_cleanup();
    }
    
    // 同步查询
    Status Query(ServerContext* /*context*/,
                 const agent_communication::AIQueryRequest* request,
                 agent_communication::AIQueryResponse* response) override {
        
        std::cout << "[AIService] 收到查询请求: " << request->question() << std::endl;
        
        auto start_time = std::chrono::steady_clock::now();
        
        // 调用 AI 模型
        std::string answer;
        std::string error;
        bool success = callQwenAPI(request->question(), answer, error);
        
        auto end_time = std::chrono::steady_clock::now();
        auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);
        
        // 设置响应
        response->set_request_id(request->request_id());
        response->set_context_id(request->context_id());
        response->set_processing_time_ms(duration.count());
        
        auto* status = response->mutable_status();
        if (success) {
            status->set_code(0);
            status->set_message("OK");
            response->set_answer(answer);
            response->set_agent_name("DirectAIService");
            std::cout << "[AIService] 查询成功，耗时: " << duration.count() << "ms" << std::endl;
            return Status::OK;
        } else {
            status->set_code(-1);
            status->set_message(error);
            std::cout << "[AIService] 查询失败: " << error << std::endl;
            return Status(grpc::StatusCode::INTERNAL, error);
        }
    }
    
    // 流式查询
    Status QueryStream(ServerContext* context,
                       const agent_communication::AIQueryRequest* request,
                       ServerWriter<agent_communication::AIStreamEvent>* writer) override {
        
        std::cout << "[AIService] 收到流式查询请求: " << request->question() << std::endl;
        
        // 发送开始事件
        agent_communication::AIStreamEvent start_event;
        start_event.set_event_type("status");
        start_event.set_task_state("processing");
        start_event.set_context_id(request->context_id());
        writer->Write(start_event);
        
        // 调用 AI 模型
        std::string answer;
        std::string error;
        bool success = callQwenAPI(request->question(), answer, error);
        
        if (success) {
            // 模拟流式输出（将答案分块发送）
            size_t chunk_size = 50;
            for (size_t i = 0; i < answer.length(); i += chunk_size) {
                if (context->IsCancelled()) {
                    return Status(grpc::StatusCode::CANCELLED, "Client cancelled");
                }
                
                agent_communication::AIStreamEvent chunk_event;
                chunk_event.set_event_type("partial");
                chunk_event.set_content(answer.substr(i, chunk_size));
                chunk_event.set_context_id(request->context_id());
                writer->Write(chunk_event);
                
                std::this_thread::sleep_for(std::chrono::milliseconds(50));
            }
            
            // 发送完成事件
            agent_communication::AIStreamEvent complete_event;
            complete_event.set_event_type("complete");
            complete_event.set_content(answer);
            complete_event.set_task_state("completed");
            complete_event.set_context_id(request->context_id());
            writer->Write(complete_event);
            
            return Status::OK;
        } else {
            agent_communication::AIStreamEvent error_event;
            error_event.set_event_type("error");
            error_event.set_content(error);
            error_event.set_task_state("failed");
            writer->Write(error_event);
            
            return Status(grpc::StatusCode::INTERNAL, error);
        }
    }
    
    // 获取查询状态
    Status GetQueryStatus(ServerContext* /*context*/,
                          const agent_communication::QueryStatusRequest* /*request*/,
                          agent_communication::QueryStatusResponse* response) override {
        auto* status = response->mutable_status();
        status->set_code(0);
        status->set_message("OK");
        response->set_task_state("completed");
        return Status::OK;
    }

private:
    bool callQwenAPI(const std::string& question, std::string& answer, std::string& error) {
        CURL* curl = curl_easy_init();
        if (!curl) {
            error = "Failed to initialize CURL";
            return false;
        }
        
        // 构建请求 JSON
        Json::Value request_json;
        request_json["model"] = model_;
        request_json["messages"] = Json::Value(Json::arrayValue);
        
        Json::Value message;
        message["role"] = "user";
        message["content"] = question;
        request_json["messages"].append(message);
        
        Json::StreamWriterBuilder writer;
        std::string request_body = Json::writeString(writer, request_json);
        
        // 设置 CURL 选项
        std::string response_body;
        std::string url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions";
        
        struct curl_slist* headers = nullptr;
        headers = curl_slist_append(headers, "Content-Type: application/json");
        headers = curl_slist_append(headers, ("Authorization: Bearer " + api_key_).c_str());
        
        curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
        curl_easy_setopt(curl, CURLOPT_POSTFIELDS, request_body.c_str());
        curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
        curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, WriteCallback);
        curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response_body);
        curl_easy_setopt(curl, CURLOPT_TIMEOUT, 60L);
        
        CURLcode res = curl_easy_perform(curl);
        
        curl_slist_free_all(headers);
        curl_easy_cleanup(curl);
        
        if (res != CURLE_OK) {
            error = "CURL error: " + std::string(curl_easy_strerror(res));
            return false;
        }
        
        // 解析响应
        Json::Value response_json;
        Json::Reader reader;
        if (!reader.parse(response_body, response_json)) {
            error = "Failed to parse response JSON";
            return false;
        }
        
        if (response_json.isMember("error")) {
            error = response_json["error"]["message"].asString();
            return false;
        }
        
        if (response_json.isMember("choices") && 
            response_json["choices"].isArray() && 
            response_json["choices"].size() > 0) {
            answer = response_json["choices"][0]["message"]["content"].asString();
            return true;
        }
        
        error = "Invalid response format";
        return false;
    }
    
    std::string api_key_;
    std::string model_;
};

void printUsage(const char* program) {
    std::cout << "用法: " << program << " <API_KEY> [PORT] [MODEL]" << std::endl;
    std::cout << std::endl;
    std::cout << "参数:" << std::endl;
    std::cout << "  API_KEY  - Qwen API Key (必需)" << std::endl;
    std::cout << "  PORT     - 监听端口 (默认: 50051)" << std::endl;
    std::cout << "  MODEL    - AI 模型名称 (默认: qwen-plus)" << std::endl;
    std::cout << std::endl;
    std::cout << "示例:" << std::endl;
    std::cout << "  " << program << " sk-xxx" << std::endl;
    std::cout << "  " << program << " sk-xxx 50051 qwen-turbo" << std::endl;
}

int main(int argc, char* argv[]) {
    if (argc < 2) {
        printUsage(argv[0]);
        return 1;
    }
    
    std::string api_key = argv[1];
    std::string port = argc > 2 ? argv[2] : "50051";
    std::string model = argc > 3 ? argv[3] : "qwen-plus";
    
    // 设置信号处理
    signal(SIGINT, signalHandler);
    signal(SIGTERM, signalHandler);
    
    std::string server_address = "0.0.0.0:" + port;
    
    // 创建服务实现
    DirectAIQueryServiceImpl service(api_key, model);
    
    // 构建服务器
    ServerBuilder builder;
    builder.AddListeningPort(server_address, grpc::InsecureServerCredentials());
    builder.RegisterService(&service);
    builder.SetMaxReceiveMessageSize(64 * 1024 * 1024);
    builder.SetMaxSendMessageSize(64 * 1024 * 1024);
    
    g_server = builder.BuildAndStart();
    
    if (!g_server) {
        std::cerr << "无法启动服务器" << std::endl;
        return 1;
    }
    
    std::cout << "==========================================" << std::endl;
    std::cout << "gRPC AI Server 启动成功" << std::endl;
    std::cout << "==========================================" << std::endl;
    std::cout << "监听地址: " << server_address << std::endl;
    std::cout << "AI 模型:  " << model << std::endl;
    std::cout << std::endl;
    std::cout << "使用客户端连接:" << std::endl;
    std::cout << "  ./grpc_client localhost:" << port << std::endl;
    std::cout << std::endl;
    std::cout << "按 Ctrl+C 停止服务器" << std::endl;
    std::cout << "==========================================" << std::endl;
    
    // 启动一个监控线程来检测关闭信号
    std::thread shutdown_monitor([&]() {
        while (!g_shutdown_requested.load()) {
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
        std::cout << "\n收到关闭信号，正在停止服务器..." << std::endl;
        if (g_server) {
            // 设置一个截止时间，避免无限等待
            auto deadline = std::chrono::system_clock::now() + std::chrono::seconds(5);
            g_server->Shutdown(deadline);
        }
    });
    
    // 等待服务器关闭
    g_server->Wait();
    
    // 等待监控线程结束
    if (shutdown_monitor.joinable()) {
        shutdown_monitor.join();
    }
    
    std::cout << "服务器已停止" << std::endl;
    return 0;
}
