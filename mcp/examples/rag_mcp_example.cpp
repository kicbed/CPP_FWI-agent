/**
 * @file rag_mcp_example.cpp
 * @brief RAG-MCP 框架使用示例
 * 
 * 演示如何使用 RAG-MCP 框架进行智能工具选择。
 * 
 * 编译: cmake --build build --target rag_mcp_example
 * 运行: ./build/examples/rag_mcp_example [--mcp-server <path>] [--enable-rag]
 */

#include "agent_rpc/mcp/mcp_agent_integration.h"

#include <iostream>
#include <string>
#include <vector>

using namespace agent_rpc::mcp;

void printUsage(const char* program) {
    std::cout << "Usage: " << program << " [options]\n"
              << "Options:\n"
              << "  --mcp-server <path>   MCP Server executable path\n"
              << "  --enable-rag          Enable RAG-MCP for intelligent tool selection\n"
              << "  --top-k <n>           Number of tools to retrieve (default: 5)\n"
              << "  --threshold <f>       Similarity threshold (default: 0.3)\n"
              << "  --help                Show this help message\n";
}

int main(int argc, char* argv[]) {
    
    // 解析命令行参数
    MCPAgentConfig config;
    config.enable_mcp = false;
    config.rag_config.enabled = false;
    config.rag_config.top_k = 5;
    config.rag_config.similarity_threshold = 0.3f;
    
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--help") {
            printUsage(argv[0]);
            return 0;
        } else if (arg == "--mcp-server" && i + 1 < argc) {
            config.mcp_server_path = argv[++i];
            config.enable_mcp = true;
        } else if (arg == "--enable-rag") {
            config.rag_config.enabled = true;
        } else if (arg == "--top-k" && i + 1 < argc) {
            config.rag_config.top_k = std::stoi(argv[++i]);
        } else if (arg == "--threshold" && i + 1 < argc) {
            config.rag_config.similarity_threshold = std::stof(argv[++i]);
        }
    }
    
    std::cout << "=== RAG-MCP Framework Example ===\n\n";
    
    // 创建 MCPAgentIntegration 实例
    MCPAgentIntegration integration;
    
    // 初始化
    std::cout << "Initializing MCPAgentIntegration...\n";
    std::cout << "  MCP Enabled: " << (config.enable_mcp ? "Yes" : "No") << "\n";
    std::cout << "  RAG Enabled: " << (config.rag_config.enabled ? "Yes" : "No") << "\n";
    
    if (!integration.initialize(config)) {
        std::cerr << "Failed to initialize MCPAgentIntegration\n";
        return 1;
    }
    
    std::cout << "  Initialized: " << (integration.isInitialized() ? "Yes" : "No") << "\n";
    std::cout << "  Available: " << (integration.isAvailable() ? "Yes" : "No") << "\n";
    std::cout << "  RAG Active: " << (integration.isRAGEnabled() ? "Yes" : "No") << "\n\n";
    
    // 获取可用工具列表
    auto tools = integration.getAvailableTools();
    std::cout << "Available Tools (" << tools.size() << "):\n";
    for (const auto& tool : tools) {
        std::cout << "  - " << tool.name << ": " << tool.description << "\n";
    }
    std::cout << "\n";
    
    // 演示智能工具选择
    std::vector<std::string> queries = {
        "计算 123 + 456 的结果",
        "今天北京的天气怎么样",
        "搜索关于人工智能的最新新闻",
        "发送一封邮件给张三"
    };
    
    std::cout << "=== Intelligent Tool Selection Demo ===\n\n";
    
    for (const auto& query : queries) {
        std::cout << "Query: \"" << query << "\"\n";
        
        // 获取相关工具
        auto relevant_tools = integration.getRelevantTools(query);
        
        std::cout << "Relevant Tools (" << relevant_tools.size() << "):\n";
        for (const auto& tool : relevant_tools) {
            std::cout << "  - " << tool.name << "\n";
        }
        
        // 获取 LLM 函数调用格式
        std::string functions_json = integration.getRelevantToolsAsJson(query);
        std::cout << "Functions JSON (truncated): " 
                  << functions_json.substr(0, std::min(size_t(100), functions_json.size()))
                  << (functions_json.size() > 100 ? "..." : "") << "\n\n";
    }
    
    // 演示工具调用（如果 MCP 可用）
    if (integration.isAvailable()) {
        std::cout << "=== Tool Call Demo ===\n\n";
        
        // 尝试调用计算器工具
        std::string tool_name = "calculator";
        std::string arguments = R"({"expression": "123 + 456"})";
        
        std::cout << "Calling tool: " << tool_name << "\n";
        std::cout << "Arguments: " << arguments << "\n";
        
        auto result = integration.callTool(tool_name, arguments);
        
        if (result.success) {
            std::cout << "Result: " << result.result << "\n";
        } else {
            std::cout << "Error: " << result.error << "\n";
        }
        std::cout << "Duration: " << result.duration_ms << "ms\n\n";
    }
    
    // 关闭
    std::cout << "Shutting down...\n";
    integration.shutdown();
    
    std::cout << "Done.\n";
    return 0;
}
