#include <a2a/models/agent_message.hpp>
#include <json.hpp>
#include <sstream>

namespace a2a {

std::string AgentMessage::to_json() const {
    std::ostringstream oss;
    oss << "{";

    // Required fields
    oss << "\"messageId\":\"" << message_id_ << "\",";
    oss << "\"role\":\"" << to_string(role_) << "\"";

    // Optional fields
    if (context_id_.has_value()) {
        oss << ",\"contextId\":\"" << *context_id_ << "\"";
    }

    if (task_id_.has_value()) {
        oss << ",\"taskId\":\"" << *task_id_ << "\"";
    }

    // Parts array
    oss << ",\"parts\":[";
    for (size_t i = 0; i < parts_.size(); ++i) {
        if (i > 0) oss << ",";
        oss << parts_[i]->to_json();
    }
    oss << "]";

    oss << "}";
    return oss.str();
}

AgentMessage AgentMessage::from_json(const std::string& json) {
    AgentMessage msg;
    try {
        const nlohmann::json value = nlohmann::json::parse(json);
        if (!value.is_object()) return msg;

        if (value.contains("messageId") && value.at("messageId").is_string()) {
            msg.message_id_ = value.at("messageId").get<std::string>();
        }
        if (value.contains("role") && value.at("role").is_string()) {
            msg.role_ = message_role_from_string(value.at("role").get<std::string>());
        }
        if (value.contains("contextId") && value.at("contextId").is_string()) {
            msg.context_id_ = value.at("contextId").get<std::string>();
        }
        if (value.contains("taskId") && value.at("taskId").is_string()) {
            msg.task_id_ = value.at("taskId").get<std::string>();
        }
        if (value.contains("parts") && value.at("parts").is_array()) {
            for (const auto& part_value : value.at("parts")) {
                auto part = Part::from_json(part_value.dump());
                if (part) msg.parts_.push_back(std::move(part));
            }
        }
    } catch (const nlohmann::json::exception&) {
        // Preserve the existing non-throwing factory contract. Callers receive
        // an empty message and can treat it as an invalid A2A response.
    }

    return msg;
}

} // namespace a2a
