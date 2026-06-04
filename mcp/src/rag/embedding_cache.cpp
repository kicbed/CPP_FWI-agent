/**
 * @file embedding_cache.cpp
 * @brief EmbeddingCache 实现
 * 
 * Requirements: 7.1, 7.2, 7.3, 7.4
 * Task 3: 实现 EmbeddingCache
 */

#include "agent_rpc/mcp/rag/embedding_cache.h"

namespace agent_rpc {
namespace mcp {
namespace rag {

EmbeddingCache::EmbeddingCache(const CacheConfig& config)
    : config_(config) {
}

std::optional<std::vector<float>> EmbeddingCache::get(const std::string& text) {
    if (!config_.enabled) {
        stats_.misses++;
        return std::nullopt;
    }
    
    std::lock_guard<std::mutex> lock(mutex_);
    
    auto it = cache_map_.find(text);
    if (it == cache_map_.end()) {
        stats_.misses++;
        return std::nullopt;
    }
    
    // 检查是否过期
    if (isExpired(it->second->second)) {
        // 移除过期条目
        cache_list_.erase(it->second);
        cache_map_.erase(it);
        stats_.misses++;
        return std::nullopt;
    }
    
    // 移动到头部（最近使用）
    moveToFront(it);
    
    stats_.hits++;
    return it->second->second.embedding;
}

void EmbeddingCache::put(const std::string& text, const std::vector<float>& embedding) {
    if (!config_.enabled) {
        return;
    }
    
    std::lock_guard<std::mutex> lock(mutex_);
    
    auto it = cache_map_.find(text);
    if (it != cache_map_.end()) {
        // 更新现有条目
        it->second->second.embedding = embedding;
        it->second->second.created_at = std::chrono::steady_clock::now();
        moveToFront(it);
        return;
    }
    
    // 如果缓存已满，驱逐最久未使用的条目
    while (cache_list_.size() >= config_.max_size && !cache_list_.empty()) {
        evictLRU();
    }
    
    // 添加新条目到头部
    CacheEntry entry;
    entry.embedding = embedding;
    entry.created_at = std::chrono::steady_clock::now();
    
    cache_list_.push_front({text, entry});
    cache_map_[text] = cache_list_.begin();
    stats_.size = cache_list_.size();
}

bool EmbeddingCache::contains(const std::string& text) const {
    if (!config_.enabled) {
        return false;
    }
    
    std::lock_guard<std::mutex> lock(mutex_);
    
    auto it = cache_map_.find(text);
    if (it == cache_map_.end()) {
        return false;
    }
    
    // 检查是否过期
    return !isExpired(it->second->second);
}

bool EmbeddingCache::remove(const std::string& text) {
    std::lock_guard<std::mutex> lock(mutex_);
    
    auto it = cache_map_.find(text);
    if (it == cache_map_.end()) {
        return false;
    }
    
    cache_list_.erase(it->second);
    cache_map_.erase(it);
    stats_.size = cache_list_.size();
    return true;
}

void EmbeddingCache::clear() {
    std::lock_guard<std::mutex> lock(mutex_);
    
    cache_list_.clear();
    cache_map_.clear();
    stats_.size = 0;
}

size_t EmbeddingCache::size() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return cache_list_.size();
}

CacheStats EmbeddingCache::getStats() const {
    std::lock_guard<std::mutex> lock(mutex_);
    stats_.size = cache_list_.size();
    return stats_;
}

void EmbeddingCache::resetStats() {
    std::lock_guard<std::mutex> lock(mutex_);
    stats_.hits = 0;
    stats_.misses = 0;
    stats_.evictions = 0;
    stats_.size = cache_list_.size();
}

bool EmbeddingCache::isExpired(const CacheEntry& entry) const {
    if (config_.ttl_seconds <= 0) {
        return false;  // TTL 为 0 表示永不过期
    }
    
    auto now = std::chrono::steady_clock::now();
    auto age = std::chrono::duration_cast<std::chrono::seconds>(
        now - entry.created_at).count();
    
    return age >= config_.ttl_seconds;
}

void EmbeddingCache::evictLRU() {
    if (cache_list_.empty()) {
        return;
    }
    
    // 移除尾部（最久未使用）
    auto& back = cache_list_.back();
    last_evicted_key_ = back.first;
    cache_map_.erase(back.first);
    cache_list_.pop_back();
    
    stats_.evictions++;
    stats_.size = cache_list_.size();
}

void EmbeddingCache::moveToFront(CacheMap::iterator it) {
    // 将条目移动到列表头部
    cache_list_.splice(cache_list_.begin(), cache_list_, it->second);
}

} // namespace rag
} // namespace mcp
} // namespace agent_rpc
