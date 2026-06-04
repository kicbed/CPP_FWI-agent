#include "agent_rpc/client/rpc_client.h"
#include "agent_rpc/common/rpc_framework.h"
#include <iostream>
#include <signal.h>
#include <thread>
#include <chrono>
#include <random>

using namespace agent_rpc::client;
using namespace agent_rpc::common;

// 全局变量用于优雅关闭
std::atomic<bool> g_running{true};

// 信号处理函数
void signalHandler(int signal) {
    std::cout << "Received signal " << signal << ", shutting down..." << std::endl;
    g_running = false;
}

// 消息处理器
void messageHandler(const std::string& message) {
    std::cout << "Received message: " << message << std::endl;
}

// 错误处理器
void errorHandler(const std::string& error, int code) {
    std::cerr << "Error: " << error << " (code: " << code << ")" << std::endl;
}

// 生成随机消息
std::string generateRandomMessage() {
    static std::random_device rd;
    static std::mt19937 gen(rd());
    static std::uniform_int_distribution<> dis(1, 1000);
    
    return "Message " + std::to_string(dis(gen));
}

int main(int argc, char* argv[]) {
    // 设置信号处理
    signal(SIGINT, signalHandler);
    signal(SIGTERM, signalHandler);
    
    // 检查命令行参数
    if (argc < 2) {
        std::cerr << "Usage: " << argv[0] << " <server_address>" << std::endl;
        std::cerr << "Example: " << argv[0] << " localhost:50051" << std::endl;
        return 1;
    }
    
    std::string server_address = argv[1];
    
    // 配置RPC框架
    RpcConfig config;
    config.server_address = server_address;
    config.max_message_size = 4 * 1024 * 1024;  // 4MB
    config.max_receive_message_size = 4 * 1024 * 1024;  // 4MB
    config.timeout_seconds = 30;
    config.heartbeat_interval = 30;
    config.log_level = "INFO";
    
    // 初始化框架
    auto& framework = RpcFramework::getInstance();
    if (!framework.initialize(config)) {
        std::cerr << "Failed to initialize RPC framework" << std::endl;
        return 1;
    }
    
    // 创建客户端
    RpcClient client;
    if (!client.initialize(config)) {
        std::cerr << "Failed to initialize RPC client" << std::endl;
        return 1;
    }
    
    // 设置处理器
    client.setMessageHandler(messageHandler);
    client.setErrorHandler(errorHandler);
    
    // 连接到服务器
    if (!client.connect(server_address)) {
        std::cerr << "Failed to connect to server: " << server_address << std::endl;
        return 1;
    }
    
    std::cout << "Connected to RPC server: " << server_address << std::endl;
    
    // 注册代理
    ServiceEndpoint agent_info;
    agent_info.host = "localhost";
    agent_info.port = 8080;
    agent_info.service_name = "example_client";
    agent_info.version = "1.0.0";
    agent_info.metadata["type"] = "example";
    agent_info.metadata["description"] = "Example RPC client";
    
    std::string agent_id = client.registerAgent(agent_info);
    if (agent_id.empty()) {
        std::cerr << "Failed to register agent" << std::endl;
        return 1;
    }
    
    std::cout << "Agent registered with ID: " << agent_id << std::endl;
    
    // 获取代理列表
    auto agents = client.getAgents();
    std::cout << "Found " << agents.size() << " agents:" << std::endl;
    for (const auto& agent : agents) {
        std::cout << "  - " << agent.service_name << " (" << agent.host 
                  << ":" << agent.port << ") v" << agent.version << std::endl;
    }
    
    // 主循环 - 发送消息
    int message_count = 0;
    while (g_running) {
        // 创建消息
        std::string message = generateRandomMessage();
        
        // 发送消息（广播给所有代理）
        int success_count = client.broadcastMessage(message);
        if (success_count > 0) {
            std::cout << "Broadcasted message " << ++message_count 
                      << " to " << success_count << " agents" << std::endl;
        }
        
        // 接收消息
        auto received_messages = client.receiveMessages(agent_id, 10, 5);
        for (const auto& received_msg : received_messages) {
            std::cout << "Received: " << received_msg << std::endl;
        }
        
        // 等待一段时间
        std::this_thread::sleep_for(std::chrono::seconds(2));
    }
    
    // 注销代理
    if (!client.unregisterAgent(agent_id, "Client shutting down")) {
        std::cerr << "Failed to unregister agent" << std::endl;
    } else {
        std::cout << "Agent unregistered successfully" << std::endl;
    }
    
    // 断开连接
    client.disconnect();
    std::cout << "Disconnected from RPC server" << std::endl;
    
    return 0;
}