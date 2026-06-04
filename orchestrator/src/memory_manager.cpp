/**
 * @file memory_manager.cpp
 * @brief MemoryManager implementation
 */

#include "agent_rpc/orchestrator/memory_manager.h"
#include <iostream>
#include <cstdarg>

namespace agent_rpc {
namespace orchestrator {

MemoryManager::MemoryManager(const std::string& redis_host, int redis_port)
    : context_(nullptr)
    , host_(redis_host)
    , port_(redis_port) {

    context_ = redisConnect(redis_host.c_str(), redis_port);

    if (context_ == nullptr || context_->err) {
        if (context_) {
            std::string error = context_->errstr;
            redisFree(context_);
            throw std::runtime_error("MemoryManager Redis 连接失败: " + error);
        } else {
            throw std::runtime_error("MemoryManager Redis 连接失败: 无法分配 context");
        }
    }
}

MemoryManager::~MemoryManager() {
    if (context_) {
        redisFree(context_);
    }
}

void MemoryManager::ensure_connection() {
    if (context_ && !context_->err) {
        return;
    }

    if (context_) {
        redisFree(context_);
    }

    context_ = redisConnect(host_.c_str(), port_);

    if (context_ == nullptr || context_->err) {
        throw std::runtime_error("MemoryManager Redis 重连失败");
    }
}

redisReply* MemoryManager::execute_command(const char* format, ...) {
    std::lock_guard<std::mutex> lock(mutex_);

    ensure_connection();

    va_list args;
    va_start(args, format);
    redisReply* reply = static_cast<redisReply*>(redisvCommand(context_, format, args));
    va_end(args);

    if (reply == nullptr) {
        throw std::runtime_error("MemoryManager Redis 命令执行失败");
    }

    if (reply->type == REDIS_REPLY_ERROR) {
        std::string error = reply->str;
        freeReplyObject(reply);
        throw std::runtime_error("MemoryManager Redis 错误: " + error);
    }

    return reply;
}

// ============================================================================
// Session Memory
// ============================================================================

void MemoryManager::save_session_message(const std::string& context_id,
                                         const a2a::AgentMessage& message) {
    try {
        std::string json_str = message.to_json();

        auto reply = execute_command("RPUSH %s %s",
                                    session_key(context_id).c_str(),
                                    json_str.c_str());
        freeReplyObject(reply);

        // Limit to 1000 messages
        reply = execute_command("LTRIM %s -1000 -1",
                               session_key(context_id).c_str());
        freeReplyObject(reply);

    } catch (const std::exception& e) {
        std::cerr << "[MemoryManager] save_session_message 错误: " << e.what() << std::endl;
    }
}

std::vector<a2a::AgentMessage> MemoryManager::get_session_history(
    const std::string& context_id, int limit) {

    std::vector<a2a::AgentMessage> history;

    try {
        redisReply* reply;

        if (limit <= 0) {
            reply = execute_command("LRANGE %s 0 -1",
                                   session_key(context_id).c_str());
        } else {
            reply = execute_command("LRANGE %s -%d -1",
                                   session_key(context_id).c_str(),
                                   limit);
        }

        if (reply->type == REDIS_REPLY_ARRAY) {
            for (size_t i = 0; i < reply->elements; ++i) {
                if (reply->element[i]->type == REDIS_REPLY_STRING) {
                    std::string json_str(reply->element[i]->str, reply->element[i]->len);
                    try {
                        auto message = a2a::AgentMessage::from_json(json_str);
                        history.push_back(message);
                    } catch (const std::exception& e) {
                        std::cerr << "[MemoryManager] 反序列化消息失败: " << e.what() << std::endl;
                    }
                }
            }
        }

        freeReplyObject(reply);

    } catch (const std::exception& e) {
        std::cerr << "[MemoryManager] get_session_history 错误: " << e.what() << std::endl;
    }

    return history;
}

// ============================================================================
// Agent Memory
// ============================================================================

void MemoryManager::save_agent_memory(const std::string& agent_id,
                                      const std::string& context_id,
                                      const json& step) {
    try {
        std::string json_str = step.dump();

        auto reply = execute_command("RPUSH %s %s",
                                    agent_key(agent_id, context_id).c_str(),
                                    json_str.c_str());
        freeReplyObject(reply);

        // Limit to 500 steps per agent per context
        reply = execute_command("LTRIM %s -500 -1",
                               agent_key(agent_id, context_id).c_str());
        freeReplyObject(reply);

    } catch (const std::exception& e) {
        std::cerr << "[MemoryManager] save_agent_memory 错误: " << e.what() << std::endl;
    }
}

std::vector<json> MemoryManager::get_agent_memory(const std::string& agent_id,
                                                   const std::string& context_id,
                                                   int limit) {
    std::vector<json> memory;

    try {
        redisReply* reply;

        if (limit <= 0) {
            reply = execute_command("LRANGE %s 0 -1",
                                   agent_key(agent_id, context_id).c_str());
        } else {
            reply = execute_command("LRANGE %s -%d -1",
                                   agent_key(agent_id, context_id).c_str(),
                                   limit);
        }

        if (reply->type == REDIS_REPLY_ARRAY) {
            for (size_t i = 0; i < reply->elements; ++i) {
                if (reply->element[i]->type == REDIS_REPLY_STRING) {
                    std::string json_str(reply->element[i]->str, reply->element[i]->len);
                    try {
                        memory.push_back(json::parse(json_str));
                    } catch (const std::exception& e) {
                        std::cerr << "[MemoryManager] 反序列化 agent memory 失败: " << e.what() << std::endl;
                    }
                }
            }
        }

        freeReplyObject(reply);

    } catch (const std::exception& e) {
        std::cerr << "[MemoryManager] get_agent_memory 错误: " << e.what() << std::endl;
    }

    return memory;
}

// ============================================================================
// Task State
// ============================================================================

void MemoryManager::save_task_state(const std::string& task_id, const json& state) {
    try {
        std::string json_str = state.dump();

        auto reply = execute_command("SET %s %s",
                                    task_key(task_id).c_str(),
                                    json_str.c_str());
        freeReplyObject(reply);

    } catch (const std::exception& e) {
        std::cerr << "[MemoryManager] save_task_state 错误: " << e.what() << std::endl;
    }
}

json MemoryManager::get_task_state(const std::string& task_id) {
    try {
        auto reply = execute_command("GET %s", task_key(task_id).c_str());

        if (reply->type == REDIS_REPLY_NIL) {
            freeReplyObject(reply);
            return json::object();
        }

        std::string json_str(reply->str, reply->len);
        freeReplyObject(reply);

        return json::parse(json_str);

    } catch (const std::exception& e) {
        std::cerr << "[MemoryManager] get_task_state 错误: " << e.what() << std::endl;
        return json::object();
    }
}

bool MemoryManager::task_exists(const std::string& task_id) {
    try {
        auto reply = execute_command("EXISTS %s", task_key(task_id).c_str());
        bool exists = (reply->integer == 1);
        freeReplyObject(reply);
        return exists;

    } catch (const std::exception& e) {
        std::cerr << "[MemoryManager] task_exists 错误: " << e.what() << std::endl;
        return false;
    }
}

// ============================================================================
// Legacy Compatibility
// ============================================================================

std::vector<a2a::AgentMessage> MemoryManager::get_legacy_history(
    const std::string& context_id, int limit) {

    std::vector<a2a::AgentMessage> history;

    try {
        redisReply* reply;

        if (limit <= 0) {
            reply = execute_command("LRANGE %s 0 -1",
                                   legacy_history_key(context_id).c_str());
        } else {
            reply = execute_command("LRANGE %s -%d -1",
                                   legacy_history_key(context_id).c_str(),
                                   limit);
        }

        if (reply->type == REDIS_REPLY_ARRAY) {
            for (size_t i = 0; i < reply->elements; ++i) {
                if (reply->element[i]->type == REDIS_REPLY_STRING) {
                    std::string json_str(reply->element[i]->str, reply->element[i]->len);
                    try {
                        auto message = a2a::AgentMessage::from_json(json_str);
                        history.push_back(message);
                    } catch (const std::exception& e) {
                        // Ignore deserialization errors for legacy data
                    }
                }
            }
        }

        freeReplyObject(reply);

    } catch (const std::exception& e) {
        std::cerr << "[MemoryManager] get_legacy_history 错误: " << e.what() << std::endl;
    }

    return history;
}

void MemoryManager::migrate_legacy_keys(const std::string& context_id) {
    try {
        // Check if legacy history exists
        auto reply = execute_command("EXISTS %s",
                                    legacy_history_key(context_id).c_str());
        bool exists = (reply->integer == 1);
        freeReplyObject(reply);

        if (!exists) {
            return;  // Nothing to migrate
        }

        // Check if new session key already has data
        reply = execute_command("EXISTS %s",
                               session_key(context_id).c_str());
        bool new_exists = (reply->integer == 1);
        freeReplyObject(reply);

        if (new_exists) {
            return;  // Already migrated
        }

        // Copy legacy history to new session key
        auto history = get_legacy_history(context_id);
        for (const auto& msg : history) {
            save_session_message(context_id, msg);
        }

        std::cout << "[MemoryManager] 已迁移 legacy key: " << context_id
                  << " (" << history.size() << " 条消息)" << std::endl;

    } catch (const std::exception& e) {
        std::cerr << "[MemoryManager] migrate_legacy_keys 错误: " << e.what() << std::endl;
    }
}

// ============================================================================
// Utility
// ============================================================================

std::vector<std::string> MemoryManager::get_keys(const std::string& pattern) {
    std::vector<std::string> keys;

    try {
        auto reply = execute_command("KEYS %s", pattern.c_str());

        if (reply->type == REDIS_REPLY_ARRAY) {
            for (size_t i = 0; i < reply->elements; ++i) {
                if (reply->element[i]->type == REDIS_REPLY_STRING) {
                    keys.push_back(reply->element[i]->str);
                }
            }
        }

        freeReplyObject(reply);

    } catch (const std::exception& e) {
        std::cerr << "[MemoryManager] get_keys 错误: " << e.what() << std::endl;
    }

    return keys;
}

} // namespace orchestrator
} // namespace agent_rpc
