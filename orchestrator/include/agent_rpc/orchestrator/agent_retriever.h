/**
 * @file agent_retriever.h
 * @brief AgentRetriever - recall candidate Agents based on query
 *
 * Implements the "R" (Retrieval) in Agent-RAG.
 * Retrieves topK candidate Agents from Registry based on query-AgentCard similarity.
 *
 * Similarity methods:
 * 1. Embedding similarity (using DashScope API) - preferred
 * 2. Keyword matching (fallback when no API key)
 *
 * Vector caching:
 * - AgentCard vectors are cached and only recomputed when Agent list changes
 * - Query vectors are cached using EmbeddingCache (LRU)
 * - This avoids repeated API calls
 */

#pragma once

#include <a2a/examples/agent_registry.hpp>
#include <a2a/examples/registry_client.hpp>
#include <agent_rpc/mcp/rag/embedding_service.h>
#include <agent_rpc/mcp/rag/local_embedding_service.h>
#include <agent_rpc/mcp/rag/embedding_cache.h>
#include "vector_store.h"
#include <string>
#include <vector>
#include <algorithm>
#include <sstream>
#include <unordered_set>
#include <map>

namespace agent_rpc {
namespace orchestrator {

/**
 * @brief Agent retrieval result
 */
struct AgentRetrievalResult {
    AgentRegistration agent;     // Agent registration info
    float relevance_score;       // Relevance score (0-1)
    std::string match_reason;    // Why this Agent was selected
};

/**
 * @brief Agent Retriever - recall candidate Agents
 *
 * Part of the Agent-RAG routing system.
 * Retrieves candidate Agents from Registry based on query similarity.
 *
 * Supports two modes:
 * 1. Embedding mode: Uses DashScope API for semantic similarity (preferred)
 * 2. Keyword mode: Uses keyword matching (fallback)
 */
class AgentRetriever {
public:
    /**
     * @brief Construct with Registry client
     * @param registry_client Registry client for querying Agents
     * @param embedding_provider Embedding provider: "local" or "dashscope"
     * @param api_key DashScope API key (only for dashscope provider)
     * @param local_embedding_url Local embedding server URL
     * @param store_dir Directory for vector persistence
     */
    explicit AgentRetriever(RegistryClient& registry_client,
                           const std::string& embedding_provider = "local",
                           const std::string& api_key = "",
                           const std::string& local_embedding_url = "http://localhost:6000",
                           const std::string& store_dir = "resources/embeddings")
        : registry_client_(registry_client)
        , use_embedding_(true)
        , embedding_provider_(embedding_provider)
        , vector_store_(store_dir) {

        if (embedding_provider == "local") {
            // 使用本地 Embedding 服务
            agent_rpc::mcp::rag::LocalEmbeddingConfig config;
            config.api_url = local_embedding_url;
            local_embedding_service_ = std::make_unique<agent_rpc::mcp::rag::LocalEmbeddingService>(config);
        } else if (embedding_provider == "dashscope" && !api_key.empty()) {
            // 使用 DashScope API
            agent_rpc::mcp::rag::EmbeddingConfig config;
            config.api_key = api_key;
            config.model = "text-embedding-v2";
            dashscope_embedding_service_ = std::make_unique<agent_rpc::mcp::rag::EmbeddingService>(config);
        } else {
            // 无 Embedding，使用关键词匹配
            use_embedding_ = false;
        }

        if (use_embedding_) {
            // Initialize cache for query vectors
            agent_rpc::mcp::rag::CacheConfig cache_config;
            cache_config.max_size = 100;  // Cache 100 query vectors
            cache_config.ttl_seconds = 3600;  // 1 hour TTL
            query_cache_ = std::make_unique<agent_rpc::mcp::rag::EmbeddingCache>(cache_config);

            // Load cached agent vectors from file
            vector_store_.load("agent_cards");
        }
    }

    /**
     * @brief 检查 Embedding 服务是否可用
     */
    bool isEmbeddingAvailable() const {
        if (!use_embedding_) return false;
        if (embedding_provider_ == "local" && local_embedding_service_) {
            return local_embedding_service_->isAvailable();
        }
        return dashscope_embedding_service_ != nullptr;
    }

