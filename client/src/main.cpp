/**
 * @file main.cpp
 * @brief RPC Client 主程序
 * 
 * 这是项目的核心客户端程序：
 * - 通过 gRPC 连接 RPC Server
 * - 发送 AI 查询请求
 * - 支持同步和流式查询
 * 
 * 架构:
 *   rpc_client ──gRPC──> rpc_server ──A2A/HTTP──> Orchestrator ──> Agents
 */

#include "agent_rpc/client/rpc_client.h"
#include "agent_rpc/common/logger.h"
#include <iostream>
#include <signal.h>
#include <string>
#include <cstdlib>
#include <cctype>
#include <vector>
#include <atomic>
#include <chrono>
#include <cstdint>

using namespace agent_rpc::client;
using namespace agent_rpc::common;

std::string generateContextId() {
    static std::atomic<std::uint64_t> sequence{0};
    const auto now = std::chrono::system_clock::now().time_since_epoch().count();
    return "ctx-cli-" + std::to_string(now) + "-" +
           std::to_string(sequence.fetch_add(1, std::memory_order_relaxed) + 1);
}

// 全局变量用于优雅关闭
std::atomic<bool> g_running{true};

void signalHandler(int signal) {
    std::cout << "\n收到信号 " << signal << ", 退出..." << std::endl;
    g_running = false;
    // 直接退出程序，因为 getline 是阻塞的，无法被信号中断
    std::exit(0);
}

void printHelp() {
    std::cout << "\n命令:" << std::endl;
    std::cout << "  /help     - 显示帮助" << std::endl;
    std::cout << "  /stream   - 切换流式模式" << std::endl;
    std::cout << "  /context <id> - 切换上下文" << std::endl;
    std::cout << "  /status   - 查看连接状态" << std::endl;
    std::cout << "  /quit     - 退出" << std::endl;
    std::cout << "\n直接输入问题发送给 AI\n" << std::endl;
}

void printUsage(const char* program) {
    std::cout << "RPC Client - AI Agent 通信客户端" << std::endl;
    std::cout << std::endl;
    std::cout << "用法: " << program << " [选项] [SERVER_ADDRESS]" << std::endl;
    std::cout << std::endl;
    std::cout << "参数:" << std::endl;
    std::cout << "  SERVER_ADDRESS    RPC Server 地址 (默认: localhost:50051)" << std::endl;
    std::cout << std::endl;
    std::cout << "选项:" << std::endl;
    std::cout << "  -s, --stream      启用流式模式" << std::endl;
    std::cout << "  -c, --context ID  设置上下文 ID" << std::endl;
    std::cout << "  -r, --registry ADDR  通过注册中心发现服务" << std::endl;
    std::cout << "      --service NAME   注册中心中的服务名 (默认: rpc_server)" << std::endl;
    std::cout << "  -t, --timeout SEC 超时时间 (默认: 60)" << std::endl;
    std::cout << "  -h, --help        显示帮助信息" << std::endl;
    std::cout << std::endl;
    std::cout << "环境变量:" << std::endl;
    std::cout << "  RPC_SERVER_ADDRESS  RPC Server 地址" << std::endl;
    std::cout << "  RPC_REGISTRY_ADDRESS 注册中心地址" << std::endl;
    std::cout << "  RPC_SERVICE_NAME    服务名" << std::endl;
    std::cout << std::endl;
    std::cout << "示例:" << std::endl;
    std::cout << "  " << program << std::endl;
    std::cout << "  " << program << " localhost:50051" << std::endl;
    std::cout << "  " << program << " -s localhost:50051  # 流式模式" << std::endl;
    std::cout << "  " << program << " --registry memory --service rpc_server" << std::endl;
}

std::vector<std::string> splitAddresses(const std::string& addresses) {
    std::vector<std::string> result;
    std::string current;

    for (char ch : addresses) {
        if (ch == ',') {
            if (!current.empty()) {
                result.push_back(current);
                current.clear();
            }
        } else if (!std::isspace(static_cast<unsigned char>(ch))) {
            current.push_back(ch);
        }
    }

    if (!current.empty()) {
        result.push_back(current);
    }

    return result;
}

