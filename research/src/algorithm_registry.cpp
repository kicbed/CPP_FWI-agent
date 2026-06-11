#include "agent_rpc/research/algorithm_registry.h"

#include <algorithm>
#include <fstream>
#include <sstream>

namespace agent_rpc::research {

namespace {

std::string join_errors(const std::vector<std::string>& errors) {
    std::ostringstream joined;
    for (size_t i = 0; i < errors.size(); ++i) {
        if (i > 0) joined << "; ";
        joined << errors[i];
    }
    return joined.str();
}

}  // namespace

bool AlgorithmRegistry::load_from_directory(const std::filesystem::path& directory,
                                            std::string* error) {
    if (error) error->clear();

    if (!std::filesystem::exists(directory)) {
        if (error) *error = "algorithm directory does not exist: " + directory.string();
        return false;
    }
    if (!std::filesystem::is_directory(directory)) {
        if (error) *error = "algorithm path is not a directory: " + directory.string();
        return false;
    }

    std::vector<std::filesystem::path> json_files;
    for (const auto& entry : std::filesystem::directory_iterator(directory)) {
        if (entry.is_regular_file() && entry.path().extension() == ".json") {
            json_files.push_back(entry.path());
        }
    }
    std::sort(json_files.begin(), json_files.end());

    std::vector<AlgorithmCard> loaded;
    for (const auto& path : json_files) {
        std::ifstream input(path);
        if (!input) {
            if (error) *error = "failed to open algorithm card: " + path.string();
            return false;
        }

        try {
            json value;
            input >> value;
            auto card = AlgorithmCard::from_json(value);
            auto validation_errors = card.validate();
            if (!validation_errors.empty()) {
                if (error) {
                    *error = "invalid algorithm card " + path.filename().string() +
                        ": " + join_errors(validation_errors);
                }
                return false;
            }
            loaded.push_back(std::move(card));
        } catch (const std::exception& ex) {
            if (error) {
                *error = "failed to parse algorithm card " + path.filename().string() +
                    ": " + ex.what();
            }
            return false;
        }
    }

    cards_ = std::move(loaded);
    return true;
}

const std::vector<AlgorithmCard>& AlgorithmRegistry::cards() const {
    return cards_;
}

const AlgorithmCard* AlgorithmRegistry::find_by_id(const std::string& id) const {
    const auto it = std::find_if(cards_.begin(), cards_.end(),
                                 [&id](const AlgorithmCard& card) {
                                     return card.id == id;
                                 });
    return it == cards_.end() ? nullptr : &(*it);
}

std::vector<AlgorithmCard> AlgorithmRegistry::filter_by_domain(
    const std::string& domain) const {
    std::vector<AlgorithmCard> matches;
    std::copy_if(cards_.begin(), cards_.end(), std::back_inserter(matches),
                 [&domain](const AlgorithmCard& card) {
                     return card.domain == domain;
                 });
    return matches;
}

std::vector<AlgorithmCard> AlgorithmRegistry::filter_by_tag(const std::string& tag) const {
    std::vector<AlgorithmCard> matches;
    std::copy_if(cards_.begin(), cards_.end(), std::back_inserter(matches),
                 [&tag](const AlgorithmCard& card) {
                     return std::find(card.tags.begin(), card.tags.end(), tag) !=
                         card.tags.end();
                 });
    return matches;
}

}  // namespace agent_rpc::research
