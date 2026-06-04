/**
 * @file agent_router.h
 * @brief Agent Router - selects appropriate agent for requests
 * 
 * Task 8.3: 实现AgentRouter路由器
 * Requirements: 2.3, 3.3, 3.4
 */

#pragma once

#include "agent_info.h"
#include <atomic>
#include <mutex>
#include <optional>
#include <random>
#include <unordered_map>
#include <unordered_set>

namespace agent_rpc {
namespace orchestrator {

/**
 * @brief Agent Router - routes requests to appropriate agents
 * 
 * Features:
 * - Multiple routing strategies (round-robin, random, skill-match, least-load)
 * - Health status tracking
 * - Skill-based filtering
 * - Thread-safe operations
 */
class AgentRouter {
public:
    AgentRouter();
    ~AgentRouter();
    
    // Disable copy
    AgentRouter(const AgentRouter&) = delete;
    AgentRouter& operator=(const AgentRouter&) = delete;
    
    /**
     * @brief Initialize router with strategy
     * @param strategy Routing strategy to use
     * @return true if initialization successful
     */
    bool initialize(RoutingStrategy strategy = RoutingStrategy::SKILL_MATCH);
    
    /**
     * @brief Shutdown router
     */
    void shutdown();
    
    // === Agent Selection ===
    
    /**
     * @brief Select an agent for a request
     * @param question The question/request content (used for skill analysis)
     * @param required_skills Optional list of required skills
     * @return Selected agent info, or nullopt if no suitable agent found
     * 
     * Property 4: Agent Selection Determinism
     */
    std::optional<AgentInfo> selectAgent(
        const std::string& question,
        const std::vector<std::string>& required_skills = {});
    
    /**
     * @brief Find agents by skill
     * @param skill Skill to search for
     * @return Vector of agents with the skill
     */
    std::vector<AgentInfo> findAgentsBySkill(const std::string& skill);
    
    /**
     * @brief Find agents by tags
     * @param tags Tags to search for (agent must have all tags)
     * @return Vector of matching agents
     */
    std::vector<AgentInfo> findAgentsByTags(const std::vector<std::string>& tags);
    
    /**
     * @brief Find healthy agents with required skills
     * @param required_skills Skills to match
     * @return Vector of healthy agents with matching skills
     */
    std::vector<AgentInfo> findHealthyAgentsWithSkills(
        const std::vector<std::string>& required_skills);
    
    // === Agent Management ===
    
    /**
     * @brief Update the list of available agents
     * @param agents New agent list
     */
    void updateAgentList(const std::vector<AgentInfo>& agents);
    
    /**
     * @brief Add a single agent
     * @param agent Agent to add
     */
    void addAgent(const AgentInfo& agent);
    
    /**
     * @brief Remove an agent by ID
     * @param agent_id Agent identifier
     * @return true if agent was removed
     */
    bool removeAgent(const std::string& agent_id);
    
    /**
     * @brief Get agent by ID
     * @param agent_id Agent identifier
     * @return Agent info if found
     */
    std::optional<AgentInfo> getAgent(const std::string& agent_id);
    
    /**
     * @brief Get all registered agents
     * @return Vector of all agents
     */
    std::vector<AgentInfo> getAllAgents();
    
    /**
     * @brief Get all healthy agents
     * @return Vector of healthy agents
     */
    std::vector<AgentInfo> getHealthyAgents();
    
    // === Health Management ===
    
    /**
     * @brief Mark an agent as unhealthy
     * @param agent_id Agent identifier
     * 
     * Property 8: Agent Health State Consistency
     */
    void markAgentUnhealthy(const std::string& agent_id);
    
    /**
     * @brief Mark an agent as healthy
     * @param agent_id Agent identifier
     */
    void markAgentHealthy(const std::string& agent_id);
    
    /**
     * @brief Check if agent is healthy
     * @param agent_id Agent identifier
     * @return true if agent is healthy
     */
    bool isAgentHealthy(const std::string& agent_id);
    
    /**
     * @brief Update agent heartbeat timestamp
     * @param agent_id Agent identifier
     */
    void updateHeartbeat(const std::string& agent_id);
    
    /**
     * @brief Update agent load
     * @param agent_id Agent identifier
     * @param load New load value
     */
    void updateAgentLoad(const std::string& agent_id, int load);
    
    // === Configuration ===
    
    /**
     * @brief Set routing strategy
     * @param strategy New strategy
     */
    void setStrategy(RoutingStrategy strategy);
    
    /**
     * @brief Get current routing strategy
     * @return Current strategy
     */
    RoutingStrategy getStrategy() const;
    
    // === Statistics ===
    
    /**
     * @brief Get total number of agents
     */
    size_t getAgentCount() const;
    
    /**
     * @brief Get number of healthy agents
     */
    size_t getHealthyAgentCount() const;

private:
    /**
     * @brief Analyze question to determine required skill
     * @param question Question content
     * @return Detected skill or empty string
     */
    std::string analyzeRequiredSkill(const std::string& question);
    
    /**
     * @brief Select agent using current strategy
     * @param candidates List of candidate agents
     * @return Selected agent
     */
    AgentInfo selectByStrategy(const std::vector<AgentInfo>& candidates);
    
    /**
     * @brief Select using round-robin strategy
     */
    AgentInfo selectRoundRobin(const std::vector<AgentInfo>& candidates);
    
    /**
     * @brief Select using random strategy
     */
    AgentInfo selectRandom(const std::vector<AgentInfo>& candidates);
    
    /**
     * @brief Select using least-load strategy
     */
    AgentInfo selectLeastLoad(const std::vector<AgentInfo>& candidates);
    
    mutable std::mutex agents_mutex_;
    std::unordered_map<std::string, AgentInfo> agents_;
    RoutingStrategy strategy_ = RoutingStrategy::SKILL_MATCH;
    std::atomic<size_t> round_robin_index_{0};
    std::mt19937 random_generator_;
    bool initialized_ = false;
};

} // namespace orchestrator
} // namespace agent_rpc
