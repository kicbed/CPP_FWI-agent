#include <iostream>
#include <string>
#include <vector>
#include <chrono>
#include <thread>
#include "agent_rpc/ai/ai_interface.h"
#include "agent_rpc/common/logger.h"

using namespace agent_rpc::ai;
using namespace agent_rpc::common;

void printSeparator(const std::string& title) {
    std::cout << "\n" << std::string(60, '=') << std::endl;
    std::cout << " " << title << std::endl;
    std::cout << std::string(60, '=') << std::endl;
}

void printToolResponse(const AIToolResponse& response) {
    std::cout << "请求ID: " << response.request_id << std::endl;
    std::cout << "是否错误: " << (response.is_error ? "是" : "否") << std::endl;
    
    if (response.is_error) {
        std::cout << "错误信息: " << response.error_message << std::endl;
    } else {
        std::cout << "结果: " << response.result << std::endl;
    }
    
    if (!response.metadata.empty()) {
        std::cout << "元数据:" << std::endl;
        for (const auto& pair : response.metadata) {
            std::cout << "  " << pair.first << ": " << pair.second << std::endl;
        }
    }
}

int main() {
    std::cout << "=== AI-MCP集成示例 ===" << std::endl;
    
    // 创建AI服务代理
    AIServiceProxy ai_proxy;
    
    printSeparator("初始化AI服务");
    
    // 初始化AI服务
    if (!ai_proxy.initialize("/root/mcp_server/build/mcp_server", 
                            {"-n", "ai-integration-server", 
                             "-l", "/tmp/mcp_logs", 
                             "-p", "/root/mcp_server/plugins"})) {
        std::cerr << "❌ 初始化AI服务失败!" << std::endl;
        return 1;
    }
    
    std::cout << "✅ AI服务初始化成功!" << std::endl;
    
    // 获取AI接口
    auto ai_interface = ai_proxy.getAIInterface();
    if (!ai_interface) {
        std::cerr << "❌ 获取AI接口失败!" << std::endl;
        return 1;
    }
    
    printSeparator("获取可用工具");
    
    // 获取可用工具
    auto available_tools = ai_interface->getAvailableTools();
    std::cout << "可用工具数量: " << available_tools.size() << std::endl;
    
    for (const auto& tool : available_tools) {
        std::cout << "  - " << tool << std::endl;
    }
    
    if (available_tools.empty()) {
        std::cout << "⚠️  没有可用的工具，请检查MCP服务器是否正常运行" << std::endl;
        return 1;
    }
    
    printSeparator("测试工具调用");
    
    // 测试1: 调用sleep工具
    if (std::find(available_tools.begin(), available_tools.end(), "sleep") != available_tools.end()) {
        std::cout << "测试1: 调用sleep工具" << std::endl;
        
        AIToolRequest sleep_request;
        sleep_request.tool_name = "sleep";
        sleep_request.arguments = R"({"milliseconds": 2000})";
        sleep_request.request_id = "sleep_test_001";
        sleep_request.metadata["test_type"] = "sleep_test";
        
        auto start_time = std::chrono::high_resolution_clock::now();
        AIToolResponse sleep_response = ai_interface->callTool(sleep_request);
        auto end_time = std::chrono::high_resolution_clock::now();
        
        auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);
        std::cout << "执行时间: " << duration.count() << " 毫秒" << std::endl;
        
        printToolResponse(sleep_response);
    }
    
    // 测试2: 调用weather工具
    if (std::find(available_tools.begin(), available_tools.end(), "get_weather") != available_tools.end()) {
        std::cout << "\n测试2: 调用weather工具" << std::endl;
        
        AIToolRequest weather_request;
        weather_request.tool_name = "get_weather";
        weather_request.arguments = R"({
            "city": "北京",
            "latitude": "39.9042",
            "longitude": "116.4074"
        })";
        weather_request.request_id = "weather_test_001";
        weather_request.metadata["test_type"] = "weather_test";
        weather_request.metadata["location"] = "北京";
        
        AIToolResponse weather_response = ai_interface->callTool(weather_request);
        printToolResponse(weather_response);
    }
    
    // 测试3: 异步工具调用
    if (std::find(available_tools.begin(), available_tools.end(), "sleep") != available_tools.end()) {
        std::cout << "\n测试3: 异步调用sleep工具" << std::endl;
        
        AIToolRequest async_request;
        async_request.tool_name = "sleep";
        async_request.arguments = R"({"milliseconds": 1500})";
        async_request.request_id = "async_sleep_test_001";
        async_request.metadata["test_type"] = "async_test";
        
        std::cout << "开始异步调用..." << std::endl;
        auto async_start = std::chrono::high_resolution_clock::now();
        
        ai_interface->callToolAsync(async_request, [async_start](const AIToolResponse& response) {
            auto async_end = std::chrono::high_resolution_clock::now();
            auto async_duration = std::chrono::duration_cast<std::chrono::milliseconds>(async_end - async_start);
            
            std::cout << "异步调用完成，执行时间: " << async_duration.count() << " 毫秒" << std::endl;
            printToolResponse(response);
        });
        
        // 等待异步调用完成
        std::this_thread::sleep_for(std::chrono::seconds(3));
    }
    
    // 测试4: 错误处理
    std::cout << "\n测试4: 错误处理测试" << std::endl;
    
    AIToolRequest error_request;
    error_request.tool_name = "nonexistent_tool";
    error_request.arguments = R"({})";
    error_request.request_id = "error_test_001";
    
    AIToolResponse error_response = ai_interface->callTool(error_request);
    printToolResponse(error_response);
    
    // 测试5: 参数验证
    if (std::find(available_tools.begin(), available_tools.end(), "get_weather") != available_tools.end()) {
        std::cout << "\n测试5: 参数验证测试" << std::endl;
        
        AIToolRequest invalid_request;
        invalid_request.tool_name = "get_weather";
        invalid_request.arguments = R"({"invalid_param": "test"})";  // 缺少必需参数
        invalid_request.request_id = "validation_test_001";
        
        AIToolResponse validation_response = ai_interface->callTool(invalid_request);
        printToolResponse(validation_response);
    }
    
    printSeparator("服务状态检查");
    
    // 检查服务状态
    std::cout << "AI服务可用: " << (ai_proxy.isServiceAvailable() ? "是" : "否") << std::endl;
    std::cout << "AI接口初始化: " << (ai_interface->isInitialized() ? "是" : "否") << std::endl;
    
    auto services = ai_proxy.getAvailableServices();
    std::cout << "可用服务数量: " << services.size() << std::endl;
    for (const auto& service : services) {
        std::cout << "  - " << service << std::endl;
    }
    
    printSeparator("清理资源");
    
    // 关闭服务
    ai_proxy.shutdown();
    std::cout << "✅ AI服务已关闭" << std::endl;
    
    std::cout << "\n=== 集成测试完成 ===" << std::endl;
    return 0;
}


