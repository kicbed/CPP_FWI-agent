#pragma once

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
inline std::string resolve_api_key_argument(const char* argument) {
    const std::string value = argument == nullptr ? "" : argument;
    if (value != "@env") {
        fail_api_key_configuration(
            "API keys must be supplied through the provider environment variable; "
            "pass the literal @env argument");
    }

    const char* provider_value = std::getenv("LLM_PROVIDER");
    const std::string provider = provider_value == nullptr ? "qwen" : provider_value;

    const auto read_env = [](const char* name) -> std::string {
        const char* candidate = std::getenv(name);
        return candidate == nullptr ? "" : candidate;
    };

    if (provider == "local") {
        return "not-needed";
    }
    std::string key;
    if (provider == "deepseek") key = read_env("DEEPSEEK_API_KEY");
    if (provider == "qwen") key = read_env("QWEN_API_KEY");
    if (provider == "openai") key = read_env("OPENAI_API_KEY");

    // Preserve the launcher's historical fallback order for custom provider
    // names without ever logging the selected value.
    if (provider != "deepseek" && provider != "qwen" && provider != "openai") {
        key = read_env("QWEN_API_KEY");
        if (key.empty()) key = read_env("DEEPSEEK_API_KEY");
        if (key.empty()) key = read_env("OPENAI_API_KEY");
    }
    if (key.empty()) {
        fail_api_key_configuration(
            "provider API key environment variable is not configured");
    }
    return key;
}

}  // namespace agent_rpc::examples
