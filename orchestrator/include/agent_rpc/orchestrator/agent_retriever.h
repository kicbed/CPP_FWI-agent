/**
 * @file agent_retriever.h
 * @brief AgentRetriever - recall candidate Agents based on query
 *
 * Implements the "R" (Retrieval) in Agent-RAG.
 * Retrieves topK candidate Agents from Registry based on query-AgentCard similarity.
 *
 * Similarity methods:
 * 1. Embedding similarity (local service or DashScope)
 * 2. Keyword matching (fallback when embedding is disabled or unavailable)
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
#include <cmath>
#include <cctype>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <mutex>
#include <sstream>
#include <system_error>
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
 * 1. Embedding mode: Uses a local service or DashScope for semantic similarity
 * 2. Keyword mode: Used automatically when embedding cannot serve a request
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
        , local_embedding_url_(local_embedding_url)
        , store_dir_(resolve_store_directory(store_dir))
        , vector_store_(store_dir_) {

        if (embedding_provider == "local") {
            // 使用本地 Embedding 服务
            agent_rpc::mcp::rag::LocalEmbeddingConfig config;
            config.api_url = local_embedding_url;
            config.timeout_ms = 5000;
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

            // The disk cache is loaded lazily after the live service reports
            // its provider/model/dimension identity. Legacy caches without
            // metadata, or caches with mixed dimensions, are never trusted.
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
            // Embedding failures must not take routing down. The local service
            // is optional, so an unavailable/malformed response falls back to
            // deterministic keyword matching for this request.
            std::lock_guard<std::mutex> lock(embedding_mutex_);
            try {
                const EmbeddingIdentity identity = resolve_embedding_identity();
                prepare_vector_cache(identity);
                results = retrieve_with_embedding(query, agents, identity.dimension);
            } catch (const std::exception&) {
                results = retrieve_with_keyword(query, agents);
            }
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
    struct EmbeddingIdentity {
        std::string provider;
        std::string model;
        std::size_t dimension = 0;

        std::string cache_key() const {
            return provider + "\n" + model + "\n" +
                   std::to_string(dimension);
        }
    };

    static constexpr int kVectorCacheSchemaVersion = 1;
    static constexpr std::size_t kMaxMetadataBytes = 16U * 1024U;

    // Preserve the old constructor call shape without continuing to write
    // relative to whichever working directory happened to launch the Agent.
    static std::string resolve_store_directory(const std::string& requested) {
        if (!requested.empty() && requested != "resources/embeddings") {
            if (!std::filesystem::path(requested).is_absolute()) {
                throw std::invalid_argument(
                    "custom embedding cache directory must be absolute");
            }
            return requested;
        }

        const char* configured = std::getenv("AGENT_EMBEDDING_CACHE_DIR");
        if (configured != nullptr && configured[0] != '\0') {
            if (!std::filesystem::path(configured).is_absolute()) {
                throw std::invalid_argument(
                    "AGENT_EMBEDDING_CACHE_DIR must be absolute");
            }
            return configured;
        }

        const char* xdg_cache = std::getenv("XDG_CACHE_HOME");
        if (xdg_cache != nullptr && xdg_cache[0] != '\0' &&
            std::filesystem::path(xdg_cache).is_absolute()) {
            return (std::filesystem::path(xdg_cache) / "cpp-fwi-agent" /
                    "embeddings").string();
        }
        const char* home = std::getenv("HOME");
        if (home != nullptr && home[0] != '\0' &&
            std::filesystem::path(home).is_absolute()) {
            return (std::filesystem::path(home) / ".cache" /
                    "cpp-fwi-agent" / "embeddings").string();
        }
        return "/tmp/cpp-fwi-agent-" +
               std::to_string(static_cast<unsigned long long>(::geteuid())) +
               "/embeddings";
    }

    struct HealthResponse {
        std::string body;
        bool too_large = false;
    };

    static std::size_t append_health_response(void* contents, std::size_t size,
                                              std::size_t count,
                                              HealthResponse* response) {
        const std::size_t bytes = size * count;
        if (bytes > kMaxMetadataBytes - response->body.size()) {
            response->too_large = true;
            return 0;
        }
        response->body.append(static_cast<const char*>(contents), bytes);
        return bytes;
    }

    static bool is_safe_model_id(const std::string& value) {
        if (value.empty() || value.size() > 128) return false;
        return std::all_of(value.begin(), value.end(), [](unsigned char c) {
            return std::isalnum(c) != 0 || c == '.' || c == '_' || c == '-' ||
                   c == '/';
        });
    }

    static bool is_valid_embedding(const std::vector<float>& embedding,
                                   std::size_t expected_dimension) {
        if (expected_dimension == 0 || embedding.size() != expected_dimension) {
            return false;
        }
        return std::all_of(embedding.begin(), embedding.end(), [](float value) {
            return std::isfinite(value);
        });
    }

    EmbeddingIdentity resolve_local_embedding_identity() const {
        CURL* curl = curl_easy_init();
        if (!curl) {
            throw std::runtime_error("Failed to initialize embedding health request");
        }
        HealthResponse response;
        const std::string url = local_embedding_url_ + "/health";
        curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
        curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, append_health_response);
        curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response);
        curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT_MS, 1000L);
        curl_easy_setopt(curl, CURLOPT_TIMEOUT_MS, 2000L);
        curl_easy_setopt(curl, CURLOPT_NOSIGNAL, 1L);
        curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 0L);
        curl_easy_setopt(curl, CURLOPT_PROTOCOLS, CURLPROTO_HTTP);
        curl_easy_setopt(curl, CURLOPT_NOPROXY, "*");

        const CURLcode result = curl_easy_perform(curl);
        long status = 0;
        curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &status);
        curl_easy_cleanup(curl);
        if (response.too_large || result != CURLE_OK || status != 200) {
            throw std::runtime_error("Local embedding health check failed");
        }

        try {
            const nlohmann::json health = nlohmann::json::parse(response.body);
            const int dimension = health.value("dimension", 0);
            const std::string model = health.value(
                "model", std::string("legacy-local-service"));
            if (!health.is_object() || health.value("status", std::string()) != "ok" ||
                !health.value("model_loaded", true) || dimension <= 0 ||
                dimension > 65536 || !is_safe_model_id(model)) {
                throw std::runtime_error("Local embedding health response is invalid");
            }
            return {"local", model, static_cast<std::size_t>(dimension)};
        } catch (const nlohmann::json::exception&) {
            throw std::runtime_error("Local embedding health response is invalid");
        }
    }

    EmbeddingIdentity resolve_embedding_identity() const {
        if (embedding_provider_ == "local" && local_embedding_service_) {
            return resolve_local_embedding_identity();
        }
        if (embedding_provider_ == "dashscope" && dashscope_embedding_service_) {
            const auto& config = dashscope_embedding_service_->getConfig();
            if (config.dimension <= 0 || config.dimension > 65536 ||
                !is_safe_model_id(config.model)) {
                throw std::runtime_error("DashScope embedding identity is invalid");
            }
            return {"dashscope", config.model,
                    static_cast<std::size_t>(config.dimension)};
        }
        throw std::runtime_error("No embedding service is configured");
    }

    bool cache_metadata_matches(const EmbeddingIdentity& identity) {
        nlohmann::json metadata;
        if (!vector_store_.loadJsonDocument(
                "agent_cards.meta.json", &metadata, kMaxMetadataBytes)) {
            return false;
        }
        try {
            const std::string agent_list_hash = metadata.value(
                "agent_list_hash", std::string());
            const bool valid_hash = agent_list_hash.size() == 16U &&
                std::all_of(agent_list_hash.begin(), agent_list_hash.end(),
                            [](unsigned char value) {
                                return std::isxdigit(value) != 0;
                            });
            const bool matches = metadata.is_object() &&
                metadata.value("schema_version", 0) ==
                    kVectorCacheSchemaVersion &&
                metadata.value("provider", std::string()) ==
                    identity.provider &&
                metadata.value("model", std::string()) == identity.model &&
                metadata.value("dimension", std::size_t{0}) ==
                    identity.dimension && valid_hash;
            if (matches) persisted_agent_list_hash_ = agent_list_hash;
            return matches;
        } catch (const std::exception&) {
            return false;
        }
    }

    void clear_vector_store() {
        for (const auto& key : vector_store_.keys("agent_cards")) {
            vector_store_.remove("agent_cards", key);
        }
    }

    void prepare_vector_cache(const EmbeddingIdentity& identity) {
        const std::string identity_key = identity.cache_key();
        if (vector_cache_initialized_ &&
            identity_key == active_embedding_identity_.cache_key()) {
            return;
        }

        clear_vector_store();
        persisted_agent_list_hash_.clear();
        if (cache_metadata_matches(identity)) {
            // Each loaded vector is validated again before use, so one corrupt
            // entry is recomputed rather than poisoning the entire retrieval.
            vector_store_.load("agent_cards");
        }
        cached_agent_embeddings_.clear();
        cached_agent_list_hash_.clear();
        query_cache_->clear();
        active_embedding_identity_ = identity;
        vector_cache_initialized_ = true;
    }

    void save_cache_metadata(const EmbeddingIdentity& identity,
                             const std::string& agent_list_hash) {
        nlohmann::json metadata = {
            {"schema_version", kVectorCacheSchemaVersion},
            {"provider", identity.provider},
            {"model", identity.model},
            {"dimension", identity.dimension},
            {"agent_list_hash", agent_list_hash},
        };
        (void)vector_store_.saveJsonDocument(
            "agent_cards.meta.json", metadata, kMaxMetadataBytes);
    }

    /**
     * @brief Retrieve using embedding similarity
     *
     * Caching strategy:
     * 1. AgentCard vectors: cached in agent_embeddings_, recomputed when Agent list changes
     * 2. Query vectors: cached in query_cache_ (LRU, 1 hour TTL)
     */
    std::vector<AgentRetrievalResult> retrieve_with_embedding(
        const std::string& query,
        const std::vector<AgentRegistration>& agents,
        std::size_t expected_dimension) {

        std::vector<AgentRetrievalResult> results;

        // Check if Agent list has changed
        std::string current_agent_list_hash = compute_agent_list_hash(agents);
        if (current_agent_list_hash != cached_agent_list_hash_) {
            // A persisted vector is reusable only when it was generated from
            // the exact same AgentCard content. If a registration changes but
            // keeps its ID, recompute instead of silently using stale vectors.
            const bool may_use_persisted_vectors =
                cached_agent_list_hash_.empty() &&
                current_agent_list_hash == persisted_agent_list_hash_;
            update_agent_embeddings(agents, expected_dimension,
                                    may_use_persisted_vectors,
                                    current_agent_list_hash);
            cached_agent_list_hash_ = current_agent_list_hash;
        }

        // Get query embedding (with cache)
        std::vector<float> query_embedding;
        auto cached_query = query_cache_->get(query);
        if (cached_query.has_value() &&
            is_valid_embedding(cached_query.value(), expected_dimension)) {
            query_embedding = cached_query.value();
        } else {
            query_cache_->remove(query);
            query_embedding = call_embedding(query);
            if (!is_valid_embedding(query_embedding, expected_dimension)) {
                throw std::runtime_error(
                    "Embedding response dimension or values are invalid");
            }
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
    void update_agent_embeddings(const std::vector<AgentRegistration>& agents,
                                 std::size_t expected_dimension,
                                 bool may_use_persisted_vectors,
                                 const std::string& agent_list_hash) {
        cached_agent_embeddings_.clear();
        if (!may_use_persisted_vectors) clear_vector_store();

        std::unordered_set<std::string> active_ids;
        active_ids.reserve(agents.size());

        for (const auto& agent : agents) {
            active_ids.insert(agent.id);
            std::vector<float> embedding;

            // Try to load from file cache
            if (vector_store_.contains("agent_cards", agent.id)) {
                embedding = vector_store_.get("agent_cards", agent.id);
            }
            if (!is_valid_embedding(embedding, expected_dimension)) {
                // Compute and save
                std::string text = build_agent_text(agent);
                embedding = call_embedding(text);
                if (!is_valid_embedding(embedding, expected_dimension)) {
                    throw std::runtime_error(
                        "Agent embedding dimension or values are invalid");
                }
                vector_store_.put("agent_cards", agent.id, embedding);
            }

            cached_agent_embeddings_.push_back(embedding);
        }

        // Drop entries for Agents that no longer exist. Only write metadata
        // after the vector file was persisted, so a torn update is rejected on
        // the next startup rather than silently mixing vector generations.
        for (const auto& key : vector_store_.keys("agent_cards")) {
            if (active_ids.find(key) == active_ids.end()) {
                vector_store_.remove("agent_cards", key);
            }
        }
        if (vector_store_.save("agent_cards")) {
            save_cache_metadata(active_embedding_identity_, agent_list_hash);
            persisted_agent_list_hash_ = agent_list_hash;
        }
    }

    /**
     * @brief Compute hash of agent list (to detect changes)
     */
    static std::string compute_agent_list_hash(
        const std::vector<AgentRegistration>& agents) {
        std::ostringstream oss;
        for (const auto& agent : agents) {
            const std::string text = build_agent_text(agent);
            oss << agent.id.size() << ':' << agent.id
                << text.size() << ':' << text << ';';
        }
        // FNV-1a is a deterministic cache fingerprint, not a security hash.
        // The length-prefixed serialization above avoids ambiguous joins.
        std::uint64_t hash = 1469598103934665603ULL;
        const std::string serialized = oss.str();
        for (unsigned char value : serialized) {
            hash ^= value;
            hash *= 1099511628211ULL;
        }
        static constexpr char hex[] = "0123456789abcdef";
        std::string result(16U, '0');
        for (std::size_t index = 0; index < result.size(); ++index) {
            const std::size_t shift = (result.size() - index - 1U) * 4U;
            result[index] = hex[(hash >> shift) & 0x0FU];
        }
        return result;
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
    std::string local_embedding_url_;
    std::string store_dir_;
    std::unique_ptr<agent_rpc::mcp::rag::LocalEmbeddingService> local_embedding_service_;
    std::unique_ptr<agent_rpc::mcp::rag::EmbeddingService> dashscope_embedding_service_;
    std::unique_ptr<agent_rpc::mcp::rag::EmbeddingCache> query_cache_;
    VectorStore vector_store_;

    std::vector<std::vector<float>> cached_agent_embeddings_;
    std::string cached_agent_list_hash_;
    std::string persisted_agent_list_hash_;
    EmbeddingIdentity active_embedding_identity_;
    bool vector_cache_initialized_ = false;
    std::mutex embedding_mutex_;
};

} // namespace orchestrator
} // namespace agent_rpc
