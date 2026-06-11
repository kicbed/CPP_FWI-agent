#include <algorithm>

#include <gtest/gtest.h>
#include <a2a/examples/agent_registry.hpp>

TEST(CodeAgentRegistrationTest, RegistrationUsesCodeTagAndSkills) {
    AgentRegistration registration;
    registration.id = "code-agent-1";
    registration.name = "Code Agent";
    registration.address = "http://localhost:5010";
    registration.tags = {"code", "engineering", "debugging"};
    registration.description = "Read-only code analysis agent for repository navigation, error diagnosis, and patch suggestions.";
    registration.capabilities = {false, true, false};
    registration.skills = {
        {"code_navigation", "Locate files and explain code paths", {"where is orchestrator routing implemented?"}},
        {"error_diagnosis", "Analyze compiler and runtime errors", {"explain this C++ build error"}},
        {"patch_proposal", "Propose safe patches without applying them", {"suggest a fix for this function"}}
    };
    registration.agent_card = registration.build_agent_card();

    EXPECT_EQ(registration.id, "code-agent-1");
    EXPECT_NE(std::find(registration.tags.begin(), registration.tags.end(), "code"), registration.tags.end());
    ASSERT_EQ(registration.skills.size(), 3u);
    EXPECT_EQ(registration.skills[0].name, "code_navigation");
    EXPECT_TRUE(registration.capabilities.tool_calling);
    EXPECT_EQ(registration.agent_card["agent_id"], "code-agent-1");
    EXPECT_EQ(registration.agent_card["tags"][0], "code");
}
