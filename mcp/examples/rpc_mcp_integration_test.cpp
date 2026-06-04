#include <iostream>
#include <string>
#include <vector>
#include <chrono>
#include <thread>
#include "agent_rpc/server/rpc_server.h"
#include "agent_rpc/common/logger.h"

using namespace agent_rpc::server;
using namespace agent_rpc::common;

void printSeparator(const std::string& title) {
    std::cout << "\n" << std::string(60, '=') << std::endl;
    std::cout << " " << title << std::endl;
    std::cout << std::string(60, '=') << std::endl;
}

int main() {
    std::cout << "=== RPC服务器与MCP集成测试 ===" << std::endl;
    
    // 设置日志级别
    setLogLevel(LogLevel::INFO);
    
    printSeparator("初始化RPC服务器");
    
    // 创建RPC服务器
    RpcServer rpc_server;
    
    // 配置RPC服务器
    RpcConfig config;
    config.server_address = "0.0.0.0:50051";
    config.max_message_size = 4 * 1024 * 1024;
    config.max_receive_message_size = 4 * 1024 * 1024;
    config.timeout_seconds = 30;
    config.max_retry_attempts = 3;
    config.heartbeat_interval = 30;
    config.enable_ssl = false;
    config.log_level = "INFO";
    
    // 设置MCP服务器路径和参数
    rpc_server.setMCPServerPath("/root/agent-communication/mcp_server_integrated/build/mcp_server");
    rpc_server.setMCPServerArgs({
        "-n", "rpc-integrated-server",
        "-l", "/tmp/mcp_logs",
        "-p", "/root/agent-communication/mcp_server_integrated/plugins"
    });
    
    // 初始化RPC服务器
    if (!rpc_server.initialize(config)) {
        std::cerr << "❌ RPC服务器初始化失败!" << std::endl;
        return 1;
    }
    
    std::cout << "✅ RPC服务器初始化成功!" << std::endl;
    
    printSeparator("启动RPC服务器");
    
    // 启动RPC服务器
    if (!rpc_server.start()) {
        std::cerr << "❌ RPC服务器启动失败!" << std::endl;
        return 1;
    }
    
    std::cout << "✅ RPC服务器启动成功!" << std::endl;
    std::cout << "服务器地址: " << rpc_server.getAddress() << std::endl;
    
    printSeparator("测试AI工具功能");
    
    // 获取服务实现
    auto service = rpc_server.getService();
    if (!service) {
        std::cerr << "❌ 获取服务实现失败!" << std::endl;
        return 1;
    }
    
    // 获取可用AI工具
    auto available_tools = service->getAvailableAITools();
    std::cout << "可用AI工具数量: " << available_tools.size() << std::endl;
    
    for (const auto& tool : available_tools) {
        std::cout << "  - " << tool << std::endl;
    }
    
    if (available_tools.empty()) {
        std::cout << "⚠️  没有可用的AI工具，请检查MCP服务器是否正常运行" << std::endl;
    } else {
        // 测试AI工具调用
        std::cout << "\n测试AI工具调用..." << std::endl;
        
        // 测试sleep工具
        if (std::find(available_tools.begin(), available_tools.end(), "sleep") != available_tools.end()) {
            std::cout << "测试sleep工具..." << std::endl;
            
            auto start_time = std::chrono::high_resolution_clock::now();
            auto response = service->callAITool("sleep", R"({"milliseconds": 2000})");
            auto end_time = std::chrono::high_resolution_clock::now();
            
            auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);
            std::cout << "执行时间: " << duration.count() << " 毫秒" << std::endl;
            
            std::cout << "请求ID: " << response.request_id << std::endl;
            std::cout << "是否错误: " << (response.is_error ? "是" : "否") << std::endl;
            
            if (response.is_error) {
                std::cout << "错误信息: " << response.error_message << std::endl;
            } else {
                std::cout << "结果: " << response.result << std::endl;
            }
        }
        
        // 测试weather工具
        if (std::find(available_tools.begin(), available_tools.end(), "get_weather") != available_tools.end()) {
            std::cout << "\n测试weather工具..." << std::endl;
            
            auto response = service->callAITool("get_weather", R"({
                "city": "北京",
                "latitude": "39.9042",
                "longitude": "116.4074"
            })");
            
            std::cout << "请求ID: " << response.request_id << std::endl;
            std::cout << "是否错误: " << (response.is_error ? "是" : "否") << std::endl;
            
            if (response.is_error) {
                std::cout << "错误信息: " << response.error_message << std::endl;
            } else {
                std::cout << "结果: " << response.result << std::endl;
            }
        }
    }
    
    printSeparator("服务器状态检查");
    
    // 检查服务器状态
    std::cout << "RPC服务器运行状态: " << (rpc_server.isRunning() ? "运行中" : "已停止") << std::endl;
    std::cout << "服务器地址: " << rpc_server.getAddress() << std::endl;
    
    // 获取AI接口
    auto ai_interface = rpc_server.getAIInterface();
    if (ai_interface) {
        std::cout << "AI接口状态: " << (ai_interface->isInitialized() ? "已初始化" : "未初始化") << std::endl;
    } else {
        std::cout << "AI接口状态: 不可用" << std::endl;
    }
    
    printSeparator("保持服务器运行");
    
    std::cout << "RPC服务器正在运行，按Ctrl+C停止..." << std::endl;
    std::cout << "可以通过gRPC客户端连接到: " << rpc_server.getAddress() << std::endl;
    
    // 保持服务器运行
    try {
        rpc_server.wait();
    } catch (const std::exception& e) {
        std::cerr << "服务器异常: " << e.what() << std::endl;
    }
    
    printSeparator("清理资源");
    
    // 停止服务器
    rpc_server.stop();
    std::cout << "✅ RPC服务器已停止" << std::endl;
    
    std::cout << "\n=== 集成测试完成 ===" << std::endl;
    return 0;
}


