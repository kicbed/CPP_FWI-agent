#pragma once

#include <nlohmann/json.hpp>

#include <filesystem>
#include <map>
#include <string>
#include <vector>

namespace agent_rpc::research {

using json = nlohmann::json;

struct ResearchKnowledgeNote {
    std::string id;
    std::string title;
    std::string note_type;
    std::string summary;
    std::vector<std::string> methods;
    std::vector<std::string> datasets;
    std::vector<std::string> assumptions;
    std::vector<std::string> parameters;
    std::vector<std::string> failure_modes;
    std::map<std::string, std::string> parameter_advice;
    std::vector<std::string> tags;
    std::string source;

    json to_json() const;
    static ResearchKnowledgeNote from_json(const json& value);
    std::vector<std::string> validate() const;
    bool is_valid() const;
};

class ResearchKnowledgeBase {
public:
    bool load_from_directory(const std::filesystem::path& root,
                             std::string* error = nullptr);

    const std::vector<ResearchKnowledgeNote>& notes() const;
    const ResearchKnowledgeNote* find_by_id(const std::string& id) const;
    std::vector<ResearchKnowledgeNote> filter_by_note_type(
        const std::string& note_type) const;
    std::vector<ResearchKnowledgeNote> filter_by_method(
        const std::string& method) const;
    std::vector<ResearchKnowledgeNote> filter_by_dataset(
        const std::string& dataset) const;
    std::vector<ResearchKnowledgeNote> find_by_failure_mode(
        const std::string& failure_mode) const;
    std::vector<std::string> parameter_advice_for(
        const std::string& method,
        const std::string& parameter) const;

private:
    std::vector<ResearchKnowledgeNote> notes_;
};

}  // namespace agent_rpc::research
