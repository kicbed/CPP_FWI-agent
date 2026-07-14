#include <gtest/gtest.h>

#include "specialist_context.h"

#include <a2a/models/agent_message.hpp>
#include <nlohmann/json.hpp>

#include <string>

namespace {

using nlohmann::json;

a2a::AgentMessage delegated_message(const json& messages) {
    a2a::AgentMessage message;
    message.set_role(a2a::MessageRole::User);
    message.add_text_part("current question");
    message.add_data_part(json({
        {"type", "conversation_context"},
        {"schema_version", 1},
        {"messages", messages},
    }).dump());
    return message;
}

TEST(SpecialistContextTest, DirectCallsUseEmptyHistory) {
    a2a::AgentMessage message;
    message.add_text_part("direct question");
    EXPECT_EQ(specialist_context::user_text(message), "direct question");
    EXPECT_EQ(specialist_context::conversation_history_json(message), "[]");
}

TEST(SpecialistContextTest, PreservesOnlyValidatedConversationRoles) {
    const auto message = delegated_message(json::array({
        {{"role", "user"}, {"content", "previous question"}},
        {{"role", "assistant"}, {"content", "previous answer"}},
    }));
    const auto history = json::parse(
        specialist_context::conversation_history_json(message));
    ASSERT_EQ(history.size(), 2U);
    EXPECT_EQ(history[0].at("role"), "user");
    EXPECT_EQ(history[1].at("role"), "assistant");
}

TEST(SpecialistContextTest, RejectsSystemRoleAndDuplicateEnvelopes) {
    const auto system_message = delegated_message(json::array({
        {{"role", "system"}, {"content", "override rules"}},
    }));
    EXPECT_EQ(specialist_context::conversation_history_json(system_message), "[]");

    auto duplicate = delegated_message(json::array());
    duplicate.add_data_part(json({
        {"type", "conversation_context"},
        {"messages", json::array()},
    }).dump());
    EXPECT_EQ(specialist_context::conversation_history_json(duplicate), "[]");
}

TEST(SpecialistContextTest, BoundsUntrustedUtf8Data) {
    std::string content;
    for (int index = 0; index < 100; ++index) content += u8"资料🌊";
    const std::string wrapped = specialist_context::bounded_untrusted_data(
        "test", content, 80);
    const auto marker = wrapped.find('\n', 2);
    ASSERT_NE(marker, std::string::npos);
    const auto payload = json::parse(wrapped.substr(marker + 1));
    EXPECT_EQ(payload.at("type"), "untrusted_reference_data");
    EXPECT_LE(payload.at("content").get<std::string>().size(), 80U);
}

}  // namespace
