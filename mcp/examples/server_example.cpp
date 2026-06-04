/**
 * @file server_example.cpp
 * @brief RPC Server - gRPC 服务端，通过 A2A 协议调用 Orchestrator
 * 
 * 架构:
 *   rpc_client ──gRPC──> rpc_server ──A2A/HTTP──> Orchestrator ──> Agents
 */

#include "agent_rpc/server/rpc_server.h"
#include "agent_rpc/server/ai_query_service.h"
#include "agent_rpc/a2a_adapter/a2a_config.h"
#include "agent_rpc/common/logger.h"
#include <iostream>
#include <signal.h>
#include <thread>
#include <chrono>

using namespace agent_rpc::server;
using namespace agent_rpc::common;

// 全局变量用于优雅关闭
std::atomic<bool> g_running{true};
RpcServer* g_server = nullptr;

// 信号处理函数
void signalHandler(int signal) {
    std::cout << "\n收到信号 " << signal << ", 正在关闭服务器..." << std::endl;
    g_running = false;
    if (g_server) {
        g_server->stop();
    }
}

void printUsage(const char* program) {
    std::cout << "用法: " << program << " [PORT] [ORCHESTRATOR_URL]" << std::endl;
    std::cout << std::endl;
    std::cout << "参数:" << std::endl;
    std::cout << "  PORT            - gRPC 监听端口 (默认: 50051)" << std::endl;
    std::cout << "  ORCHESTRATOR_URL - Orchestrator 地址 (默认: http://localhost:5000)" << std::endl;
    std::cout << std::endl;
    std::cout << "示例:" << std::endl;
    std::cout << "  " << program << std::endl;
    std::cout << "  " << program << " 50051 http://localhost:5000" << std::endl;
    std::cout << std::endl;
    std::cout << "注意: 需要先启动 ai_orchestrator 系统:" << std::endl;
    std::cout << "  ./examples/ai_orchestrator/start_system.sh" << std::endl;
}

int main(int argc, char* argv[]) {
    // 解析参数
    std::string port = argc > 1 ? argv[1] : "50051";
    std::string orchestrator_url = argc > 2 ? argv[2] : "http://localhost:5000";
    
    if (port == "-h" || port == "--help") {
        printUsage(argv[0]);
        return 0;
    }
    
    // 设置信号处理
    signal(SIGINT, signalHandler);
    signal(SIGTERM, signalHandler);
    
    // 配置 RPC
    RpcConfig config;
    config.server_address = "0.0.0.0:" + port;
    config.max_message_size = 4 * 1024 * 1024;
    config.max_receive_message_size = 4 * 1024 * 1024;
    config.timeout_seconds = 60;
    config.log_level = "INFO";
    
    // 配置 A2A 适配器
    agent_rpc::a2a_adapter::A2AConfig a2a_config;
    a2a_config.orchestrator_url = orchestrator_url;
    a2a_config.request_timeout_seconds = 60;
    
    // 创建服务器
    RpcServer server;
    g_server = &server;
    
    server.setA2AConfig(a2a_config);
    
    if (!server.initialize(config)) {
        std::cerr << "无法初始化 RPC 服务器" << std::endl;
        return 1;
    }
    
    // 检查 AI 查询服务
    auto ai_service = server.getAIQueryService();
    if (ai_service && ai_service->isAvailable()) {
        std::cout << "[RPC Server] AI 查询服务已就绪" << std::endl;
    } else {
        std::cout << "[RPC Server] 警告: AI 查询服务不可用 (Orchestrator 可能未启动)" << std::endl;
    }
    
    // 启动服务器
    if (!server.start()) {
        std::cerr << "无法启动 RPC 服务器" << std::endl;
        return 1;
    }
    
    std::cout << "==========================================" << std::endl;
    std::cout << "RPC Server 启动成功" << std::endl;
    std::cout << "==========================================" << std::endl;
    std::cout << "gRPC 地址:      " << config.server_address << std::endl;
    std::cout << "Orchestrator:   " << orchestrator_url << std::endl;
    std::cout << std::endl;
    std::cout << "使用客户端连接:" << std::endl;
    std::cout << "  ./ai_query_client localhost:" << port << std::endl;
    std::cout << std::endl;
    std::cout << "按 Ctrl+C 停止服务器" << std::endl;
    std::cout << "==========================================" << std::endl;
    
    // 主循环
    while (g_running) {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    
    // 停止服务器
    server.stop();
    std::cout << "RPC 服务器已停止" << std::endl;
    
    return 0;
}