    /**
     * @brief Retrieve candidate Agents
     * @param query User query
     * @param topK Maximum candidates to return
     * @return Sorted list of candidate Agents (most relevant first)
     *
     * Algorithm:
     * 1. Get all Agents from Registry
     * 2. Compute relevance score (embedding or keyword)
     * 3. Sort by score descending
     * 4. Return topK
     */
    std::vector<AgentRetrievalResult> retrieve(const std::string& query, int topK = 5) {
        // 1. Get all Agents from Registry
        auto agents = registry_client_.get_all_agents();

        // 2. Compute relevance score for each Agent
        std::vector<AgentRetrievalResult> results;

        if (use_embedding_ && (local_embedding_service_ || dashscope_embedding_service_)) {
            // Embedding mode: semantic similarity
            results = retrieve_with_embedding(query, agents);
        } else {
            // Keyword mode: keyword matching
            results = retrieve_with_keyword(query, agents);
        }

        // 3. Sort by score descending
        std::sort(results.begin(), results.end(),
                  [](const AgentRetrievalResult& a, const AgentRetrievalResult& b) {
                      return a.relevance_score > b.relevance_score;
                  });

        // 4. Return topK
        if (results.size() > static_cast<size_t>(topK)) {
            results.resize(topK);
        }

        return results;
    }

private:
    /**
     * @brief Retrieve using embedding similarity
     *
     * Caching strategy:
     * 1. AgentCard vectors: cached in agent_embeddings_, recomputed when Agent list changes
     * 2. Query vectors: cached in query_cache_ (LRU, 1 hour TTL)
     */
    std::vector<AgentRetrievalResult> retrieve_with_embedding(
        const std::string& query,
        const std::vector<AgentRegistration>& agents) {

        std::vector<AgentRetrievalResult> results;

        // Check if Agent list has changed
        std::string current_agent_list_hash = compute_agent_list_hash(agents);
        if (current_agent_list_hash != cached_agent_list_hash_) {
            // Agent list changed, recompute all agent embeddings
            update_agent_embeddings(agents);
            cached_agent_list_hash_ = current_agent_list_hash;
        }

        // Get query embedding (with cache)
        std::vector<float> query_embedding;
        auto cached_query = query_cache_->get(query);
        if (cached_query.has_value()) {
            query_embedding = cached_query.value();
        } else {
            query_embedding = call_embedding(query);
            query_cache_->put(query, query_embedding);
        }

        // Compute cosine similarity
        for (size_t i = 0; i < agents.size(); ++i) {
            float score = cosine_similarity(query_embedding, cached_agent_embeddings_[i]);
            results.push_back({agents[i], score, "embedding"});
        }

        return results;
    }

    /**
     * @brief Update cached agent embeddings
     *
     * 1. Try to load from VectorStore (file cache)
     * 2. If not cached, compute and save to VectorStore
     */
    void update_agent_embeddings(const std::vector<AgentRegistration>& agents) {
        cached_agent_embeddings_.clear();

        for (const auto& agent : agents) {
            std::vector<float> embedding;

            // Try to load from file cache
            if (vector_store_.contains("agent_cards", agent.id)) {
                embedding = vector_store_.get("agent_cards", agent.id);
            } else {
                // Compute and save
                std::string text = build_agent_text(agent);
                embedding = call_embedding(text);
                vector_store_.put("agent_cards", agent.id, embedding);
            }

            cached_agent_embeddings_.push_back(embedding);
        }

        // Save to file for next startup
        vector_store_.save("agent_cards");
    }

    /**
     * @brief Compute hash of agent list (to detect changes)
     */
    std::string compute_agent_list_hash(const std::vector<AgentRegistration>& agents) {
        std::ostringstream oss;
        for (const auto& agent : agents) {
            oss << agent.id << ",";
        }
        return oss.str();
    }

    /**
     * @brief Retrieve using keyword matching (fallback)
     */
    std::vector<AgentRetrievalResult> retrieve_with_keyword(
        const std::string& query,
        const std::vector<AgentRegistration>& agents) {

        std::vector<AgentRetrievalResult> results;

        for (const auto& agent : agents) {
            float score = compute_keyword_relevance(query, agent);
            std::string reason = compute_match_reason(query, agent);
            results.push_back({agent, score, reason});
        }

        return results;
    }

    /**
     * @brief Build text representation for an Agent (for embedding)
     */
    static std::string build_agent_text(const AgentRegistration& agent) {
        std::ostringstream oss;
        oss << agent.name << " " << agent.description;
        for (const auto& tag : agent.tags) {
            oss << " " << tag;
        }
        for (const auto& skill : agent.skills) {
            oss << " " << skill.name << " " << skill.description;
            for (const auto& example : skill.input_examples) {
                oss << " " << example;
            }
        }
        return oss.str();
    }

