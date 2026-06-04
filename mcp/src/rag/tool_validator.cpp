/**
 * @file tool_validator.cpp
 * @brief ToolValidator 实现
 * 
 * Requirements: 5.1, 5.2, 5.3, 5.4
 * Task 8: 实现 ToolValidator
 */

#include "agent_rpc/mcp/rag/tool_validator.h"
#include "agent_rpc/common/logger.h"

#include <nlohmann/json.hpp>
#include <chrono>
#include <future>

using json = nlohmann::json;

namespace agent_rpc {
namespace mcp {
namespace rag {

ToolValidator::ToolValidator(const ValidatorConfig& config)
    : config_(config) {
}

ValidationResult ToolValidator::validate(const RetrievedTool& tool) {
    ValidationResult result;
    result.tool_name = tool.name;
    
    auto start_time = std::chrono::steady_clock::now();
    
    if (!tool_call_func_) {
        // 没有设置工具调用函数，默认视为有效
        result.is_valid = true;
        return result;
    }
    
    try {
        // 生成测试查询
        auto test_queries = generateTestQueries(tool);
        
        if (test_queries.empty()) {
            // 无法生成测试查询，默认视为有效
            result.is_valid = true;
            return result;
        }
        
        // 执行测试查询（带超时）
        for (const auto& query : test_queries) {
            // 使用 async 实现超时
            auto future = std::async(std::launch::async, [this, &tool, &query]() {
                return executeTestQuery(tool.name, query);
            });
            
            auto status = future.wait_for(std::chrono::milliseconds(config_.timeout_ms));
            
            if (status == std::future_status::timeout) {
                LOG_WARN("Validation timeout for tool: " + tool.name);
                if (config_.treat_timeout_as_valid) {
                    result.is_valid = true;
                } else {
                    result.is_valid = false;
                    result.error_message = "Validation timeout";
                }
                break;
            }
            
            bool success = future.get();
            if (!success) {
                result.is_valid = false;
                result.error_message = "Test query failed";
                break;
            }
            
            result.is_valid = true;
        }
        
    } catch (const std::exception& e) {
        result.is_valid = false;
        result.error_message = e.what();
        LOG_ERROR("Validation error for tool " + tool.name + ": " + e.what());
    }
    
    auto end_time = std::chrono::steady_clock::now();
    result.duration_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        end_time - start_time).count();
    
    return result;
}

std::vector<ValidationResult> ToolValidator::validateBatch(
    const std::vector<RetrievedTool>& tools) {
    
    std::vector<ValidationResult> results;
    results.reserve(tools.size());
    
    for (const auto& tool : tools) {
        results.push_back(validate(tool));
    }
    
    return results;
}

std::vector<RetrievedTool> ToolValidator::filterInvalid(
    const std::vector<RetrievedTool>& tools) {
    
    auto validation_results = validateBatch(tools);
    
    std::vector<RetrievedTool> valid_tools;
    
    for (size_t i = 0; i < tools.size(); ++i) {
        if (validation_results[i].is_valid) {
            valid_tools.push_back(tools[i]);
        } else {
            LOG_INFO("Filtered out invalid tool: " + tools[i].name + 
                    " - " + validation_results[i].error_message);
        }
    }
    
    return valid_tools;
}

std::vector<std::string> ToolValidator::generateTestQueries(const RetrievedTool& tool) {
    std::vector<std::string> queries;
    
    // 尝试从 input_schema 生成测试查询
    if (!tool.input_schema.empty()) {
        try {
            json schema = json::parse(tool.input_schema);
            
            // 生成一个简单的测试参数
            json test_params = json::object();
            
            if (schema.contains("properties")) {
                for (auto& [key, value] : schema["properties"].items()) {
                    // 根据类型生成测试值
                    std::string type = value.value("type", "string");
                    
                    if (type == "string") {
                        test_params[key] = "test";
                    } else if (type == "number" || type == "integer") {
                        test_params[key] = 1;
                    } else if (type == "boolean") {
                        test_params[key] = true;
                    } else if (type == "array") {
                        test_params[key] = json::array();
                    } else if (type == "object") {
                        test_params[key] = json::object();
                    }
                    
                    // 限制参数数量
                    if (test_params.size() >= 3) {
                        break;
                    }
                }
            }
            
            if (!test_params.empty()) {
                queries.push_back(test_params.dump());
            }
            
        } catch (...) {
            // 解析失败，使用空参数
            queries.push_back("{}");
        }
    } else {
        // 没有 schema，使用空参数
        queries.push_back("{}");
    }
    
    // 限制查询数量
    if (static_cast<int>(queries.size()) > config_.max_test_queries) {
        queries.resize(config_.max_test_queries);
    }
    
    return queries;
}

bool ToolValidator::executeTestQuery(const std::string& tool_name, const std::string& query) {
    if (!tool_call_func_) {
        return true;
    }
    
    try {
        auto result = tool_call_func_(tool_name, query);
        
        // 只要不是严重错误就认为有效
        // 参数错误等可能是测试查询不完整导致的
        return result.success || 
               result.error.find("parameter") != std::string::npos ||
               result.error.find("argument") != std::string::npos;
               
    } catch (...) {
        return false;
    }
}

} // namespace rag
} // namespace mcp
} // namespace agent_rpc
