/**
 * @file config.h
 * @brief OrchestratorConfig - centralized configuration management
 *
 * Reads from environment variables, command-line args, and config files.
 * Provides defaults and validation.
 */

#pragma once

#include <string>
#include <vector>
#include <stdexcept>
#include <cerrno>
#include <cstdlib>
#include <limits>

// LLM Provider 类型 - 使用 forward declaration 避免重复定义
#ifndef LLM_PROVIDER_DEFINED
#define LLM_PROVIDER_DEFINED
enum class LLMProvider {
    DEEPSEEK,   // DeepSeek (OpenAI 兼容)
    QWEN,       // 通义千问 (DashScope)
    OPENAI,     // OpenAI
    LOCAL       // 本地模型 (Ollama)
};
#endif

namespace agent_rpc {
namespace orchestrator {

/**
 * @brief Routing mode enumeration
 */
enum class RoutingMode {
    FIXED,      // Traditional if-else routing (math/code/fwi/general)
    AGENT_RAG   // Agent-RAG dynamic routing
};

/**
 * @brief Tool calling mode enumeration
 */
enum class ToolCallingMode {
    RULE,       // Rule-based tool selection (keyword matching)
    LLM         // LLM-based tool selection (Tool-RAG + LLM Tool Calling)
};

/**
 * @brief Convert routing mode to string
 */
inline std::string to_string(RoutingMode mode) {
    switch (mode) {
        case RoutingMode::FIXED:    return "fixed";
        case RoutingMode::AGENT_RAG: return "agent-rag";
        default:                    return "unknown";
    }
}

/**
 * @brief Parse routing mode from string
 */
inline RoutingMode routing_mode_from_string(const std::string& str) {
    if (str == "agent-rag") return RoutingMode::AGENT_RAG;
    return RoutingMode::FIXED;  // Default
}

/**
 * @brief Convert tool calling mode to string
 */
inline std::string to_string(ToolCallingMode mode) {
    switch (mode) {
        case ToolCallingMode::RULE: return "rule";
        case ToolCallingMode::LLM:  return "llm";
        default:                    return "unknown";
    }
}

/**
 * @brief Parse tool calling mode from string
 */
inline ToolCallingMode tool_calling_mode_from_string(const std::string& str) {
    if (str == "llm") return ToolCallingMode::LLM;
    return ToolCallingMode::RULE;  // Default
}

/**
 * @brief Orchestrator configuration
 */
struct OrchestratorConfig {
    // Agent identity
    std::string agent_id = "orch-1";
    int port = 5000;
    std::string registry_url = "http://localhost:8500";
    std::string api_key;

    // Redis
    std::string redis_host = "127.0.0.1";
    int redis_port = 6379;

    // Routing
    RoutingMode routing_mode = RoutingMode::FIXED;
    ToolCallingMode tool_calling_mode = ToolCallingMode::RULE;

    // MCP
    bool enable_mcp = false;
    std::string mcp_server_path;
    std::vector<std::string> mcp_args;

    // RAG
    bool enable_rag = false;
    int rag_top_k = 5;
    float rag_threshold = 0.3f;
    std::string dashscope_api_key;  // For DashScope embedding API
    std::string embedding_provider = "local";  // "local" or "dashscope"
    std::string local_embedding_url = "http://localhost:6000";  // Local embedding server

    // Access control
    std::string api_token;          // Simple token for access control
    std::vector<std::string> allowed_clients;  // Allowed client IDs

    // LLM Provider
    LLMProvider llm_provider = LLMProvider::DEEPSEEK;
    std::string llm_model;
    std::string llm_api_url;

    // Conversation context. These are hard server-side ceilings; a client may
    // request a smaller historyLength but cannot enlarge the prompt window.
    // The legacy *_chars names are byte ceilings after JSON serialization,
    // not Unicode code-point or tokenizer-token counts.
    std::size_t context_max_messages = 10;
    std::size_t context_max_chars = 12000;
    std::size_t context_max_message_chars = 4000;
    std::size_t conversation_max_stored_messages = 200;
    std::size_t conversation_ttl_seconds = 30 * 24 * 60 * 60;

    // Logging
    std::string log_file;
    std::string log_level = "INFO";

