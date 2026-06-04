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

    // Logging
    std::string log_file;
    std::string log_level = "INFO";

    /**
     * @brief Load configuration from environment variables
     */
    static OrchestratorConfig from_env() {
        OrchestratorConfig config;

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
