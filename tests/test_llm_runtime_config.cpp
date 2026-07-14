#include "llm_runtime_config.h"

#include <gtest/gtest.h>

#include <stdexcept>
#include <string>

namespace {

using agent_rpc::examples::resolve_llm_runtime_config;

TEST(LLMRuntimeConfigTest, DeepSeekUsesOnlyDeepSeekKeyAndEndpoint) {
    const auto config = resolve_llm_runtime_config("deepseek");

    EXPECT_EQ(config.provider, LLMProvider::DEEPSEEK);
    EXPECT_EQ(config.model, "deepseek-chat");
    EXPECT_EQ(config.api_url,
              "https://api.deepseek.com/v1/chat/completions");
    EXPECT_EQ(config.api_key_env_name, "DEEPSEEK_API_KEY");
    EXPECT_TRUE(config.requires_api_key());
}

TEST(LLMRuntimeConfigTest, QwenUsesOnlyQwenKeyAndDashScopeEndpoint) {
    const auto config = resolve_llm_runtime_config("qwen");

    EXPECT_EQ(config.provider, LLMProvider::QWEN);
    EXPECT_EQ(config.model, "qwen-plus");
    EXPECT_EQ(
        config.api_url,
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation");
    EXPECT_EQ(config.api_key_env_name, "QWEN_API_KEY");
    EXPECT_TRUE(config.requires_api_key());
}

TEST(LLMRuntimeConfigTest, OpenAIUsesOnlyOpenAIKeyAndEndpoint) {
    const auto config = resolve_llm_runtime_config("openai");

    EXPECT_EQ(config.provider, LLMProvider::OPENAI);
    EXPECT_EQ(config.model, "gpt-4o-mini");
    EXPECT_EQ(config.api_url,
              "https://api.openai.com/v1/chat/completions");
    EXPECT_EQ(config.api_key_env_name, "OPENAI_API_KEY");
    EXPECT_TRUE(config.requires_api_key());
}

TEST(LLMRuntimeConfigTest, LocalUsesLoopbackDefaultAndNoKey) {
    const auto config = resolve_llm_runtime_config("local");

    EXPECT_EQ(config.provider, LLMProvider::LOCAL);
    EXPECT_EQ(config.model, "qwen2.5:7b");
    EXPECT_EQ(config.api_url,
              "http://127.0.0.1:11434/v1/chat/completions");
    EXPECT_TRUE(config.api_key_env_name.empty());
    EXPECT_FALSE(config.requires_api_key());
}

TEST(LLMRuntimeConfigTest, ExplicitModelAndOfficialEndpointAreAccepted) {
    const auto config = resolve_llm_runtime_config(
        "openai", "approved-model",
        "https://api.openai.com/v1/chat/completions");

    EXPECT_EQ(config.provider, LLMProvider::OPENAI);
    EXPECT_EQ(config.model, "approved-model");
    EXPECT_EQ(config.api_url, "https://api.openai.com/v1/chat/completions");
    EXPECT_EQ(config.api_key_env_name, "OPENAI_API_KEY");
}

TEST(LLMRuntimeConfigTest, CloudProviderRejectsEndpointOverride) {
    EXPECT_THROW(resolve_llm_runtime_config(
                     "openai", "approved-model", "https://gateway.example/v1/chat"),
                 std::invalid_argument);
    EXPECT_THROW(resolve_llm_runtime_config(
                     "deepseek", "", "http://127.0.0.1:9999/collect"),
                 std::invalid_argument);
}

TEST(LLMRuntimeConfigTest, LocalEndpointMustRemainOnLoopback) {
    const auto local = resolve_llm_runtime_config(
        "local", "approved-local", "http://localhost:11434/v1/chat/completions");
    EXPECT_EQ(local.api_url, "http://localhost:11434/v1/chat/completions");
    EXPECT_THROW(resolve_llm_runtime_config(
                     "local", "", "https://example.com/v1/chat/completions"),
                 std::invalid_argument);
    EXPECT_THROW(resolve_llm_runtime_config("local", "", "http://127.0.0.1:11434"),
                 std::invalid_argument);
}

TEST(LLMRuntimeConfigTest, LocalEndpointRejectsAmbiguousUrlForms) {
    const std::vector<std::string> rejected = {
        "http://127.0.0.1:0/v1/chat/completions",
        "http://127.0.0.1:65536/v1/chat/completions",
        "http://127.0.0.1:11434//evil",
        "http://127.0.0.1:11434/v1/chat?next=http://evil.example",
        "http://127.0.0.1:11434/v1/chat#fragment",
        "http://127.0.0.1:11434@evil.example/v1/chat",
        "http://127.0.0.1.evil.example:11434/v1/chat",
        "http://2130706433:11434/v1/chat",
        "http://[::1]:11434/v1/chat",
        "http://localhost:11434/v1/chat\\extra",
        "http://localhost:11434/v1/chat\nInjected: true",
    };
    for (const auto& endpoint : rejected) {
        EXPECT_THROW(resolve_llm_runtime_config("local", "", endpoint),
                     std::invalid_argument)
            << endpoint;
    }
}

TEST(LLMRuntimeConfigTest, UnknownOrEmptyProviderFailsClosed) {
    EXPECT_THROW(resolve_llm_runtime_config("custom"),
                 std::invalid_argument);
    EXPECT_THROW(resolve_llm_runtime_config(""), std::invalid_argument);
    EXPECT_THROW(resolve_llm_runtime_config("DeepSeek"),
                 std::invalid_argument);
}

}  // namespace
