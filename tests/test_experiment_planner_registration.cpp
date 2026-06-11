#include <algorithm>

#include <gtest/gtest.h>
#include <a2a/examples/agent_registry.hpp>

TEST(ExperimentPlannerRegistrationTest, RegistrationUsesPlanningTagsAndDryRunSkill) {
    AgentRegistration registration;
    registration.id = "experiment-planner-1";
    registration.name = "Experiment Planner Agent";
    registration.address = "http://localhost:5011";
    registration.tags = {"experiment", "planning", "research-computing", "fwi"};
    registration.description = "Plans dry-run research computing experiments using AlgorithmCards and local research knowledge.";
    registration.capabilities = {false, true, true};
    registration.skills = {
        {"experiment_planning", "Create structured experiment plans", {"plan a multi-scale FWI experiment on Marmousi"}},
        {"parameter_advice", "Recommend parameters with risk analysis", {"how should I set frequency bands for missing low frequency data?"}},
        {"dry_run_job", "Render dry-run job specs without execution", {"generate a dry-run command for CUDA-MPI FWI"}}
    };
    registration.agent_card = registration.build_agent_card();

    EXPECT_EQ(registration.id, "experiment-planner-1");
    EXPECT_NE(std::find(registration.tags.begin(), registration.tags.end(), "experiment"),
              registration.tags.end());
    EXPECT_NE(std::find(registration.tags.begin(), registration.tags.end(), "research-computing"),
              registration.tags.end());
    ASSERT_EQ(registration.skills.size(), 3u);
    EXPECT_EQ(registration.skills[2].name, "dry_run_job");
    EXPECT_TRUE(registration.capabilities.tool_calling);
    EXPECT_TRUE(registration.capabilities.knowledge_base);
    EXPECT_EQ(registration.agent_card["agent_id"], "experiment-planner-1");
}
