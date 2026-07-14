#include <a2a/models/agent_task.hpp>
#include <sstream>

namespace a2a {

std::string AgentTask::to_json() const {
    std::ostringstream oss;
    oss << "{";

    // Required fields
    oss << "\"id\":\"" << id_ << "\",";
    oss << "\"contextId\":\"" << context_id_ << "\",";
    oss << "\"status\":" << status_.to_json();

    // Artifacts array
    if (!artifacts_.empty()) {
        oss << ",\"artifacts\":[";
        for (size_t i = 0; i < artifacts_.size(); ++i) {
            if (i > 0) oss << ",";
            oss << artifacts_[i].to_json();
        }
        oss << "]";
    }

    // History array
    if (!history_.empty()) {
        oss << ",\"history\":[";
        for (size_t i = 0; i < history_.size(); ++i) {
            if (i > 0) oss << ",";
            oss << history_[i].to_json();
        }
        oss << "]";
    }

    // Metadata
    if (!metadata_.empty()) {
        oss << ",\"metadata\":{";
        bool first = true;
        for (const auto& [key, value] : metadata_) {
            if (!first) oss << ",";
            oss << "\"" << key << "\":\"" << value << "\"";
            first = false;
        }
        oss << "}";
    }

    oss << "}";
    return oss.str();
}

AgentTask AgentTask::from_json(const std::string& json) {
    AgentTask task;

    // Extract id
    size_t id_pos = json.find("\"id\":");
    if (id_pos != std::string::npos) {
        size_t start = json.find("\"", id_pos + 5) + 1;
        size_t end = json.find("\"", start);
        task.id_ = json.substr(start, end - start);
    }

    // Extract contextId
    size_t ctx_pos = json.find("\"contextId\":");
    if (ctx_pos != std::string::npos) {
        size_t start = json.find("\"", ctx_pos + 12) + 1;
        size_t end = json.find("\"", start);
        task.context_id_ = json.substr(start, end - start);
    }

    // Extract status
    size_t status_pos = json.find("\"status\":");
    if (status_pos != std::string::npos) {
        size_t start = status_pos + 9;
        size_t brace_count = 0;
        size_t end = start;

        for (size_t i = start; i < json.length(); ++i) {
            if (json[i] == '{') brace_count++;
            else if (json[i] == '}') {
                brace_count--;
                if (brace_count == 0) {
                    end = i + 1;
                    break;
                }
            }
        }

        std::string status_json = json.substr(start, end - start);
        task.status_ = AgentTaskStatus::from_json(status_json);
    }

    // Extract artifacts
    size_t artifacts_pos = json.find("\"artifacts\":[");
    if (artifacts_pos != std::string::npos) {
        size_t array_start = artifacts_pos + 13;
        int bracket_count = 1;
        size_t array_end = array_start;

        for (size_t i = array_start; i < json.length() && bracket_count > 0; ++i) {
            if (json[i] == '[') bracket_count++;
            else if (json[i] == ']') {
                bracket_count--;
                if (bracket_count == 0) {
                    array_end = i;
                    break;
                }
            }
        }

        std::string artifacts_json = json.substr(array_start, array_end - array_start);

        // Parse each artifact
        int brace_count = 0;
        size_t item_start = 0;

        for (size_t i = 0; i < artifacts_json.length(); ++i) {
            if (artifacts_json[i] == '{') {
                if (brace_count == 0) item_start = i;
                brace_count++;
            } else if (artifacts_json[i] == '}') {
                brace_count--;
                if (brace_count == 0) {
                    std::string item_json = artifacts_json.substr(item_start, i - item_start + 1);
                    task.artifacts_.push_back(Artifact::from_json(item_json));
                }
            }
        }
    }

    // Extract history
    size_t history_pos = json.find("\"history\":[");
    if (history_pos != std::string::npos) {
        size_t array_start = history_pos + 11;
        int bracket_count = 1;
        size_t array_end = array_start;

        for (size_t i = array_start; i < json.length() && bracket_count > 0; ++i) {
            if (json[i] == '[') bracket_count++;
            else if (json[i] == ']') {
                bracket_count--;
                if (bracket_count == 0) {
                    array_end = i;
                    break;
                }
            }
        }

        std::string history_json = json.substr(array_start, array_end - array_start);

        // Parse each message in history
        int brace_count = 0;
        size_t item_start = 0;

        for (size_t i = 0; i < history_json.length(); ++i) {
            if (history_json[i] == '{') {
                if (brace_count == 0) item_start = i;
                brace_count++;
            } else if (history_json[i] == '}') {
                brace_count--;
                if (brace_count == 0) {
                    std::string item_json = history_json.substr(item_start, i - item_start + 1);
                    task.history_.push_back(AgentMessage::from_json(item_json));
                }
            }
        }
    }

    return task;
}

} // namespace a2a
