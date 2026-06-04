/**
 * @file agent_info.h
 * @brief Agent information and routing strategy definitions
 * 
 * Task 8.2: 实现AgentInfo和RoutingStrategy
 * Requirements: 3.4
 */

#pragma once

#include <string>
#include <vector>
#include <chrono>
#include <optional>

namespace agent_rpc {
namespace orchestrator {

/**
 * @brief Agent information structure
 */
struct AgentInfo {
    std::string id;                              // Unique agent identifier
    std::string name;                            // Human-readable name
    std::string url;                             // Agent endpoint URL
    std::vector<std::string> skills;             // Agent capabilities/skills
    std::vector<std::string> tags;               // Additional tags for filtering
    bool is_healthy = true;                      // Health status
    std::chrono::steady_clock::time_point last_heartbeat;  // Last heartbeat time
    int current_load = 0;                        // Current task load (for LEAST_LOAD strategy)
    std::string description;                     // Agent description
    std::string version;                         // Agent version
    
    /**
     * @brief Check if agent has a specific skill
     */
    bool hasSkill(const std::string& skill) const {
        for (const auto& s : skills) {
            if (s == skill) return true;
        }
        return false;
    }
    
    /**
     * @brief Check if agent has a specific tag
     */
    bool hasTag(const std::string& tag) const {
        for (const auto& t : tags) {
            if (t == tag) return true;
        }
        return false;
    }
    
    /**
     * @brief Check if agent has all required skills
     */
    bool hasAllSkills(const std::vector<std::string>& required_skills) const {
        for (const auto& skill : required_skills) {
            if (!hasSkill(skill)) return false;
        }
        return true;
    }
    
    /**
     * @brief Check if agent has any of the required skills
     */
    bool hasAnySkill(const std::vector<std::string>& required_skills) const {
        for (const auto& skill : required_skills) {
            if (hasSkill(skill)) return true;
        }
        return required_skills.empty();
    }
};

/**
 * @brief Routing strategy enumeration
 */
enum class RoutingStrategy {
    ROUND_ROBIN,    // Distribute requests evenly in order
    RANDOM,         // Random selection
    SKILL_MATCH,    // Select based on skill matching
    LEAST_LOAD      // Select agent with lowest current load
};

/**
 * @brief Convert routing strategy to string
 */
inline std::string to_string(RoutingStrategy strategy) {
    switch (strategy) {
        case RoutingStrategy::ROUND_ROBIN: return "round_robin";
        case RoutingStrategy::RANDOM: return "random";
        case RoutingStrategy::SKILL_MATCH: return "skill_match";
        case RoutingStrategy::LEAST_LOAD: return "least_load";
        default: return "unknown";
    }
}

/**
 * @brief Parse routing strategy from string
 */
inline RoutingStrategy routing_strategy_from_string(const std::string& str) {
    if (str == "round_robin") return RoutingStrategy::ROUND_ROBIN;
    if (str == "random") return RoutingStrategy::RANDOM;
    if (str == "skill_match") return RoutingStrategy::SKILL_MATCH;
    if (str == "least_load") return RoutingStrategy::LEAST_LOAD;
    return RoutingStrategy::SKILL_MATCH;  // Default
}

} // namespace orchestrator
} // namespace agent_rpc
