#pragma once

#include <cstddef>
#include <filesystem>
#include <string>
#include <vector>

namespace code_agent {

struct SearchMatch {
    std::string path;
    std::size_t line = 0;
    std::string text;
};

class ProjectInspector {
public:
    explicit ProjectInspector(const std::string& project_root);

    std::vector<std::string> list_files(std::size_t max_entries = 200) const;
    std::string read_file(const std::string& relative_path, std::size_t max_bytes = 20000) const;
    std::vector<SearchMatch> search_text(const std::string& needle,
                                         std::size_t max_matches = 50,
                                         std::size_t max_file_bytes = 200000) const;
    std::string summarize_for_query(const std::string& query) const;

private:
    std::filesystem::path resolve_safe_path(const std::string& relative_path) const;
    std::string read_file_content(const std::filesystem::path& absolute_path,
                                  std::size_t max_bytes) const;

    std::filesystem::path project_root_;
};

}  // namespace code_agent
