#include "agent_rpc/research/research_knowledge.h"

#include <algorithm>
#include <fstream>
#include <sstream>

namespace agent_rpc::research {

namespace {

const std::vector<std::string>& note_directories() {
    static const std::vector<std::string> directories = {
        "papers",
        "algorithms",
        "experiments",
        "failure_cases"
    };
    return directories;
}

bool contains(const std::vector<std::string>& values, const std::string& value) {
    return std::find(values.begin(), values.end(), value) != values.end();
}

bool is_supported_note_type(const std::string& note_type) {
    static const std::vector<std::string> note_types = {
        "paper",
        "algorithm",
        "experiment",
        "failure_case"
    };
    return contains(note_types, note_type);
}

std::string join_errors(const std::vector<std::string>& errors) {
    std::ostringstream joined;
    for (size_t i = 0; i < errors.size(); ++i) {
        if (i > 0) joined << "; ";
        joined << errors[i];
    }
    return joined.str();
}

std::vector<std::filesystem::path> collect_json_files(
    const std::filesystem::path& root) {
    std::vector<std::filesystem::path> json_files;

    for (const auto& directory : note_directories()) {
        const auto typed_dir = root / directory;
        if (!std::filesystem::exists(typed_dir)) {
            continue;
        }
        if (!std::filesystem::is_directory(typed_dir)) {
            continue;
        }
        for (const auto& entry : std::filesystem::directory_iterator(typed_dir)) {
            if (entry.is_regular_file() && entry.path().extension() == ".json") {
                json_files.push_back(entry.path());
            }
        }
    }

    std::sort(json_files.begin(), json_files.end());
    return json_files;
}

}  // namespace

json ResearchKnowledgeNote::to_json() const {
    return {
        {"id", id},
        {"title", title},
        {"note_type", note_type},
        {"summary", summary},
        {"methods", methods},
        {"datasets", datasets},
        {"assumptions", assumptions},
        {"parameters", parameters},
        {"failure_modes", failure_modes},
        {"parameter_advice", parameter_advice},
        {"tags", tags},
        {"source", source}
    };
}

ResearchKnowledgeNote ResearchKnowledgeNote::from_json(const json& value) {
    ResearchKnowledgeNote note;
    note.id = value.value("id", "");
    note.title = value.value("title", "");
    note.note_type = value.value("note_type", "");
    note.summary = value.value("summary", "");
    note.methods = value.value("methods", std::vector<std::string>{});
    note.datasets = value.value("datasets", std::vector<std::string>{});
    note.assumptions = value.value("assumptions", std::vector<std::string>{});
    note.parameters = value.value("parameters", std::vector<std::string>{});
    note.failure_modes = value.value("failure_modes", std::vector<std::string>{});
    note.parameter_advice = value.value(
        "parameter_advice",
        std::map<std::string, std::string>{});
    note.tags = value.value("tags", std::vector<std::string>{});
    note.source = value.value("source", "");
    return note;
}

std::vector<std::string> ResearchKnowledgeNote::validate() const {
    std::vector<std::string> errors;

    if (id.empty()) errors.push_back("id is required");
    if (title.empty()) errors.push_back("title is required");
    if (note_type.empty()) {
        errors.push_back("note_type is required");
    } else if (!is_supported_note_type(note_type)) {
        errors.push_back("note_type must be paper, algorithm, experiment, or failure_case");
    }
    if (summary.empty()) errors.push_back("summary is required");
    if (methods.empty()) errors.push_back("methods must not be empty");

    return errors;
}

bool ResearchKnowledgeNote::is_valid() const {
    return validate().empty();
}

bool ResearchKnowledgeBase::load_from_directory(const std::filesystem::path& root,
                                                std::string* error) {
    if (error) error->clear();

    if (!std::filesystem::exists(root)) {
        if (error) *error = "research knowledge directory does not exist: " + root.string();
        return false;
    }
    if (!std::filesystem::is_directory(root)) {
        if (error) *error = "research knowledge path is not a directory: " + root.string();
        return false;
    }

    std::vector<ResearchKnowledgeNote> loaded;
    for (const auto& path : collect_json_files(root)) {
        std::ifstream input(path);
        if (!input) {
            if (error) *error = "failed to open research knowledge note: " + path.string();
            return false;
        }

        try {
            json value;
            input >> value;
            auto note = ResearchKnowledgeNote::from_json(value);
            auto validation_errors = note.validate();
            if (!validation_errors.empty()) {
                if (error) {
                    *error = "invalid research knowledge note " +
                        path.filename().string() + ": " + join_errors(validation_errors);
                }
                return false;
            }
            loaded.push_back(std::move(note));
        } catch (const std::exception& ex) {
            if (error) {
                *error = "failed to parse research knowledge note " +
                    path.filename().string() + ": " + ex.what();
            }
            return false;
        }
    }

    notes_ = std::move(loaded);
    return true;
}

const std::vector<ResearchKnowledgeNote>& ResearchKnowledgeBase::notes() const {
    return notes_;
}

const ResearchKnowledgeNote* ResearchKnowledgeBase::find_by_id(
    const std::string& id) const {
    const auto it = std::find_if(notes_.begin(), notes_.end(),
                                 [&id](const ResearchKnowledgeNote& note) {
                                     return note.id == id;
                                 });
    return it == notes_.end() ? nullptr : &(*it);
}

std::vector<ResearchKnowledgeNote> ResearchKnowledgeBase::filter_by_note_type(
    const std::string& note_type) const {
    std::vector<ResearchKnowledgeNote> matches;
    std::copy_if(notes_.begin(), notes_.end(), std::back_inserter(matches),
                 [&note_type](const ResearchKnowledgeNote& note) {
                     return note.note_type == note_type;
                 });
    return matches;
}

std::vector<ResearchKnowledgeNote> ResearchKnowledgeBase::filter_by_method(
    const std::string& method) const {
    std::vector<ResearchKnowledgeNote> matches;
    std::copy_if(notes_.begin(), notes_.end(), std::back_inserter(matches),
                 [&method](const ResearchKnowledgeNote& note) {
                     return contains(note.methods, method);
                 });
    return matches;
}

std::vector<ResearchKnowledgeNote> ResearchKnowledgeBase::filter_by_dataset(
    const std::string& dataset) const {
    std::vector<ResearchKnowledgeNote> matches;
    std::copy_if(notes_.begin(), notes_.end(), std::back_inserter(matches),
                 [&dataset](const ResearchKnowledgeNote& note) {
                     return contains(note.datasets, dataset);
                 });
    return matches;
}

std::vector<ResearchKnowledgeNote> ResearchKnowledgeBase::find_by_failure_mode(
    const std::string& failure_mode) const {
    std::vector<ResearchKnowledgeNote> matches;
    std::copy_if(notes_.begin(), notes_.end(), std::back_inserter(matches),
                 [&failure_mode](const ResearchKnowledgeNote& note) {
                     return contains(note.failure_modes, failure_mode);
                 });
    std::stable_sort(matches.begin(), matches.end(),
                     [](const ResearchKnowledgeNote& lhs,
                        const ResearchKnowledgeNote& rhs) {
                         if (lhs.note_type == rhs.note_type) {
                             return lhs.id < rhs.id;
                         }
                         if (lhs.note_type == "failure_case") return true;
                         if (rhs.note_type == "failure_case") return false;
                         return lhs.note_type < rhs.note_type;
                     });
    return matches;
}

std::vector<std::string> ResearchKnowledgeBase::parameter_advice_for(
    const std::string& method,
    const std::string& parameter) const {
    std::vector<std::string> advice;
    for (const auto& note : notes_) {
        if (!contains(note.methods, method)) {
            continue;
        }
        const auto it = note.parameter_advice.find(parameter);
        if (it != note.parameter_advice.end()) {
            advice.push_back(it->second);
        }
    }
    return advice;
}

}  // namespace agent_rpc::research
