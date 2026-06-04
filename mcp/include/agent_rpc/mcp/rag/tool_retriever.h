/**
 * @file tool_retriever.h
 * @brief 工具检索器，整合 EmbeddingService 和 VectorIndex
 * 
 * Requirements: 1.1, 1.3, 1.4, 4.1, 4.2, 4.3, 5.1
 * Task 7: 实现 ToolRetriever
 */

#pragma once

#include "embedding_service.h"
#include "embedding_cache.h"
#include "vector_index.h"
#include "../mcp_agent_integration.h"

#include <string>
#include <vector>
#include <memory>
#include <functional>

namespace agent_rpc {
namespace mcp {
namespace rag {

/**
 * @brief 检索器配置
 */
struct RetrieverConfig {
    EmbeddingConfig embedding_config;     ///< Embedding 服务配置
    CacheConfig cache_config;             ///< 缓存配置
    int top_k = 5;                        ///< 返回工具数量
    float similarity_threshold = 0.3f;    ///< 相似度阈值
    bool enable_validation = false;       ///< 是否启用验证
    int validation_timeout_ms = 5000;     ///< 验证超时
    std::string index_path;               ///< 索引文件路径
    bool auto_save_index = true;          ///< 是否自动保存索引
};

/**
 * @brief 检索到的工具
 */
struct RetrievedTool {
    std::string name;                     ///< 工具名称
    std::string description;              ///< 工具描述
    std::string input_schema;             ///< 输入参数 JSON Schema
    float relevance_score;                ///< 相关性分数
};

/**
 * @brief 工具检索器
 * 
 * 整合 EmbeddingService、EmbeddingCache 和 VectorIndex，
 * 提供完整的工具检索功能。
 * 
 * 工作流程：
 * 1. 接收用户查询
 * 2. 检查缓存是否有查询向量
 * 3. 如果没有，调用 EmbeddingService 生成向量
 * 4. 在 VectorIndex 中搜索最相似的工具
 * 5. 可选：验证工具兼容性
 * 6. 返回检索结果
 */
class ToolRetriever {
public:
    explicit ToolRetriever(const RetrieverConfig& config);
    ~ToolRetriever();
    
    // 禁止拷贝
    ToolRetriever(const ToolRetriever&) = delete;
    ToolRetriever& operator=(const ToolRetriever&) = delete;
    
    // ========================================================================
    // 生命周期管理
    // ========================================================================
    
    /**
     * @brief 初始化检索器
     * @return 如果初始化成功返回 true
     * 
     * 会尝试从 index_path 加载索引，如果文件不存在则创建空索引。
     */
    bool initialize();
    
    /**
     * @brief 关闭检索器
     * 
     * 如果 auto_save_index 为 true，会保存索引到文件。
     */
    void shutdown();
    
    /**
     * @brief 检查是否已初始化
     */
    bool isInitialized() const { return initialized_; }
    
    // ========================================================================
    // 工具索引
    // ========================================================================
    
    /**
     * @brief 索引 MCP 工具
     * @param tools 工具列表
     * 
     * 会为每个工具生成向量并添加到索引。
     */
    void indexTools(const std::vector<ToolInfo>& tools);
    
    /**
     * @brief 添加单个工具到索引
     */
    void addTool(const ToolInfo& tool);
    
    /**
     * @brief 从索引移除工具
     */
    bool removeTool(const std::string& tool_name);
    
    /**
     * @brief 刷新索引（重新生成所有向量）
     */
    void refreshIndex();
    
    /**
     * @brief 保存索引到文件
     */
    bool saveIndex();
    
    /**
     * @brief 加载索引从文件
     */
    bool loadIndex();
    
    // ========================================================================
    // 工具检索
    // ========================================================================
    
    /**
     * @brief 检索相关工具
     * @param query 用户查询
     * @return 检索到的工具列表，按相关性降序排列
     */
    std::vector<RetrievedTool> retrieve(const std::string& query);
    
    /**
     * @brief 检索相关工具（自定义 top_k）
     */
    std::vector<RetrievedTool> retrieve(const std::string& query, int top_k);
    
    /**
     * @brief 获取所有工具（不进行检索）
     */
    std::vector<RetrievedTool> getAllTools() const;
    
    // ========================================================================
    // 格式转换
    // ========================================================================
    
    /**
     * @brief 将检索结果转换为 LLM 函数调用格式
     * @param tools 检索到的工具
     * @return JSON 格式的函数定义
     */
    static std::string toFunctionCallingFormat(const std::vector<RetrievedTool>& tools);
    
    // ========================================================================
    // 状态和配置
    // ========================================================================
    
    /**
     * @brief 获取索引大小
     */
    size_t getIndexSize() const;
    
    /**
     * @brief 获取缓存统计
     */
    CacheStats getCacheStats() const;
    
    /**
     * @brief 获取配置
     */
    const RetrieverConfig& getConfig() const { return config_; }

private:
    /**
     * @brief 获取文本的向量（带缓存）
     */
    std::vector<float> getEmbedding(const std::string& text);
    
    /**
     * @brief 构建工具的文本表示（用于向量化）
     */
    static std::string buildToolText(const ToolInfo& tool);
    
    RetrieverConfig config_;
    std::unique_ptr<EmbeddingService> embedding_service_;
    std::unique_ptr<EmbeddingCache> cache_;
    std::unique_ptr<VectorIndex> index_;
    bool initialized_ = false;
};

/**
 * @brief RAG-MCP 集成配置
 */
struct RAGMCPConfig {
    bool enabled = false;                 ///< 是否启用 RAG-MCP
    RetrieverConfig retriever_config;     ///< 检索器配置
};

} // namespace rag
} // namespace mcp
} // namespace agent_rpc
