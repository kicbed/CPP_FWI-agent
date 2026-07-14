#pragma once

#include "agent_rpc/orchestrator/config.h"
#include <a2a/examples/llm_url_policy.hpp>

#include <cstdlib>
#include <stdexcept>
#include <string>

namespace agent_rpc::examples {

inline bool is_loopback_llm_url(const std::string& url) {
    return is_strict_loopback_http_url(url);
}

struct LLMRuntimeConfig {
    LLMProvider provider;
    std::string provider_name;
    std::string model;
    std::string api_url;
    std::string api_key_env_name;

    bool requires_api_key() const noexcept {
        return !api_key_env_name.empty();
    }
};

// This is the single provider allowlist used by specialist endpoint and key
// selection. Unknown provider names are rejected before any key is read or any
// network client is constructed.
inline LLMRuntimeConfig resolve_llm_runtime_config(
    const std::string& provider_name,
    const std::string& configured_model = "",
    const std::string& configured_api_url = "") {
    LLMRuntimeConfig config;
    config.provider_name = provider_name;

    if (provider_name == "deepseek") {
        static const std::string endpoint =
            "https://api.deepseek.com/v1/chat/completions";
        if (!configured_api_url.empty() && configured_api_url != endpoint) {
            throw std::invalid_argument(
                "cloud LLM_API_URL must match the provider's fixed official endpoint");
        }
        config.provider = LLMProvider::DEEPSEEK;
        config.model = configured_model.empty() ? "deepseek-chat" : configured_model;
        config.api_url = endpoint;
        config.api_key_env_name = "DEEPSEEK_API_KEY";
    } else if (provider_name == "qwen") {
        static const std::string endpoint =
            "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation";
        if (!configured_api_url.empty() && configured_api_url != endpoint) {
            throw std::invalid_argument(
                "cloud LLM_API_URL must match the provider's fixed official endpoint");
        }
        config.provider = LLMProvider::QWEN;
        config.model = configured_model.empty() ? "qwen-plus" : configured_model;
        config.api_url = endpoint;
        config.api_key_env_name = "QWEN_API_KEY";
    } else if (provider_name == "openai") {
        static const std::string endpoint =
            "https://api.openai.com/v1/chat/completions";
        if (!configured_api_url.empty() && configured_api_url != endpoint) {
            throw std::invalid_argument(
                "cloud LLM_API_URL must match the provider's fixed official endpoint");
        }
        config.provider = LLMProvider::OPENAI;
        config.model = configured_model.empty() ? "gpt-4o-mini" : configured_model;
        config.api_url = endpoint;
        config.api_key_env_name = "OPENAI_API_KEY";
    } else if (provider_name == "local") {
        config.provider = LLMProvider::LOCAL;
        config.model = configured_model.empty() ? "qwen2.5:7b" : configured_model;
        config.api_url = configured_api_url.empty()
            ? "http://127.0.0.1:11434/v1/chat/completions"
            : configured_api_url;
        if (!is_loopback_llm_url(config.api_url)) {
            throw std::invalid_argument(
                "local LLM_API_URL must be loopback HTTP with a port and path");
        }
        config.api_key_env_name.clear();
    } else {
        throw std::invalid_argument(
            "unsupported LLM_PROVIDER; expected deepseek, qwen, openai, or local");
    }

    return config;
}

inline LLMRuntimeConfig load_llm_runtime_config_from_env() {
    const char* provider_value = std::getenv("LLM_PROVIDER");
    const char* model_value = std::getenv("LLM_MODEL");
    const char* api_url_value = std::getenv("LLM_API_URL");

    // Keep the launcher's established default while treating an explicitly
    // empty/unknown value as invalid rather than silently changing providers.
    return resolve_llm_runtime_config(
        provider_value == nullptr ? "qwen" : provider_value,
        model_value == nullptr ? "" : model_value,
        api_url_value == nullptr ? "" : api_url_value);
}

}  // namespace agent_rpc::examples
