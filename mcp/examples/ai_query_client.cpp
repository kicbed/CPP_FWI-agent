/**
 * @file ai_query_client.cpp
 * @brief AI Query RPC Client - 通过 gRPC 发送 AI 查询请求
 * 
 * 这是项目的核心测试用例：
 * - rpc_client 通过 gRPC 连接 rpc_server
 * - rpc_server 通过 A2A 协议调用 Orchestrator
 * - Orchestrator 路由到各个专业 Agent (Math Agent 等)
 */

#include "agent_rpc/client/ai_query_client.h"
#include "agent_rpc/common/rpc_framework.h"
#include <iostream>
#include <signal.h>
#include <string>

using namespace agent_rpc::client;
using namespace agent_rpc::common;

// 全局变量用于优雅关闭
std::atomic<bool> g_running{true};

void signalHandler(int signal) {
    std::cout << "\n收到信号 " << signal << ", 退出..." << std::endl;
    g_running = false;
}

void printHelp() {
    std::cout << "\n命令:" << std::endl;
    std::cout << "  /help     - 显示帮助" << std::endl;
    std::cout << "  /stream   - 切换流式模式" << std::endl;
    std::cout << "  /context <id> - 切换上下文" << std::endl;
    std::cout << "  /quit     - 退出" << std::endl;
    std::cout << "\n直接输入问题发送给 AI\n" << std::endl;
}

void printUsage(const char* program) {
    std::cout << "用法: " << program << " <RPC_SERVER_ADDRESS>" << std::endl;
    std::cout << std::endl;
    std::cout << "参数:" << std::endl;
    std::cout << "  RPC_SERVER_ADDRESS - gRPC 服务器地址 (例如: localhost:50051)" << std::endl;
    std::cout << std::endl;
    std::cout << "示例:" << std::endl;
    std::cout << "  " << program << " localhost:50051" << std::endl;
    std::cout << std::endl;
    std::cout << "注意: 需要先启动以下服务:" << std::endl;
    std::cout << "  1. ai_orchestrator 系统 (./start_system.sh)" << std::endl;
    std::cout << "  2. rpc_server (./rpc_server)" << std::endl;
}

int main(int argc, char* argv[]) {
    if (argc < 2) {
        printUsage(argv[0]);
        return 1;
    }
    
    std::string server_address = argv[1];
    
    // 设置信号处理
    signal(SIGINT, signalHandler);
    signal(SIGTERM, signalHandler);
    
    std::cout << "==========================================" << std::endl;
    std::cout << "AI Query RPC Client" << std::endl;
    std::cout << "==========================================" << std::endl;
    std::cout << "连接到 RPC Server: " << server_address << std::endl;
    
    // 创建 AI 查询客户端
    AIQueryClient client;
    
    if (!client.connect(server_address)) {
        std::cerr << "无法连接到服务器: " << server_address << std::endl;
        return 1;
    }
    
    std::cout << "连接成功!" << std::endl;
    printHelp();
    
    std::string context_id = "default";
    bool stream_mode = false;
    std::string line;
    
    while (g_running) {
        std::cout << "[" << context_id << (stream_mode ? "/流式" : "") << "] > ";
        
        if (!std::getline(std::cin, line)) {
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
            bool success = client.queryStream(line, 
                [](const agent_communication::AIStreamEvent& event) {
                    if (event.event_type() == "partial") {
                        std::cout << event.content();
                        std::cout.flush();
                    } else if (event.event_type() == "complete") {
                        std::cout << std::endl;
                    } else if (event.event_type() == "error") {
                        std::cout << "\n错误: " << event.content() << std::endl;
                    }
                }, context_id, 60);
            
            if (!success) {
                std::cout << "\n流式查询失败" << std::endl;
            }
        } else {
            // 同步查询
            auto response = client.query(line, context_id, 60);
            
            if (response.status().code() == 0) {
                std::cout << "\nAI: " << response.answer() << std::endl;
                if (!response.agent_name().empty()) {
                    std::cout << "[处理 Agent: " << response.agent_name() 
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