int main(int argc, char* argv[]) {
    // 默认配置
    std::string server_address = "localhost:50051";
    std::string registry_address;
    std::string service_name = "rpc_server";
    std::string context_id = generateContextId();
    bool stream_mode = false;
    int timeout_seconds = 60;
    
    // 从环境变量读取
    if (const char* env_addr = std::getenv("RPC_SERVER_ADDRESS")) {
        server_address = env_addr;
    }
    if (const char* env_registry = std::getenv("RPC_REGISTRY_ADDRESS")) {
        registry_address = env_registry;
    }
    if (const char* env_service = std::getenv("RPC_SERVICE_NAME")) {
        service_name = env_service;
    }
    
    // 解析命令行参数
    for (int i = 1; i < argc; i++) {
        std::string arg = argv[i];
        
        if (arg == "-h" || arg == "--help") {
            printUsage(argv[0]);
            return 0;
        } else if (arg == "-s" || arg == "--stream") {
            stream_mode = true;
        } else if ((arg == "-c" || arg == "--context") && i + 1 < argc) {
            context_id = argv[++i];
        } else if ((arg == "-r" || arg == "--registry") && i + 1 < argc) {
            registry_address = argv[++i];
        } else if (arg == "--service" && i + 1 < argc) {
            service_name = argv[++i];
        } else if ((arg == "-t" || arg == "--timeout") && i + 1 < argc) {
            timeout_seconds = std::atoi(argv[++i]);
        } else if (arg[0] != '-') {
            server_address = arg;
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
    
    std::cout << "==========================================" << std::endl;
    std::cout << "RPC Client - AI 查询客户端" << std::endl;
    std::cout << "==========================================" << std::endl;
    if (!registry_address.empty()) {
        std::cout << "注册中心: " << registry_address << std::endl;
        std::cout << "服务名:   " << service_name << std::endl;
    } else {
        std::cout << "连接到: " << server_address << std::endl;
    }
    
    RpcClient client;
    RpcConfig config;
    config.timeout_seconds = timeout_seconds;
    config.max_message_size = 64 * 1024 * 1024;
    config.max_receive_message_size = 64 * 1024 * 1024;
    if (!client.initialize(config)) {
        std::cerr << "错误: 无法初始化 RPC 客户端" << std::endl;
        return 1;
    }

    bool connected = false;
    if (!registry_address.empty()) {
        connected = client.connectViaRegistry(
            registry_address, service_name, LoadBalanceStrategy::ROUND_ROBIN);
    } else {
        const auto addresses = splitAddresses(server_address);
        connected = addresses.size() <= 1
            ? client.connect(server_address)
            : client.connect(addresses, LoadBalanceStrategy::ROUND_ROBIN);
    }

    if (!connected) {
        std::cerr << "错误: 无法连接到服务器";
        if (!registry_address.empty()) {
            std::cerr << " (registry=" << registry_address << ", service=" << service_name << ")";
        } else {
            std::cerr << " " << server_address;
        }
        std::cerr << std::endl;
        std::cerr << "请确保 rpc_server 已启动" << std::endl;
        return 1;
    }
    
    std::cout << "连接成功!" << std::endl;
    std::cout << "流式模式: " << (stream_mode ? "开启" : "关闭") << std::endl;
    std::cout << "上下文 ID: " << context_id << std::endl;
    printHelp();
    
    std::string line;
    
    while (g_running) {
        std::cout << "[" << context_id << (stream_mode ? "/流式" : "") << "] > ";
        std::cout.flush();
        
        if (!std::getline(std::cin, line)) {
            // EOF 或输入错误，退出循环
            if (std::cin.eof()) {
                std::cout << "\n" << std::endl;
            }
            break;
        }
        
        // 检查是否收到退出信号
        if (!g_running) {
            break;
        }
        
        if (line.empty()) continue;
        
        // 处理命令
        if (line == "/quit" || line == "/exit" || line == "/q") {
            std::cout << "再见!" << std::endl;
            break;
        }
        
        if (line == "/help" || line == "/h") {
            printHelp();
            continue;
        }
        
        if (line == "/stream" || line == "/s") {
            stream_mode = !stream_mode;
            std::cout << "流式模式: " << (stream_mode ? "开启" : "关闭") << std::endl;
            continue;
        }
        
        if (line == "/status") {
            std::cout << "连接状态: " << (client.isConnected() ? "已连接" : "未连接") << std::endl;
            std::cout << "服务器: " << client.getServerAddress() << std::endl;
            std::cout << "上下文: " << context_id << std::endl;
            continue;
        }
        
        if (line.substr(0, 9) == "/context " || line.substr(0, 3) == "/c ") {
            size_t pos = line.find(' ');
            if (pos != std::string::npos) {
                context_id = line.substr(pos + 1);
                std::cout << "切换到上下文: " << context_id << std::endl;
            }
            continue;
        }
        
        // 发送 AI 查询
        std::cout << "\n思考中..." << std::endl;
        
        if (stream_mode) {
            // 流式查询
            std::cout << "\nAI: ";
            std::cout.flush();
            
            bool success = client.aiQueryStream(line, 
                [](const agent_communication::AIStreamEvent& event) {
                    if (event.event_type() == "partial") {
                        std::cout << event.content();
                        std::cout.flush();
                    } else if (event.event_type() == "complete") {
                        std::cout << std::endl;
                    } else if (event.event_type() == "error") {
                        std::cout << "\n错误: " << event.content() << std::endl;
                    }
                }, context_id, timeout_seconds);
            
            if (!success) {
                std::cout << "\n流式查询失败" << std::endl;
            }
        } else {
            // 同步查询
            auto response = client.aiQuery(line, context_id, timeout_seconds);
            
            if (response.status().code() == 0) {
                std::cout << "\nAI: " << response.answer() << std::endl;
                if (!response.agent_name().empty()) {
                    std::cout << "[Agent: " << response.agent_name() 
                              << ", 耗时: " << response.processing_time_ms() << "ms]" << std::endl;
                }
            } else {
                std::cout << "\n错误: " << response.status().message() << std::endl;
            }
        }
        std::cout << std::endl;
    }
    
    client.disconnect();
    std::cout << "已断开连接" << std::endl;
    
    return 0;
}
