/**
 * @file main.cpp
 * @brief RPC Server 主程序
 * 
 * 这是项目的核心服务端程序：
 * - 提供 gRPC 服务，接收客户端请求
 * - 通过 A2A 协议调用 Orchestrator 协调多 Agent
 * - 支持 AI 查询、流式响应等功能
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
#include <cstdlib>

using namespace agent_rpc::server;
using namespace agent_rpc::common;

// 全局变量用于优雅关闭
std::atomic<bool> g_running{true};
RpcServer* g_server = nullptr;

void signalHandler(int signal) {
    std::cout << "\n收到信号 " << signal << ", 正在关闭服务器..." << std::endl;
    g_running = false;
    // 不在信号处理函数中调用 stop()，让主循环处理
}

void printUsage(const char* program) {
    std::cout << "RPC Server - AI Agent 通信服务端" << std::endl;
    std::cout << std::endl;
    std::cout << "用法: " << program << " [选项]" << std::endl;
    std::cout << std::endl;
    std::cout << "选项:" << std::endl;
    std::cout << "  -p, --port PORT           gRPC 监听端口 (默认: 50051)" << std::endl;
    std::cout << "  -o, --orchestrator URL    Orchestrator 地址 (默认: http://localhost:5000)" << std::endl;
    std::cout << "  -r, --registry ADDR       注册中心地址，例如 consul://127.0.0.1:8500" << std::endl;
    std::cout << "      --enable-registry     显式启用服务注册" << std::endl;
    std::cout << "  -t, --timeout SECONDS     请求超时时间 (默认: 60)" << std::endl;
    std::cout << "  -h, --help                显示帮助信息" << std::endl;
    std::cout << std::endl;
    std::cout << "环境变量:" << std::endl;
    std::cout << "  RPC_SERVER_PORT           gRPC 监听端口" << std::endl;
    std::cout << "  ORCHESTRATOR_URL          Orchestrator 地址" << std::endl;
    std::cout << "  RPC_REGISTRY_ADDRESS      注册中心地址" << std::endl;
    std::cout << std::endl;
    std::cout << "示例:" << std::endl;
    std::cout << "  " << program << std::endl;
    std::cout << "  " << program << " -p 50051 -o http://localhost:5000" << std::endl;
    std::cout << std::endl;
    std::cout << "启动顺序:" << std::endl;
    std::cout << "  1. 启动 ai_orchestrator 系统: ./start_system.sh" << std::endl;
    std::cout << "  2. 启动 rpc_server: ./rpc_server" << std::endl;
    std::cout << "  3. 使用 rpc_client 连接: ./rpc_client localhost:50051" << std::endl;
}

int main(int argc, char* argv[]) {
    // 默认配置
    std::string port = "50051";
    std::string orchestrator_url = "http://localhost:5000";
    std::string registry_address = "localhost:8500";
    bool enable_registry = false;
    int timeout_seconds = 60;
    
    // 从环境变量读取
    if (const char* env_port = std::getenv("RPC_SERVER_PORT")) {
        port = env_port;
    }
    if (const char* env_url = std::getenv("ORCHESTRATOR_URL")) {
        orchestrator_url = env_url;
    }
    if (const char* env_registry = std::getenv("RPC_REGISTRY_ADDRESS")) {
        registry_address = env_registry;
        enable_registry = true;
    }
    
    // 解析命令行参数
    for (int i = 1; i < argc; i++) {
        std::string arg = argv[i];
        
        if (arg == "-h" || arg == "--help") {
            printUsage(argv[0]);
            return 0;
        } else if ((arg == "-p" || arg == "--port") && i + 1 < argc) {
            port = argv[++i];
        } else if ((arg == "-o" || arg == "--orchestrator") && i + 1 < argc) {
            orchestrator_url = argv[++i];
        } else if ((arg == "-r" || arg == "--registry") && i + 1 < argc) {
            registry_address = argv[++i];
            enable_registry = true;
        } else if (arg == "--enable-registry") {
            enable_registry = true;
        } else if ((arg == "-t" || arg == "--timeout") && i + 1 < argc) {
            timeout_seconds = std::atoi(argv[++i]);
        } else {
            std::cerr << "未知参数: " << arg << std::endl;
            printUsage(argv[0]);
            return 1;
        }
    }
    
    // 设置信号处理
    signal(SIGINT, signalHandler);
    signal(SIGTERM, signalHandler);

    LogConfig log_config;
    log_config.level = LogLevel::Level_INFO;
    log_config.async_logging = true;
    log_config.color_output = true;
    initializeAdvancedLogger(log_config);
    
    // 配置 RPC Server
    RpcConfig config;
    config.server_address = "0.0.0.0:" + port;
    config.max_message_size = 64 * 1024 * 1024;  // 64MB
    config.max_receive_message_size = 64 * 1024 * 1024;
    config.timeout_seconds = timeout_seconds;
    config.log_level = "INFO";
    config.enable_service_registry = enable_registry;
    if (enable_registry) {
        config.registry_address = registry_address;
    }
    
    // 配置 A2A 适配器
    agent_rpc::a2a_adapter::A2AConfig a2a_config;
    a2a_config.orchestrator_url = orchestrator_url;
    a2a_config.request_timeout_seconds = timeout_seconds;
    
    // 创建并初始化服务器
    RpcServer server;
    g_server = &server;
    
    server.setA2AConfig(a2a_config);
    
    LOG_INFO("正在初始化 RPC Server...");
    
    if (!server.initialize(config)) {
        LOG_ERROR("无法初始化 RPC 服务器");
        std::cerr << "错误: 无法初始化 RPC 服务器" << std::endl;
        return 1;
    }
    
    // 检查 AI 查询服务状态
    auto ai_service = server.getAIQueryService();
    bool ai_available = ai_service && ai_service->isAvailable();
    
    // 启动服务器
    if (!server.start()) {
        LOG_ERROR("无法启动 RPC 服务器");
        std::cerr << "错误: 无法启动 RPC 服务器" << std::endl;
        return 1;
    }
    
    // 打印启动信息
    std::cout << "==========================================" << std::endl;
    std::cout << "RPC Server 启动成功" << std::endl;
    std::cout << "==========================================" << std::endl;
    std::cout << "gRPC 地址:      " << config.server_address << std::endl;
    std::cout << "Orchestrator:   " << orchestrator_url << std::endl;
    if (enable_registry) {
        std::cout << "Registry:       " << registry_address << std::endl;
    }
    std::cout << "AI 服务状态:    " << (ai_available ? "可用" : "不可用") << std::endl;
    std::cout << "超时时间:       " << timeout_seconds << " 秒" << std::endl;
    std::cout << std::endl;
    std::cout << "使用客户端连接:" << std::endl;
    std::cout << "  ./rpc_client localhost:" << port << std::endl;
    std::cout << std::endl;
    std::cout << "按 Ctrl+C 停止服务器" << std::endl;
    std::cout << "==========================================" << std::endl;
    
    LOG_INFO("RPC Server 已启动: " + config.server_address);
    
    // 主循环
    while (g_running) {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    
    // 停止服务器
    server.stop();
    LOG_INFO("RPC Server 已停止");
    std::cout << "RPC 服务器已停止" << std::endl;
    
    return 0;
}
