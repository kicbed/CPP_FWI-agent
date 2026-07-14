#pragma once

#include "llm_runtime_config.h"

#include <cstdio>
#include <cstdlib>
#include <string>

namespace agent_rpc::examples {

[[noreturn]] inline void fail_api_key_configuration(const char* message) {
    std::fputs(message, stderr);
    std::fputc('\n', stderr);
    std::exit(EXIT_FAILURE);
}

// The launch scripts pass the literal "@env" instead of a secret. Raw secret
// arguments are deliberately rejected so keys cannot appear in process lists.
inline std::string resolve_api_key_argument(
    const char* argument,
    const LLMRuntimeConfig& llm_config) {
    const std::string value = argument == nullptr ? "" : argument;
    if (value != "@env") {
        fail_api_key_configuration(
            "API keys must be supplied through the provider environment variable; "
            "pass the literal @env argument");
    }

    if (!llm_config.requires_api_key()) {
        return "not-needed";
    }

    const char* candidate = std::getenv(llm_config.api_key_env_name.c_str());
    const std::string key = candidate == nullptr ? "" : candidate;
    if (key.empty()) {
        fail_api_key_configuration(
            "provider API key environment variable is not configured");
    }
    return key;
}

// Compatibility overload for the orchestrator: it uses the same strict
// provider mapping, so unknown values can no longer trigger key fallback.
inline std::string resolve_api_key_argument(const char* argument) {
    return resolve_api_key_argument(argument,
                                    load_llm_runtime_config_from_env());
}

}  // namespace agent_rpc::examples
