/**
 * @file test_agent_router_properties.cpp
 * @brief Property-based tests for Agent Router
 * 
 * Task 8.4, 8.5: Property tests for agent routing
 * 
 * **Feature: a2a-integration**
 * **Property 4: Agent Selection Determinism**
 * **Property 8: Agent Health State Consistency**
 * **Validates: Requirements 2.3, 3.4, 4.5, 10.3**
 */

#include <gtest/gtest.h>
#include <rapidcheck.h>
#include <rapidcheck/gtest.h>

#include "agent_rpc/orchestrator/agent_router.h"
#include "agent_rpc/orchestrator/agent_info.h"

#include <unordered_set>
#include <algorithm>

using namespace agent_rpc::orchestrator;

// ============================================================================
// Test Fixtures
// ============================================================================

class AgentRouterPropertyTest : public ::testing::Test {
protected:
    void SetUp() override {
        router_ = std::make_unique<AgentRouter>();
        ASSERT_TRUE(router_->initialize(RoutingStrategy::SKILL_MATCH));
    }
    
    void TearDown() override {
        router_->shutdown();
    }
    
    AgentInfo createAgent(const std::string& id, 
                          const std::vector<std::string>& skills,
                          bool healthy = true) {
        AgentInfo agent;
        agent.id = id;
        agent.name = "Agent " + id;
        agent.url = "http://localhost:500" + id;
        agent.skills = skills;
        agent.is_healthy = healthy;
        agent.current_load = 0;
        return agent;
    }
    
    std::unique_ptr<AgentRouter> router_;
};

// ============================================================================
// Helper Generators
// ============================================================================

namespace rc {

// Generator for agent IDs
Gen<std::string> genAgentId() {
    return gen::map(
        gen::inRange(1, 1000),
        [](int n) { return "agent-" + std::to_string(n); }
    );
}

// Generator for skills
Gen<std::string> genSkill() {
    return gen::element<std::string>(
        "math", "coding", "writing", "translation", "general"
    );
}

// Generator for skill list
Gen<std::vector<std::string>> genSkillList() {
    return gen::unique<std::vector<std::string>>(genSkill());
}

// Generator for agent count
Gen<int> genAgentCount() {
    return gen::inRange(1, 20);
}

} // namespace rc

// ============================================================================
// Property 4: Agent Selection Determinism
// **Validates: Requirements 2.3, 3.4**
// ============================================================================

/**
 * Property 4.1: Round-robin produces cyclic sequence
 * Note: The exact order depends on internal map iteration, but the pattern should repeat
 */
TEST_F(AgentRouterPropertyTest, RoundRobinProducesCyclicSequence) {
    router_->setStrategy(RoutingStrategy::ROUND_ROBIN);
    
    // Add agents
    std::vector<AgentInfo> agents;
    for (int i = 0; i < 5; ++i) {
        agents.push_back(createAgent(std::to_string(i), {"general"}));
    }
    router_->updateAgentList(agents);
    
    // Select agents multiple times
    std::vector<std::string> selected_ids;
    for (int i = 0; i < 15; ++i) {
        auto selected = router_->selectAgent("test question");
        ASSERT_TRUE(selected.has_value());
        selected_ids.push_back(selected->id);
    }
    
    // Verify round-robin pattern: sequence should repeat every 5 selections
    // First cycle establishes the order
    std::vector<std::string> first_cycle(selected_ids.begin(), selected_ids.begin() + 5);
    std::vector<std::string> second_cycle(selected_ids.begin() + 5, selected_ids.begin() + 10);
    std::vector<std::string> third_cycle(selected_ids.begin() + 10, selected_ids.begin() + 15);
    
    EXPECT_EQ(first_cycle, second_cycle);
    EXPECT_EQ(second_cycle, third_cycle);
    
    // Verify all agents are selected in each cycle
    std::unordered_set<std::string> unique_in_cycle(first_cycle.begin(), first_cycle.end());
    EXPECT_EQ(unique_in_cycle.size(), 5u);
}

/**
 * Property 4.2: Skill-match returns agents with matching skills
 */
