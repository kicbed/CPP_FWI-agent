/**
 * @file vector_index.h
 * @brief 内存向量索引，支持快速相似度搜索
 * 
 * Requirements: 1.3, 1.4, 3.1, 3.2, 3.3, 3.4
 * Task 5: 实现 VectorIndex
 */

#pragma once

#include <string>
#include <vector>
#include <unordered_map>
#include <mutex>
#include <cstdint>

namespace agent_rpc {
namespace mcp {
namespace rag {

/**
 * @brief 索引中的工具记录
 */
struct IndexedTool {
    std::string name;                 ///< 工具名称
    std::string description;          ///< 工具描述
    std::string input_schema;         ///< 输入参数 JSON Schema
    std::vector<float> embedding;     ///< 向量表示
    int64_t created_at = 0;           ///< 创建时间戳
    int64_t updated_at = 0;           ///< 更新时间戳
};

/**
 * @brief 搜索结果
 */
struct SearchResult {
    IndexedTool tool;                 ///< 工具信息
    float similarity;                 ///< 相似度分数 [0, 1]
};

/**
 * @brief 向量索引类
 * 
 * 内存向量索引，支持：
 * - 工具的增删改查
 * - 基于余弦相似度的 Top-K 搜索
 * - 持久化到 JSON 文件
 * - 线程安全
 */
class VectorIndex {
public:
    VectorIndex() = default;
    ~VectorIndex() = default;
    
    // 禁止拷贝
    VectorIndex(const VectorIndex&) = delete;
    VectorIndex& operator=(const VectorIndex&) = delete;
    
    // ========================================================================
    // 工具管理
    // ========================================================================
    
    /**
     * @brief 添加工具到索引
     * @param tool 工具信息（必须包含 embedding）
     */
    void addTool(const IndexedTool& tool);
    
    /**
     * @brief 从索引移除工具
     * @param tool_name 工具名称
     * @return 如果工具存在并被移除返回 true
     */
    bool removeTool(const std::string& tool_name);
    
    /**
     * @brief 更新工具信息
     * @param tool 新的工具信息
     * @return 如果工具存在并被更新返回 true
     */
    bool updateTool(const IndexedTool& tool);
    
    /**
     * @brief 获取工具信息
     * @param tool_name 工具名称
     * @return 工具信息，如果不存在返回 nullptr
     */
    const IndexedTool* getTool(const std::string& tool_name) const;
    
    /**
     * @brief 检查工具是否存在
     */
    bool hasTool(const std::string& tool_name) const;
    
    /**
     * @brief 获取所有工具
     */
    std::vector<IndexedTool> getAllTools() const;
    
    // ========================================================================
    // 搜索
    // ========================================================================
    
    /**
     * @brief 搜索最相似的 K 个工具
     * @param query_embedding 查询向量
     * @param top_k 返回数量
     * @param threshold 相似度阈值 (0-1)，低于此值的结果会被过滤
     * @return 搜索结果，按相似度降序排列
     */
    std::vector<SearchResult> search(
        const std::vector<float>& query_embedding,
        int top_k,
        float threshold = 0.0f) const;
    
    /**
     * @brief 计算余弦相似度
     */
    static float cosineSimilarity(
        const std::vector<float>& a,
        const std::vector<float>& b);
    
    // ========================================================================
    // 持久化
    // ========================================================================
    
    /**
     * @brief 保存索引到 JSON 文件
     * @param path 文件路径
     * @return 如果保存成功返回 true
     */
    bool saveToFile(const std::string& path) const;
    
    /**
     * @brief 从 JSON 文件加载索引
     * @param path 文件路径
     * @return 如果加载成功返回 true
     */
    bool loadFromFile(const std::string& path);
    
    // ========================================================================
    // 状态
    // ========================================================================
    
    /**
     * @brief 获取索引大小
     */
    size_t size() const;
    
    /**
     * @brief 清空索引
     */
    void clear();
    
    /**
     * @brief 获取索引版本
     */
    std::string getVersion() const { return version_; }
    
    /**
     * @brief 设置索引版本
     */
    void setVersion(const std::string& version) { version_ = version; }

private:
    std::unordered_map<std::string, IndexedTool> tools_;
    mutable std::mutex mutex_;
    std::string version_ = "1.0";
};

} // namespace rag
} // namespace mcp
} // namespace agent_rpc
