/**
 * @file vector_index.cpp
 * @brief VectorIndex 实现
 * 
 * Requirements: 1.3, 1.4, 3.1, 3.2, 3.3, 3.4
 * Task 5: 实现 VectorIndex
 */

#include "agent_rpc/mcp/rag/vector_index.h"
#include "agent_rpc/common/logger.h"

#include <nlohmann/json.hpp>
#include <fstream>
#include <algorithm>
#include <cmath>
#include <chrono>

using json = nlohmann::json;

namespace agent_rpc {
namespace mcp {
namespace rag {

void VectorIndex::addTool(const IndexedTool& tool) {
    std::lock_guard<std::mutex> lock(mutex_);
    
    IndexedTool new_tool = tool;
    auto now = std::chrono::system_clock::now().time_since_epoch();
    new_tool.created_at = std::chrono::duration_cast<std::chrono::seconds>(now).count();
    new_tool.updated_at = new_tool.created_at;
    
    tools_[tool.name] = new_tool;
}

bool VectorIndex::removeTool(const std::string& tool_name) {
    std::lock_guard<std::mutex> lock(mutex_);
    return tools_.erase(tool_name) > 0;
}

bool VectorIndex::updateTool(const IndexedTool& tool) {
    std::lock_guard<std::mutex> lock(mutex_);
    
    auto it = tools_.find(tool.name);
    if (it == tools_.end()) {
        return false;
    }
    
    IndexedTool updated_tool = tool;
    updated_tool.created_at = it->second.created_at;
    auto now = std::chrono::system_clock::now().time_since_epoch();
    updated_tool.updated_at = std::chrono::duration_cast<std::chrono::seconds>(now).count();
    
    it->second = updated_tool;
    return true;
}

const IndexedTool* VectorIndex::getTool(const std::string& tool_name) const {
    std::lock_guard<std::mutex> lock(mutex_);
    
    auto it = tools_.find(tool_name);
    if (it == tools_.end()) {
        return nullptr;
    }
    return &it->second;
}

bool VectorIndex::hasTool(const std::string& tool_name) const {
    std::lock_guard<std::mutex> lock(mutex_);
    return tools_.find(tool_name) != tools_.end();
}

std::vector<IndexedTool> VectorIndex::getAllTools() const {
    std::lock_guard<std::mutex> lock(mutex_);
    
    std::vector<IndexedTool> result;
    result.reserve(tools_.size());
    for (const auto& pair : tools_) {
        result.push_back(pair.second);
    }
    return result;
}

std::vector<SearchResult> VectorIndex::search(
    const std::vector<float>& query_embedding,
    int top_k,
    float threshold) const {
    
    std::lock_guard<std::mutex> lock(mutex_);
    
    if (tools_.empty() || query_embedding.empty()) {
        return {};
    }
    
    // 计算所有工具的相似度
    std::vector<SearchResult> all_results;
    all_results.reserve(tools_.size());
    
    for (const auto& pair : tools_) {
        const IndexedTool& tool = pair.second;
        if (tool.embedding.empty()) {
            continue;
        }
        
        float similarity = cosineSimilarity(query_embedding, tool.embedding);
        
        SearchResult result;
        result.tool = tool;
        result.similarity = similarity;
        all_results.push_back(result);
    }
    
    // 按相似度降序排序
    std::sort(all_results.begin(), all_results.end(),
        [](const SearchResult& a, const SearchResult& b) {
            return a.similarity > b.similarity;
        });
    
    // 应用阈值过滤
    std::vector<SearchResult> filtered_results;
    for (const auto& result : all_results) {
        if (result.similarity >= threshold) {
            filtered_results.push_back(result);
        }
    }
    
    // 如果没有结果满足阈值，返回最佳匹配
    if (filtered_results.empty() && !all_results.empty()) {
        filtered_results.push_back(all_results[0]);
    }
    
    // 限制返回数量
    if (static_cast<int>(filtered_results.size()) > top_k) {
        filtered_results.resize(top_k);
    }
    
    return filtered_results;
}

float VectorIndex::cosineSimilarity(
    const std::vector<float>& a,
    const std::vector<float>& b) {
    
    if (a.size() != b.size() || a.empty()) {
        return 0.0f;
    }
    
    float dot_product = 0.0f;
    float norm_a = 0.0f;
    float norm_b = 0.0f;
    
    for (size_t i = 0; i < a.size(); ++i) {
        dot_product += a[i] * b[i];
        norm_a += a[i] * a[i];
        norm_b += b[i] * b[i];
    }
    
    if (norm_a == 0.0f || norm_b == 0.0f) {
        return 0.0f;
    }
    
    return dot_product / (std::sqrt(norm_a) * std::sqrt(norm_b));
}

bool VectorIndex::saveToFile(const std::string& path) const {
    std::lock_guard<std::mutex> lock(mutex_);
    
    try {
        json tools_array = json::array();
        
        for (const auto& pair : tools_) {
            const IndexedTool& tool = pair.second;
            
            json tool_json = {
                {"name", tool.name},
                {"description", tool.description},
                {"input_schema", tool.input_schema},
                {"embedding", tool.embedding},
                {"created_at", tool.created_at},
                {"updated_at", tool.updated_at}
            };
            
            tools_array.push_back(tool_json);
        }
        
        json index_json = {
            {"version", version_},
            {"model", "text-embedding-v2"},
            {"dimension", tools_.empty() ? 1536 : 
                (tools_.begin()->second.embedding.empty() ? 1536 : 
                 static_cast<int>(tools_.begin()->second.embedding.size()))},
            {"tools", tools_array}
        };
        
        std::ofstream file(path);
        if (!file.is_open()) {
            LOG_ERROR("Failed to open file for writing: " + path);
            return false;
        }
        
        file << index_json.dump(2);
        file.close();
        
        LOG_INFO("Saved vector index to: " + path + " (" + 
                std::to_string(tools_.size()) + " tools)");
        return true;
        
    } catch (const std::exception& e) {
        LOG_ERROR("Failed to save vector index: " + std::string(e.what()));
        return false;
    }
}

bool VectorIndex::loadFromFile(const std::string& path) {
    std::lock_guard<std::mutex> lock(mutex_);
    
    try {
        std::ifstream file(path);
        if (!file.is_open()) {
            LOG_WARN("Index file not found: " + path);
            return false;
        }
        
        json index_json = json::parse(file);
        file.close();
        
        // 读取版本
        if (index_json.contains("version")) {
            version_ = index_json["version"].get<std::string>();
        }
        
        // 清空现有数据
        tools_.clear();
        
        // 加载工具
        if (index_json.contains("tools")) {
            for (const auto& tool_json : index_json["tools"]) {
                IndexedTool tool;
                tool.name = tool_json.value("name", "");
                tool.description = tool_json.value("description", "");
                tool.input_schema = tool_json.value("input_schema", "");
                tool.created_at = tool_json.value("created_at", 0);
                tool.updated_at = tool_json.value("updated_at", 0);
                
                if (tool_json.contains("embedding")) {
                    for (const auto& val : tool_json["embedding"]) {
                        tool.embedding.push_back(val.get<float>());
                    }
                }
                
                if (!tool.name.empty()) {
                    tools_[tool.name] = tool;
                }
            }
        }
        
        LOG_INFO("Loaded vector index from: " + path + " (" + 
                std::to_string(tools_.size()) + " tools)");
        return true;
        
    } catch (const std::exception& e) {
        LOG_ERROR("Failed to load vector index: " + std::string(e.what()));
        return false;
    }
}

size_t VectorIndex::size() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return tools_.size();
}

void VectorIndex::clear() {
    std::lock_guard<std::mutex> lock(mutex_);
    tools_.clear();
}

} // namespace rag
} // namespace mcp
} // namespace agent_rpc