RC_GTEST_FIXTURE_PROP(AgentRouterPropertyTest, SkillMatchReturnsMatchingAgents, ()) {
    router_->setStrategy(RoutingStrategy::SKILL_MATCH);
    
    // Create agents with different skills
    auto math_agent = createAgent("math-1", {"math", "general"});
    auto code_agent = createAgent("code-1", {"coding", "general"});
    auto write_agent = createAgent("write-1", {"writing", "general"});
    
    router_->addAgent(math_agent);
    router_->addAgent(code_agent);
    router_->addAgent(write_agent);
    
    // Select with specific skill requirement
    auto selected = router_->selectAgent("", {"math"});
    
    RC_ASSERT(selected.has_value());
    RC_ASSERT(selected->hasSkill("math"));
}

/**
 * Property 4.3: Selection only returns healthy agents
 */
RC_GTEST_FIXTURE_PROP(AgentRouterPropertyTest, SelectionOnlyReturnsHealthyAgents, ()) {
    int agent_count = *rc::gen::inRange(2, 10);
    
    std::vector<AgentInfo> agents;
    for (int i = 0; i < agent_count; ++i) {
        auto agent = createAgent(std::to_string(i), {"general"});
        agent.is_healthy = (i % 2 == 0);  // Half healthy, half unhealthy
        agents.push_back(agent);
    }
    router_->updateAgentList(agents);
    
    // Select multiple times
    for (int i = 0; i < 20; ++i) {
        auto selected = router_->selectAgent("test");
        if (selected.has_value()) {
            RC_ASSERT(selected->is_healthy);
        }
    }
}

/**
 * Property 4.4: Least-load selects agent with minimum load
 */
TEST_F(AgentRouterPropertyTest, LeastLoadSelectsMinimumLoad) {
    router_->setStrategy(RoutingStrategy::LEAST_LOAD);
    
    // Create agents with different loads
    auto agent1 = createAgent("1", {"general"});
    agent1.current_load = 10;
    auto agent2 = createAgent("2", {"general"});
    agent2.current_load = 5;
    auto agent3 = createAgent("3", {"general"});
    agent3.current_load = 15;
    
    router_->addAgent(agent1);
    router_->addAgent(agent2);
    router_->addAgent(agent3);
    
    auto selected = router_->selectAgent("test");
    
    ASSERT_TRUE(selected.has_value());
    EXPECT_EQ(selected->id, "2");  // Agent with load 5
}

/**
 * Property 4.5: Empty agent list returns nullopt
 */
TEST_F(AgentRouterPropertyTest, EmptyAgentListReturnsNullopt) {
    auto selected = router_->selectAgent("test question");
    EXPECT_FALSE(selected.has_value());
}

/**
 * Property 4.6: All unhealthy agents returns nullopt
 */
TEST_F(AgentRouterPropertyTest, AllUnhealthyReturnsNullopt) {
    auto agent1 = createAgent("1", {"general"}, false);
    auto agent2 = createAgent("2", {"general"}, false);
    
    router_->addAgent(agent1);
    router_->addAgent(agent2);
    
    auto selected = router_->selectAgent("test");
    EXPECT_FALSE(selected.has_value());
}

// ============================================================================
// Property 8: Agent Health State Consistency
// **Validates: Requirements 10.3, 4.5**
// ============================================================================

/**
 * Property 8.1: Unhealthy agents are excluded from selection
 */
RC_GTEST_FIXTURE_PROP(AgentRouterPropertyTest, UnhealthyAgentsExcludedFromSelection, ()) {
    int agent_count = *rc::gen::inRange(2, 10);
    
    std::vector<AgentInfo> agents;
    for (int i = 0; i < agent_count; ++i) {
        agents.push_back(createAgent(std::to_string(i), {"general"}));
    }
    router_->updateAgentList(agents);
    
    // Mark some agents unhealthy
    std::unordered_set<std::string> unhealthy_ids;
    for (int i = 0; i < agent_count; i += 2) {
        router_->markAgentUnhealthy(std::to_string(i));
        unhealthy_ids.insert(std::to_string(i));
    }
    
    // Select multiple times
    for (int i = 0; i < 50; ++i) {
        auto selected = router_->selectAgent("test");
        if (selected.has_value()) {
            RC_ASSERT(unhealthy_ids.find(selected->id) == unhealthy_ids.end());
        }
    }
}