    /**
     * @brief Compute cosine similarity between two vectors
     */
    static float cosine_similarity(const std::vector<float>& a, const std::vector<float>& b) {
        if (a.size() != b.size() || a.empty()) return 0.0f;

        float dot = 0.0f, norm_a = 0.0f, norm_b = 0.0f;
        for (size_t i = 0; i < a.size(); ++i) {
            dot += a[i] * b[i];
            norm_a += a[i] * a[i];
            norm_b += b[i] * b[i];
        }

        float denom = std::sqrt(norm_a) * std::sqrt(norm_b);
        return (denom > 0) ? (dot / denom) : 0.0f;
    }

    /**
     * @brief Compute keyword-based relevance (fallback)
     */
    float compute_keyword_relevance(const std::string& query, const AgentRegistration& agent) {
        float score = 0.0f;
        std::string lower_query = to_lowercase(query);
        auto keywords = split_keywords(lower_query);

        // Tag matching
        for (const auto& tag : agent.tags) {
            std::string lower_tag = to_lowercase(tag);
            if (lower_query.find(lower_tag) != std::string::npos) {
                score += 0.3f;
            }
        }

        // Description matching
        std::string lower_desc = to_lowercase(agent.description);
        for (const auto& kw : keywords) {
            if (lower_desc.find(kw) != std::string::npos) {
                score += 0.05f;
            }
        }

        // Skill matching
        for (const auto& skill : agent.skills) {
            for (const auto& kw : keywords) {
                if (to_lowercase(skill.name).find(kw) != std::string::npos) {
                    score += 0.1f;
                }
            }
            for (const auto& example : skill.input_examples) {
                if (compute_keyword_overlap(lower_query, to_lowercase(example)) > 0.3f) {
                    score += 0.2f;
                }
            }
        }

        return std::min(1.0f, std::max(0.0f, score));
    }

    /**
     * @brief Compute match reason (for logging)
     */
    std::string compute_match_reason(const std::string& query, const AgentRegistration& agent) {
        if (use_embedding_) return "embedding";

        std::string lower_query = to_lowercase(query);
        std::vector<std::string> reasons;

        for (const auto& tag : agent.tags) {
            if (lower_query.find(to_lowercase(tag)) != std::string::npos) {
                reasons.push_back("tag:" + tag);
            }
        }

        for (const auto& skill : agent.skills) {
            for (const auto& example : skill.input_examples) {
                if (compute_keyword_overlap(lower_query, to_lowercase(example)) > 0.3f) {
                    reasons.push_back("skill:" + skill.name);
                    break;
                }
            }
        }

        if (reasons.empty()) return "default";

        std::string result;
        for (size_t i = 0; i < reasons.size(); ++i) {
            if (i > 0) result += ",";
            result += reasons[i];
        }
        return result;
    }

    // Utility functions
    static std::string to_lowercase(const std::string& str) {
        std::string result;
        result.reserve(str.size());
        for (char c : str) {
            result += static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        }
        return result;
    }

    static std::vector<std::string> split_keywords(const std::string& text) {
        std::vector<std::string> keywords;
        std::istringstream iss(text);
        std::string word;
        while (iss >> word) {
            if (word.length() > 1) {
                keywords.push_back(word);
            }
        }
        return keywords;
    }

    static float compute_keyword_overlap(const std::string& text1, const std::string& text2) {
        auto words1 = split_keywords(text1);
        auto words2 = split_keywords(text2);

        if (words1.empty() || words2.empty()) return 0.0f;

        int matches = 0;
        for (const auto& w1 : words1) {
            for (const auto& w2 : words2) {
                if (w1 == w2 || w1.find(w2) != std::string::npos || w2.find(w1) != std::string::npos) {
                    matches++;
                    break;
                }
            }
        }

        return static_cast<float>(matches) / std::max(words1.size(), words2.size());
    }

    /**
     * @brief 调用 Embedding 服务
     */
    std::vector<float> call_embedding(const std::string& text) {
        if (embedding_provider_ == "local" && local_embedding_service_) {
            return local_embedding_service_->embed(text);
        } else if (dashscope_embedding_service_) {
            return dashscope_embedding_service_->embed(text);
        }
        throw std::runtime_error("No embedding service available");
    }

    RegistryClient& registry_client_;
    bool use_embedding_;
    std::string embedding_provider_;
    std::unique_ptr<agent_rpc::mcp::rag::LocalEmbeddingService> local_embedding_service_;
    std::unique_ptr<agent_rpc::mcp::rag::EmbeddingService> dashscope_embedding_service_;
    std::unique_ptr<agent_rpc::mcp::rag::EmbeddingCache> query_cache_;
    VectorStore vector_store_;

    std::vector<std::vector<float>> cached_agent_embeddings_;
    std::string cached_agent_list_hash_;
};

} // namespace orchestrator
} // namespace agent_rpc
