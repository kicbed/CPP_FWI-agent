/**
 * @file tool_retriever.cpp
 * @brief ToolRetriever 实现
 * 
 * Requirements: 1.1, 1.3, 1.4, 4.1, 4.2, 4.3, 5.1
 * Task 7: 实现 ToolRetriever
 */

#include "agent_rpc/mcp/rag/tool_retriever.h"
#include "agent_rpc/common/logger.h"

#include <nlohmann/json.hpp>
#include <sstream>

using json = nlohmann::json;

namespace agent_rpc {
namespace mcp {
namespace rag {

ToolRetriever::ToolRetriever(const RetrieverConfig& config)
    : config_(config) {
}

ToolRetriever::~ToolRetriever() {
    if (initialized_) {
        shutdown();
    }
}

bool ToolRetriever::initialize() {
    if (initialized_) {
        LOG_WARN("ToolRetriever already initialized");
        return true;
    }
    
    try {
        // 创建 Embedding 服务
        embedding_service_ = std::make_unique<EmbeddingService>(config_.embedding_config);
        
        // 创建缓存
        cache_ = std::make_unique<EmbeddingCache>(config_.cache_config);
        
        // 创建索引
        index_ = std::make_unique<VectorIndex>();
        
        // 尝试加载索引
        if (!config_.index_path.empty()) {
            if (!index_->loadFromFile(config_.index_path)) {
                LOG_INFO("Creating new vector index");
            }
        }
        
        initialized_ = true;
        LOG_INFO("ToolRetriever initialized successfully");
        return true;
        
    } catch (const std::exception& e) {
        LOG_ERROR("Failed to initialize ToolRetriever: " + std::string(e.what()));
        return false;
    }
}

void ToolRetriever::shutdown() {
    if (!initialized_) {
        return;
    }
    
    // 保存索引
    if (config_.auto_save_index && !config_.index_path.empty()) {
        saveIndex();
    }
    
    embedding_service_.reset();
    cache_.reset();
    index_.reset();
    
    initialized_ = false;
    LOG_INFO("ToolRetriever shutdown");
}

void ToolRetriever::indexTools(const std::vector<ToolInfo>& tools) {
    if (!initialized_) {
        LOG_ERROR("ToolRetriever not initialized");
        return;
    }
    
    LOG_INFO("Indexing " + std::to_string(tools.size()) + " tools");
    
    for (const auto& tool : tools) {
        addTool(tool);
    }
    
    LOG_INFO("Indexed " + std::to_string(index_->size()) + " tools");
}

void ToolRetriever::addTool(const ToolInfo& tool) {
    if (!initialized_) {
        LOG_ERROR("ToolRetriever not initialized");
        return;
    }
    
    try {
        // 构建工具文本
        std::string tool_text = buildToolText(tool);
        
        // 生成向量
        std::vector<float> embedding = getEmbedding(tool_text);
        
        // 添加到索引
        IndexedTool indexed_tool;
        indexed_tool.name = tool.name;
        indexed_tool.description = tool.description;
        indexed_tool.input_schema = tool.input_schema;
        indexed_tool.embedding = embedding;
        
        index_->addTool(indexed_tool);
        
        LOG_INFO("Added tool to index: " + tool.name);
        
    } catch (const std::exception& e) {
        LOG_ERROR("Failed to add tool " + tool.name + ": " + e.what());
    }
}

bool ToolRetriever::removeTool(const std::string& tool_name) {
    if (!initialized_) {
        return false;
    }
    return index_->removeTool(tool_name);
}

void ToolRetriever::refreshIndex() {
    if (!initialized_) {
        return;
    }
    
    // 获取所有工具
    auto tools = index_->getAllTools();
    
    // 清空索引
    index_->clear();
    
    // 重新索引
    for (const auto& tool : tools) {
        ToolInfo info;
        info.name = tool.name;
        info.description = tool.description;
        info.input_schema = tool.input_schema;
        addTool(info);
    }
}

bool ToolRetriever::saveIndex() {
    if (!initialized_ || config_.index_path.empty()) {
        return false;
    }
    return index_->saveToFile(config_.index_path);
}

bool ToolRetriever::loadIndex() {
    if (!initialized_ || config_.index_path.empty()) {
        return false;
    }
    return index_->loadFromFile(config_.index_path);
}

std::vector<RetrievedTool> ToolRetriever::retrieve(const std::string& query) {
    return retrieve(query, config_.top_k);
}

std::vector<RetrievedTool> ToolRetriever::retrieve(const std::string& query, int top_k) {
    if (!initialized_) {
        LOG_ERROR("ToolRetriever not initialized");
        return {};
    }
    
    if (index_->size() == 0) {
        LOG_WARN("Index is empty, returning empty results");
        return {};
    }
    
    try {
        // 获取查询向量
        std::vector<float> query_embedding = getEmbedding(query);
        
        // 搜索
        auto search_results = index_->search(
            query_embedding, 
            top_k, 
            config_.similarity_threshold);
        
        // 转换结果
        std::vector<RetrievedTool> results;
        results.reserve(search_results.size());
        
        for (const auto& sr : search_results) {
            RetrievedTool tool;
            tool.name = sr.tool.name;
            tool.description = sr.tool.description;
            tool.input_schema = sr.tool.input_schema;
            tool.relevance_score = sr.similarity;
            results.push_back(tool);
        }
        
        LOG_INFO("Retrieved " + std::to_string(results.size()) + 
                " tools for query: " + query.substr(0, 50) + "...");
        
        return results;
        
    } catch (const std::exception& e) {
        LOG_ERROR("Failed to retrieve tools: " + std::string(e.what()));
        return {};
    }
}

std::vector<RetrievedTool> ToolRetriever::getAllTools() const {
    if (!initialized_) {
        return {};
    }
    
    auto indexed_tools = index_->getAllTools();
    
    std::vector<RetrievedTool> results;
    results.reserve(indexed_tools.size());
    
    for (const auto& tool : indexed_tools) {
        RetrievedTool rt;
        rt.name = tool.name;
        rt.description = tool.description;
        rt.input_schema = tool.input_schema;
        rt.relevance_score = 1.0f;  // 全部返回时相关性为 1
        results.push_back(rt);
    }
    
    return results;
}

std::string ToolRetriever::toFunctionCallingFormat(const std::vector<RetrievedTool>& tools) {
    json functions = json::array();
    
    for (const auto& tool : tools) {
        json func = {
            {"name", tool.name},
            {"description", tool.description}
        };
        
        // 解析 input_schema
        if (!tool.input_schema.empty()) {
            try {
                func["parameters"] = json::parse(tool.input_schema);
            } catch (...) {
                func["parameters"] = json::object();
            }
        } else {
            func["parameters"] = json::object();
        }
        
        functions.push_back(func);
    }
    
    return functions.dump(2);
}

size_t ToolRetriever::getIndexSize() const {
    if (!initialized_) {
        return 0;
    }
    return index_->size();
}

CacheStats ToolRetriever::getCacheStats() const {
    if (!initialized_ || !cache_) {
        return CacheStats{};
    }
    return cache_->getStats();
}

std::vector<float> ToolRetriever::getEmbedding(const std::string& text) {
    // 先检查缓存
    if (cache_) {
        auto cached = cache_->get(text);
        if (cached.has_value()) {
            return cached.value();
        }
    }
    
    // 调用 API
    std::vector<float> embedding = embedding_service_->embed(text);
    
    // 存入缓存
    if (cache_) {
        cache_->put(text, embedding);
    }
    
    return embedding;
}

std::string ToolRetriever::buildToolText(const ToolInfo& tool) {
    std::ostringstream oss;
    oss << "Tool: " << tool.name << "\n";
    oss << "Description: " << tool.description << "\n";
    
    // 简化 input_schema 用于向量化
    if (!tool.input_schema.empty()) {
        try {
            json schema = json::parse(tool.input_schema);
            if (schema.contains("properties")) {
                oss << "Parameters: ";
                for (auto& [key, value] : schema["properties"].items()) {
                    oss << key;
                    if (value.contains("description")) {
                        oss << " (" << value["description"].get<std::string>() << ")";
                    }
                    oss << ", ";
                }
            }
        } catch (...) {
            // 忽略解析错误
        }
    }
    
    return oss.str();
}

} // namespace rag
} // namespace mcp
} // namespace agent_rpc