/**
 * Property 8.2: markAgentUnhealthy changes health state
 */
RC_GTEST_FIXTURE_PROP(AgentRouterPropertyTest, MarkUnhealthyChangesState, ()) {
    auto agent_id = *rc::genAgentId();
    auto agent = createAgent(agent_id, {"general"}, true);
    
    router_->addAgent(agent);
    
    RC_ASSERT(router_->isAgentHealthy(agent_id));
    
    router_->markAgentUnhealthy(agent_id);
    
    RC_ASSERT(!router_->isAgentHealthy(agent_id));
}

/**
 * Property 8.3: markAgentHealthy restores health state
 */
RC_GTEST_FIXTURE_PROP(AgentRouterPropertyTest, MarkHealthyRestoresState, ()) {
    auto agent_id = *rc::genAgentId();
    auto agent = createAgent(agent_id, {"general"}, false);
    
    router_->addAgent(agent);
    
    RC_ASSERT(!router_->isAgentHealthy(agent_id));
    
    router_->markAgentHealthy(agent_id);
    
    RC_ASSERT(router_->isAgentHealthy(agent_id));
}

/**
 * Property 8.4: Health state is consistent across queries
 */
RC_GTEST_FIXTURE_PROP(AgentRouterPropertyTest, HealthStateConsistentAcrossQueries, ()) {
    auto agent_id = *rc::genAgentId();
    auto agent = createAgent(agent_id, {"general"});
    
    router_->addAgent(agent);
    
    // Toggle health state
    bool expected_healthy = *rc::gen::arbitrary<bool>();
    if (expected_healthy) {
        router_->markAgentHealthy(agent_id);
    } else {
        router_->markAgentUnhealthy(agent_id);
    }
    
    // Query multiple times
    for (int i = 0; i < 10; ++i) {
        RC_ASSERT(router_->isAgentHealthy(agent_id) == expected_healthy);
    }
}

/**
 * Property 8.5: getHealthyAgents only returns healthy agents
 */
RC_GTEST_FIXTURE_PROP(AgentRouterPropertyTest, GetHealthyAgentsOnlyReturnsHealthy, ()) {
    int agent_count = *rc::gen::inRange(1, 10);
    
    for (int i = 0; i < agent_count; ++i) {
        auto agent = createAgent(std::to_string(i), {"general"});
        agent.is_healthy = *rc::gen::arbitrary<bool>();
        router_->addAgent(agent);
    }
    
    auto healthy_agents = router_->getHealthyAgents();
    
    for (const auto& agent : healthy_agents) {
        RC_ASSERT(agent.is_healthy);
    }
}

/**
 * Property 8.6: Healthy agent count matches actual healthy agents
 */
RC_GTEST_FIXTURE_PROP(AgentRouterPropertyTest, HealthyCountMatchesActual, ()) {
    int agent_count = *rc::gen::inRange(1, 10);
    int expected_healthy = 0;
    
    for (int i = 0; i < agent_count; ++i) {
        auto agent = createAgent(std::to_string(i), {"general"});
        agent.is_healthy = *rc::gen::arbitrary<bool>();
        if (agent.is_healthy) expected_healthy++;
        router_->addAgent(agent);
    }
    
    RC_ASSERT(router_->getHealthyAgentCount() == static_cast<size_t>(expected_healthy));
}

// ============================================================================
// Additional Unit Tests
// ============================================================================

TEST_F(AgentRouterPropertyTest, InitializeAndShutdown) {
    AgentRouter router;
    
    EXPECT_TRUE(router.initialize(RoutingStrategy::ROUND_ROBIN));
    EXPECT_EQ(router.getStrategy(), RoutingStrategy::ROUND_ROBIN);
    
    router.shutdown();
    
    // Can reinitialize
    EXPECT_TRUE(router.initialize(RoutingStrategy::SKILL_MATCH));
}

