#include "code_agent_tools.hpp"

#include <algorithm>
#include <cctype>
#include <fstream>
#include <iterator>
#include <set>
#include <sstream>
#include <stdexcept>

namespace code_agent {
namespace {

namespace fs = std::filesystem;

bool path_is_within(const fs::path& child, const fs::path& parent) {
    auto child_it = child.begin();
    auto parent_it = parent.begin();

    for (; parent_it != parent.end(); ++parent_it, ++child_it) {
        if (child_it == child.end() || *child_it != *parent_it) {
            return false;
        }
    }

    return true;
}

bool is_ignored_component(const fs::path& component) {
    const auto name = component.generic_string();
    return name == ".git" ||
           name == ".codegraph" ||
           name == "build" ||
           name == "logs" ||
           name == "pids" ||
           name == "__pycache__";
}

bool has_ignored_component(const fs::path& relative_path) {
    for (const auto& component : relative_path) {
        if (is_ignored_component(component)) {
            return true;
        }
    }
    return false;
}

std::string trim_line(std::string line) {
    while (!line.empty() && (line.back() == '\r' || line.back() == '\n')) {
        line.pop_back();
    }
    return line;
}

bool looks_binary(const std::string& content) {
    return content.find('\0') != std::string::npos;
}

std::vector<std::string> query_terms(const std::string& query) {
    static const std::set<std::string> stop_words = {
        "about", "agent", "code", "does", "file", "find", "from", "have",
        "into", "list", "read", "show", "that", "the", "this", "what",
        "when", "where", "with"
    };

    std::vector<std::string> terms;
    std::set<std::string> seen;
    std::string current;

    auto flush = [&]() {
        if (current.size() >= 4 && stop_words.find(current) == stop_words.end() && seen.insert(current).second) {
            terms.push_back(current);
        }
        current.clear();
    };

    for (unsigned char ch : query) {
        if (std::isalnum(ch) || ch == '_' || ch == '-' || ch == '.') {
            current.push_back(static_cast<char>(std::tolower(ch)));
        } else {
            flush();
        }
    }
    flush();

    if (terms.size() > 5) {
        terms.resize(5);
    }
    return terms;
}

}  // namespace

ProjectInspector::ProjectInspector(const std::string& project_root) {
    if (project_root.empty()) {
        throw std::invalid_argument("project root is required");
    }

    project_root_ = fs::weakly_canonical(fs::path(project_root));
    if (!fs::exists(project_root_) || !fs::is_directory(project_root_)) {
        throw std::invalid_argument("project root must be an existing directory: " + project_root);
    }
}

std::vector<std::string> ProjectInspector::list_files(std::size_t max_entries) const {
    std::vector<std::string> files;
    if (max_entries == 0) {
        return files;
    }

    fs::recursive_directory_iterator it(project_root_, fs::directory_options::skip_permission_denied);
    const fs::recursive_directory_iterator end;
    for (; it != end; ++it) {
        const auto relative = fs::relative(it->path(), project_root_);
        if (it->is_directory() && has_ignored_component(relative)) {
            it.disable_recursion_pending();
            continue;
        }
        if (!it->is_regular_file() || has_ignored_component(relative)) {
            continue;
        }

        files.push_back(relative.generic_string());
    }

    std::sort(files.begin(), files.end());
    if (files.size() > max_entries) {
        files.resize(max_entries);
    }
    return files;
}

std::string ProjectInspector::read_file(const std::string& relative_path, std::size_t max_bytes) const {
    return read_file_content(resolve_safe_path(relative_path), max_bytes);
}

std::vector<SearchMatch> ProjectInspector::search_text(const std::string& needle,
                                                       std::size_t max_matches,
                                                       std::size_t max_file_bytes) const {
    std::vector<SearchMatch> matches;
    if (needle.empty() || max_matches == 0) {
        return matches;
    }

    const auto files = list_files(10000);
    for (const auto& file : files) {
        const auto absolute_path = resolve_safe_path(file);
        const auto content = read_file_content(absolute_path, max_file_bytes);
        if (looks_binary(content)) {
            continue;
        }

        std::istringstream input(content);
        std::string line;
        std::size_t line_number = 0;
        while (std::getline(input, line)) {
            ++line_number;
            if (line.find(needle) == std::string::npos) {
                continue;
            }

            matches.push_back(SearchMatch{file, line_number, trim_line(line)});
            if (matches.size() >= max_matches) {
                return matches;
            }
        }
    }

    return matches;
}

std::string ProjectInspector::summarize_for_query(const std::string& query) const {
    std::ostringstream summary;
    summary << "Read-only project inspection is available. No files were changed.\n";

    const auto files = list_files(80);
    summary << "\nProject files (first " << files.size() << "):\n";
    for (const auto& file : files) {
        summary << "- " << file << "\n";
    }

    const auto terms = query_terms(query);
    if (!terms.empty()) {
        summary << "\nSearch hints from the user query:\n";
        for (const auto& term : terms) {
            const auto matches = search_text(term, 5);
            if (matches.empty()) {
                continue;
            }
            summary << "Term `" << term << "`:\n";
            for (const auto& match : matches) {
                summary << "- " << match.path << ":" << match.line << ": " << match.text << "\n";
            }
        }
    }

    return summary.str();
}

fs::path ProjectInspector::resolve_safe_path(const std::string& relative_path) const {
    if (relative_path.empty()) {
        throw std::runtime_error("relative path is required");
    }

    const fs::path requested(relative_path);
    if (requested.is_absolute()) {
        throw std::runtime_error("absolute paths are not allowed: " + relative_path);
    }

    const auto absolute_path = fs::weakly_canonical(project_root_ / requested);
    if (!path_is_within(absolute_path, project_root_)) {
        throw std::runtime_error("path escapes project root: " + relative_path);
    }

    const auto safe_relative = fs::relative(absolute_path, project_root_);
    if (has_ignored_component(safe_relative)) {
        throw std::runtime_error("path is not available to Code Agent: " + relative_path);
    }

    if (!fs::exists(absolute_path) || !fs::is_regular_file(absolute_path)) {
        throw std::runtime_error("path is not a regular file: " + relative_path);
    }

    return absolute_path;
}

std::string ProjectInspector::read_file_content(const fs::path& absolute_path, std::size_t max_bytes) const {
    std::ifstream input(absolute_path, std::ios::binary);
    if (!input.is_open()) {
        throw std::runtime_error("failed to open file: " + absolute_path.generic_string());
    }

    std::string content((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
    if (content.size() <= max_bytes) {
        return content;
    }

    return content.substr(0, max_bytes) + "\n... (truncated)\n";
}

}  // namespace code_agent
