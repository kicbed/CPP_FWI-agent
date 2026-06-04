/**
 * @file agent_router.cpp
 * @brief Agent Router implementation
 * 
 * Task 8.3: 实现AgentRouter路由器
 */

#include "agent_rpc/orchestrator/agent_router.h"
#include <algorithm>
#include <cctype>

namespace agent_rpc {
namespace orchestrator {

AgentRouter::AgentRouter() 
    : random_generator_(std::random_device{}()) {}

AgentRouter::~AgentRouter() {
    shutdown();
}

bool AgentRouter::initialize(RoutingStrategy strategy) {
    if (initialized_) {
        return true;
    }
    
    strategy_ = strategy;
    round_robin_index_ = 0;
    initialized_ = true;
    return true;
}

void AgentRouter::shutdown() {
    if (!initialized_) {
        return;
    }
    
    std::lock_guard<std::mutex> lock(agents_mutex_);
    agents_.clear();
    initialized_ = false;
}

std::optional<AgentInfo> AgentRouter::selectAgent(
    const std::string& question,
    const std::vector<std::string>& required_skills) {
    
    std::lock_guard<std::mutex> lock(agents_mutex_);
    
    if (agents_.empty()) {
        return std::nullopt;
    }
    
    // Build candidate list
    std::vector<AgentInfo> candidates;
    
    // Determine skills to match
    std::vector<std::string> skills_to_match = required_skills;
    if (skills_to_match.empty() && strategy_ == RoutingStrategy::SKILL_MATCH) {
        // Analyze question to determine skill
        std::string detected_skill = analyzeRequiredSkill(question);
        if (!detected_skill.empty()) {
            skills_to_match.push_back(detected_skill);
        }
    }
    
    // Filter agents
    for (const auto& [id, agent] : agents_) {
        // Skip unhealthy agents
        if (!agent.is_healthy) {
            continue;
        }
        
        // Check skill requirements
        if (!skills_to_match.empty()) {
            if (!agent.hasAnySkill(skills_to_match)) {
                continue;
            }
        }
        
        candidates.push_back(agent);
    }
    
    if (candidates.empty()) {
        // No matching agents, try all healthy agents as fallback
        for (const auto& [id, agent] : agents_) {
            if (agent.is_healthy) {
                candidates.push_back(agent);
            }
        }
    }
    
    if (candidates.empty()) {
        return std::nullopt;
    }
    
    return selectByStrategy(candidates);
}

std::vector<AgentInfo> AgentRouter::findAgentsBySkill(const std::string& skill) {
    std::lock_guard<std::mutex> lock(agents_mutex_);
    
    std::vector<AgentInfo> result;
    for (const auto& [id, agent] : agents_) {
        if (agent.hasSkill(skill)) {
            result.push_back(agent);
        }
    }
    return result;
}

std::vector<AgentInfo> AgentRouter::findAgentsByTags(const std::vector<std::string>& tags) {
    std::lock_guard<std::mutex> lock(agents_mutex_);
    
    std::vector<AgentInfo> result;
    for (const auto& [id, agent] : agents_) {
        bool has_all_tags = true;
        for (const auto& tag : tags) {
            if (!agent.hasTag(tag)) {
                has_all_tags = false;
                break;
            }
        }
        if (has_all_tags) {
            result.push_back(agent);
        }
    }
    return result;
}

std::vector<AgentInfo> AgentRouter::findHealthyAgentsWithSkills(
    const std::vector<std::string>& required_skills) {
    
    std::lock_guard<std::mutex> lock(agents_mutex_);
    
    std::vector<AgentInfo> result;
    for (const auto& [id, agent] : agents_) {
        if (agent.is_healthy && agent.hasAnySkill(required_skills)) {
            result.push_back(agent);
        }
    }
    return result;
}

void AgentRouter::updateAgentList(const std::vector<AgentInfo>& agents) {
    std::lock_guard<std::mutex> lock(agents_mutex_);
    
    agents_.clear();
    for (const auto& agent : agents) {
        agents_[agent.id] = agent;
    }
}

void AgentRouter::addAgent(const AgentInfo& agent) {
    std::lock_guard<std::mutex> lock(agents_mutex_);
    agents_[agent.id] = agent;
}

bool AgentRouter::removeAgent(const std::string& agent_id) {
    std::lock_guard<std::mutex> lock(agents_mutex_);
    return agents_.erase(agent_id) > 0;
}

std::optional<AgentInfo> AgentRouter::getAgent(const std::string& agent_id) {
    std::lock_guard<std::mutex> lock(agents_mutex_);
    
    auto it = agents_.find(agent_id);
    if (it != agents_.end()) {
        return it->second;
    }
    return std::nullopt;
}

std::vector<AgentInfo> AgentRouter::getAllAgents() {
    std::lock_guard<std::mutex> lock(agents_mutex_);
    
    std::vector<AgentInfo> result;
    result.reserve(agents_.size());
    for (const auto& [id, agent] : agents_) {
        result.push_back(agent);
    }
    return result;
}

std::vector<AgentInfo> AgentRouter::getHealthyAgents() {
    std::lock_guard<std::mutex> lock(agents_mutex_);
    
    std::vector<AgentInfo> result;
    for (const auto& [id, agent] : agents_) {
        if (agent.is_healthy) {
            result.push_back(agent);
        }
    }
    return result;
}

void AgentRouter::markAgentUnhealthy(const std::string& agent_id) {
    std::lock_guard<std::mutex> lock(agents_mutex_);
    
    auto it = agents_.find(agent_id);
    if (it != agents_.end()) {
        it->second.is_healthy = false;
    }
}

void AgentRouter::markAgentHealthy(const std::string& agent_id) {
    std::lock_guard<std::mutex> lock(agents_mutex_);
    
    auto it = agents_.find(agent_id);
    if (it != agents_.end()) {
        it->second.is_healthy = true;
        it->second.last_heartbeat = std::chrono::steady_clock::now();
    }
}

bool AgentRouter::isAgentHealthy(const std::string& agent_id) {
    std::lock_guard<std::mutex> lock(agents_mutex_);
    
    auto it = agents_.find(agent_id);
    if (it != agents_.end()) {
        return it->second.is_healthy;
    }
    return false;
}

void AgentRouter::updateHeartbeat(const std::string& agent_id) {
    std::lock_guard<std::mutex> lock(agents_mutex_);
    
    auto it = agents_.find(agent_id);
    if (it != agents_.end()) {
        it->second.last_heartbeat = std::chrono::steady_clock::now();
    }
}

void AgentRouter::updateAgentLoad(const std::string& agent_id, int load) {
    std::lock_guard<std::mutex> lock(agents_mutex_);
    
    auto it = agents_.find(agent_id);
    if (it != agents_.end()) {
        it->second.current_load = load;
    }
}

void AgentRouter::setStrategy(RoutingStrategy strategy) {
    strategy_ = strategy;
}

RoutingStrategy AgentRouter::getStrategy() const {
    return strategy_;
}

size_t AgentRouter::getAgentCount() const {
    std::lock_guard<std::mutex> lock(agents_mutex_);
    return agents_.size();
}

size_t AgentRouter::getHealthyAgentCount() const {
    std::lock_guard<std::mutex> lock(agents_mutex_);
    
    size_t count = 0;
    for (const auto& [id, agent] : agents_) {
        if (agent.is_healthy) {
            count++;
        }
    }
    return count;
}

std::string AgentRouter::analyzeRequiredSkill(const std::string& question) {
    // Simple keyword-based skill detection
    std::string lower_question;
    lower_question.reserve(question.size());
    for (char c : question) {
        lower_question += static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    }
    
    // Math-related keywords
    if (lower_question.find("math") != std::string::npos ||
        lower_question.find("calculate") != std::string::npos ||
        lower_question.find("compute") != std::string::npos ||
        lower_question.find("equation") != std::string::npos ||
        lower_question.find("数学") != std::string::npos ||
        lower_question.find("计算") != std::string::npos) {
        return "math";
    }
    
    // Code-related keywords
    if (lower_question.find("code") != std::string::npos ||
        lower_question.find("program") != std::string::npos ||
        lower_question.find("function") != std::string::npos ||
        lower_question.find("debug") != std::string::npos ||
        lower_question.find("代码") != std::string::npos ||
        lower_question.find("编程") != std::string::npos) {
        return "coding";
    }
    
    // Writing-related keywords
    if (lower_question.find("write") != std::string::npos ||
        lower_question.find("essay") != std::string::npos ||
        lower_question.find("article") != std::string::npos ||
        lower_question.find("写作") != std::string::npos ||
        lower_question.find("文章") != std::string::npos) {
        return "writing";
    }
    
    // Translation-related keywords
    if (lower_question.find("translate") != std::string::npos ||
        lower_question.find("翻译") != std::string::npos) {
        return "translation";
    }
    
    return "";  // No specific skill detected
}

AgentInfo AgentRouter::selectByStrategy(const std::vector<AgentInfo>& candidates) {
    if (candidates.size() == 1) {
        return candidates[0];
    }
    
    switch (strategy_) {
        case RoutingStrategy::ROUND_ROBIN:
            return selectRoundRobin(candidates);
        case RoutingStrategy::RANDOM:
            return selectRandom(candidates);
        case RoutingStrategy::LEAST_LOAD:
            return selectLeastLoad(candidates);
        case RoutingStrategy::SKILL_MATCH:
        default:
            // For skill match, candidates are already filtered, use round-robin
            return selectRoundRobin(candidates);
    }
}

AgentInfo AgentRouter::selectRoundRobin(const std::vector<AgentInfo>& candidates) {
    size_t index = round_robin_index_.fetch_add(1) % candidates.size();
    return candidates[index];
}

AgentInfo AgentRouter::selectRandom(const std::vector<AgentInfo>& candidates) {
    std::uniform_int_distribution<size_t> dist(0, candidates.size() - 1);
    return candidates[dist(random_generator_)];
}

AgentInfo AgentRouter::selectLeastLoad(const std::vector<AgentInfo>& candidates) {
    auto min_it = std::min_element(candidates.begin(), candidates.end(),
        [](const AgentInfo& a, const AgentInfo& b) {
            return a.current_load < b.current_load;
        });
    return *min_it;
}

} // namespace orchestrator
} // namespace agent_rpc