TEST_F(AgentRouterPropertyTest, AddAndRemoveAgent) {
    auto agent = createAgent("test-1", {"math"});
    
    router_->addAgent(agent);
    EXPECT_EQ(router_->getAgentCount(), 1u);
    
    auto retrieved = router_->getAgent("test-1");
    ASSERT_TRUE(retrieved.has_value());
    EXPECT_EQ(retrieved->id, "test-1");
    
    EXPECT_TRUE(router_->removeAgent("test-1"));
    EXPECT_EQ(router_->getAgentCount(), 0u);
    
    EXPECT_FALSE(router_->removeAgent("non-existent"));
}

TEST_F(AgentRouterPropertyTest, FindAgentsBySkill) {
    router_->addAgent(createAgent("1", {"math", "general"}));
    router_->addAgent(createAgent("2", {"coding", "general"}));
    router_->addAgent(createAgent("3", {"math", "coding"}));
    
    auto math_agents = router_->findAgentsBySkill("math");
    EXPECT_EQ(math_agents.size(), 2u);
    
    auto coding_agents = router_->findAgentsBySkill("coding");
    EXPECT_EQ(coding_agents.size(), 2u);
    
    auto general_agents = router_->findAgentsBySkill("general");
    EXPECT_EQ(general_agents.size(), 2u);
}

TEST_F(AgentRouterPropertyTest, FindAgentsByTags) {
    auto agent1 = createAgent("1", {"math"});
    agent1.tags = {"production", "fast"};
    auto agent2 = createAgent("2", {"coding"});
    agent2.tags = {"production", "slow"};
    auto agent3 = createAgent("3", {"writing"});
    agent3.tags = {"staging"};
    
    router_->addAgent(agent1);
    router_->addAgent(agent2);
    router_->addAgent(agent3);
    
    auto prod_agents = router_->findAgentsByTags({"production"});
    EXPECT_EQ(prod_agents.size(), 2u);
    
    auto fast_prod_agents = router_->findAgentsByTags({"production", "fast"});
    EXPECT_EQ(fast_prod_agents.size(), 1u);
}

TEST_F(AgentRouterPropertyTest, UpdateAgentLoad) {
    auto agent = createAgent("1", {"general"});
    router_->addAgent(agent);
    
    router_->updateAgentLoad("1", 50);
    
    auto retrieved = router_->getAgent("1");
    ASSERT_TRUE(retrieved.has_value());
    EXPECT_EQ(retrieved->current_load, 50);
}

TEST_F(AgentRouterPropertyTest, SkillAnalysisFromQuestion) {
    router_->addAgent(createAgent("math-1", {"math"}));
    router_->addAgent(createAgent("code-1", {"coding"}));
    router_->addAgent(createAgent("write-1", {"writing"}));
    
    // Math question
    auto math_result = router_->selectAgent("Please calculate 2+2");
    ASSERT_TRUE(math_result.has_value());
    EXPECT_TRUE(math_result->hasSkill("math"));
    
    // Code question
    auto code_result = router_->selectAgent("Write a function to sort array");
    ASSERT_TRUE(code_result.has_value());
    EXPECT_TRUE(code_result->hasSkill("coding"));
    
    // Writing question
    auto write_result = router_->selectAgent("Write an essay about AI");
    ASSERT_TRUE(write_result.has_value());
    EXPECT_TRUE(write_result->hasSkill("writing"));
}

TEST_F(AgentRouterPropertyTest, AgentInfoHasSkillMethods) {
    AgentInfo agent;
    agent.skills = {"math", "coding"};
    agent.tags = {"production"};
    
    EXPECT_TRUE(agent.hasSkill("math"));
    EXPECT_TRUE(agent.hasSkill("coding"));
    EXPECT_FALSE(agent.hasSkill("writing"));
    
    EXPECT_TRUE(agent.hasTag("production"));
    EXPECT_FALSE(agent.hasTag("staging"));
    
    EXPECT_TRUE(agent.hasAllSkills({"math", "coding"}));
    EXPECT_FALSE(agent.hasAllSkills({"math", "writing"}));
    
    EXPECT_TRUE(agent.hasAnySkill({"math", "writing"}));
    EXPECT_FALSE(agent.hasAnySkill({"writing", "translation"}));
}

// ============================================================================
// Main
// ============================================================================

int main(int argc, char** argv) {
    ::testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}