    /**
     * @brief Load configuration from environment variables
     */
    static OrchestratorConfig from_env() {
        OrchestratorConfig config;

        const auto bounded_size_from_env = [](const char* name,
                                              std::size_t fallback,
                                              std::size_t minimum,
                                              std::size_t maximum) {
            const char* value = std::getenv(name);
            if (value == nullptr || *value == '\0') return fallback;
            errno = 0;
            char* end = nullptr;
            const unsigned long long parsed = std::strtoull(value, &end, 10);
            if (errno != 0 || end == value || *end != '\0' ||
                parsed < minimum || parsed > maximum) {
                return fallback;
            }
            return static_cast<std::size_t>(parsed);
        };

        // Routing
        const char* routing_mode = std::getenv("ROUTING_MODE");
        if (routing_mode) {
            config.routing_mode = routing_mode_from_string(routing_mode);
        }

        const char* tool_calling_mode = std::getenv("TOOL_CALLING_MODE");
        if (tool_calling_mode) {
            config.tool_calling_mode = tool_calling_mode_from_string(tool_calling_mode);
        }

        // MCP
        const char* enable_mcp = std::getenv("ENABLE_MCP");
        if (enable_mcp && std::string(enable_mcp) == "true") {
            config.enable_mcp = true;
        }

        // RAG
        const char* enable_rag = std::getenv("ENABLE_RAG");
        if (enable_rag && std::string(enable_rag) == "true") {
            config.enable_rag = true;
        }

        const char* rag_top_k = std::getenv("RAG_TOP_K");
        if (rag_top_k) {
            config.rag_top_k = std::stoi(rag_top_k);
        }

        const char* rag_threshold = std::getenv("RAG_THRESHOLD");
        if (rag_threshold) {
            config.rag_threshold = std::stof(rag_threshold);
        }

        // DashScope API Key for embedding
        const char* dashscope_key = std::getenv("DASHSCOPE_API_KEY");
        if (dashscope_key) {
            config.dashscope_api_key = dashscope_key;
        }

        // Embedding provider
        const char* embedding_provider = std::getenv("EMBEDDING_PROVIDER");
        if (embedding_provider) {
            config.embedding_provider = embedding_provider;
        }

        // Local embedding URL
        const char* local_embedding_url = std::getenv("LOCAL_EMBEDDING_URL");
        if (local_embedding_url) {
            config.local_embedding_url = local_embedding_url;
        }

        // Access control
        const char* api_token = std::getenv("AGENT_API_TOKEN");
        if (api_token) {
            config.api_token = api_token;
        }

        // LLM Provider
        const char* llm_provider = std::getenv("LLM_PROVIDER");
        if (llm_provider) {
            std::string provider_str = llm_provider;
            if (provider_str == "deepseek") config.llm_provider = LLMProvider::DEEPSEEK;
            else if (provider_str == "qwen") config.llm_provider = LLMProvider::QWEN;
            else if (provider_str == "openai") config.llm_provider = LLMProvider::OPENAI;
            else if (provider_str == "local") config.llm_provider = LLMProvider::LOCAL;
        }

        // LLM Model
        const char* llm_model = std::getenv("LLM_MODEL");
        if (llm_model) {
            config.llm_model = llm_model;
        }

        // LLM API URL
        const char* llm_api_url = std::getenv("LLM_API_URL");
        if (llm_api_url) {
            config.llm_api_url = llm_api_url;
        }

        config.context_max_messages = bounded_size_from_env(
            "CONTEXT_MAX_MESSAGES", config.context_max_messages, 1, 50);
        config.context_max_chars = bounded_size_from_env(
            "CONTEXT_MAX_CHARS", config.context_max_chars, 1024, 100000);
        config.context_max_message_chars = bounded_size_from_env(
            "CONTEXT_MAX_MESSAGE_CHARS", config.context_max_message_chars,
            256, config.context_max_chars);
        config.conversation_max_stored_messages = bounded_size_from_env(
            "CONVERSATION_MAX_STORED_MESSAGES",
            config.conversation_max_stored_messages, 20, 5000);
        config.conversation_ttl_seconds = bounded_size_from_env(
            "CONVERSATION_TTL_SECONDS", config.conversation_ttl_seconds,
            60, 365ULL * 24ULL * 60ULL * 60ULL);

        return config;
    }

    /**
     * @brief Validate configuration
     * @throws std::runtime_error if configuration is invalid
     */
    void validate() const {
        if (api_key.empty()) {
            throw std::runtime_error("API key is required");
        }
        if (port <= 0 || port > 65535) {
            throw std::runtime_error("Invalid port: " + std::to_string(port));
        }
        if (redis_port <= 0 || redis_port > 65535) {
            throw std::runtime_error("Invalid Redis port: " + std::to_string(redis_port));
        }
    }
};

} // namespace orchestrator
} // namespace agent_rpc
