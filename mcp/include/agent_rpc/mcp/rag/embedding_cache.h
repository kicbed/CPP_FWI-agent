/**
 * @file embedding_cache.h
 * @brief LRU 缓存，避免重复 API 调用
 * 
 * Requirements: 7.1, 7.2, 7.3, 7.4
 * Task 3: 实现 EmbeddingCache
 */

#pragma once

#include <string>
#include <vector>
#include <optional>
#include <unordered_map>
#include <list>
#include <mutex>
#include <chrono>

namespace agent_rpc {
namespace mcp {
namespace rag {

/**
 * @brief 缓存配置
 */
struct CacheConfig {
    size_t max_size = 1000;           ///< 最大缓存条目
    int ttl_seconds = 3600;           ///< 缓存过期时间 (秒)
    bool enabled = true;              ///< 是否启用缓存
};

/**
 * @brief 缓存统计信息
 */
struct CacheStats {
    size_t hits = 0;                  ///< 缓存命中次数
    size_t misses = 0;                ///< 缓存未命中次数
    size_t evictions = 0;             ///< 驱逐次数
    size_t size = 0;                  ///< 当前缓存大小
    
    float hitRate() const {
        size_t total = hits + misses;
        return total > 0 ? static_cast<float>(hits) / total : 0.0f;
    }
};

/**
 * @brief LRU Embedding 缓存
 * 
 * 使用 LRU (Least Recently Used) 策略管理缓存。
 * 支持：
 * - 可配置的最大缓存大小
 * - TTL 过期机制
 * - 线程安全
 */
class EmbeddingCache {
public:
    explicit EmbeddingCache(const CacheConfig& config);
    ~EmbeddingCache() = default;
    
    // 禁止拷贝
    EmbeddingCache(const EmbeddingCache&) = delete;
    EmbeddingCache& operator=(const EmbeddingCache&) = delete;
    
    /**
     * @brief 获取缓存的向量
     * @param text 文本键
     * @return 如果存在且未过期返回向量，否则返回 nullopt
     */
    std::optional<std::vector<float>> get(const std::string& text);
    
    /**
     * @brief 存入缓存
     * @param text 文本键
     * @param embedding 向量值
     */
    void put(const std::string& text, const std::vector<float>& embedding);
    
    /**
     * @brief 检查键是否存在（不更新 LRU 顺序）
     */
    bool contains(const std::string& text) const;
    
    /**
     * @brief 移除指定键
     * @return 如果键存在并被移除返回 true
     */
    bool remove(const std::string& text);
    
    /**
     * @brief 清空缓存
     */
    void clear();
    
    /**
     * @brief 获取当前缓存大小
     */
    size_t size() const;
    
    /**
     * @brief 获取缓存统计
     */
    CacheStats getStats() const;
    
    /**
     * @brief 重置统计信息
     */
    void resetStats();
    
    /**
     * @brief 获取配置
     */
    const CacheConfig& getConfig() const { return config_; }
    
    /**
     * @brief 获取最近被驱逐的键（用于测试）
     */
    std::string getLastEvictedKey() const { return last_evicted_key_; }

private:
    struct CacheEntry {
        std::vector<float> embedding;
        std::chrono::steady_clock::time_point created_at;
    };
    
    using CacheList = std::list<std::pair<std::string, CacheEntry>>;
    using CacheMap = std::unordered_map<std::string, CacheList::iterator>;
    
    /**
     * @brief 检查条目是否过期
     */
    bool isExpired(const CacheEntry& entry) const;
    
    /**
     * @brief 驱逐最久未使用的条目
     */
    void evictLRU();
    
    /**
     * @brief 移动条目到列表头部（最近使用）
     */
    void moveToFront(CacheMap::iterator it);
    
    CacheConfig config_;
    CacheList cache_list_;            ///< 双向链表，头部是最近使用
    CacheMap cache_map_;              ///< 哈希表，快速查找
    mutable std::mutex mutex_;
    
    // 统计信息
    mutable CacheStats stats_;
    std::string last_evicted_key_;
};

} // namespace rag
} // namespace mcp
} // namespace agent_rpc
