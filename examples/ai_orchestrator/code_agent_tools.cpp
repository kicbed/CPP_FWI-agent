#include "code_agent_tools.hpp"

#include <algorithm>
#include <cctype>
#include <fstream>
#include <set>
#include <sstream>
#include <stdexcept>
#include <system_error>

namespace code_agent {
namespace {

namespace fs = std::filesystem;

// Project inspection is intentionally conservative: these limits apply even if a
// caller asks for more. This keeps repository contents from becoming an unbounded
// prompt or memory allocation.
constexpr std::size_t kMaxListedFiles = 10000;
constexpr std::size_t kMaxInspectableFileBytes = 1024 * 1024;
constexpr std::size_t kMaxReadOutputBytes = 64 * 1024;
constexpr std::size_t kMaxSearchFileBytes = 512 * 1024;
constexpr std::size_t kMaxSearchTotalInputBytes = 8 * 1024 * 1024;
constexpr std::size_t kMaxSearchOutputBytes = 64 * 1024;
constexpr std::size_t kMaxSearchMatches = 100;
constexpr std::size_t kMaxQueryBytes = 256;
constexpr std::size_t kMaxLineBytes = 4096;
constexpr std::size_t kMaxMatchTextBytes = 1024;

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

std::string lowercase(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return value;
}

bool starts_with(const std::string& value, const std::string& prefix) {
    return value.size() >= prefix.size() &&
           value.compare(0, prefix.size(), prefix) == 0;
}

bool is_ignored_component(const fs::path& component) {
    const auto name = lowercase(component.generic_string());
    return name == ".git" ||
           name == ".codegraph" ||
           name == "build" ||
           name == "logs" ||
           name == "pids" ||
           name == "__pycache__" ||
           name == "node_modules" ||
           name == ".venv" ||
           name == "venv";
}

bool is_sensitive_component(const fs::path& component) {
    const auto name = lowercase(component.filename().generic_string());
    if (name.empty()) {
        return false;
    }

    if (name == ".env" || starts_with(name, ".env.")) {
        return true;
    }

    static const std::set<std::string> sensitive_names = {
        ".aws", ".envrc", ".gnupg", ".netrc", ".npmrc", ".pypirc", ".ssh",
        "cert", "certs", "certificate", "certificates", "credential",
        "credentials", "keys", "keystore", "password", "passwords", "passwd",
        "secret", "secrets", "token", "tokens", "truststore", "id_dsa",
        "id_ecdsa", "id_ed25519", "id_rsa", ".git-credentials"
    };
    if (sensitive_names.find(name) != sensitive_names.end()) {
        return true;
    }

    static const std::set<std::string> sensitive_extensions = {
        ".asc", ".cer", ".cert", ".crt", ".der", ".gpg", ".jks",
        ".key", ".kdbx", ".keystore", ".p12", ".pem", ".pfx",
        ".pkcs8", ".pkcs12"
    };
    if (sensitive_extensions.find(lowercase(component.extension().generic_string())) !=
        sensitive_extensions.end()) {
        return true;
    }

    std::string token;
    std::set<std::string> tokens;
    for (unsigned char ch : name) {
        if (std::isalnum(ch)) {
            token.push_back(static_cast<char>(ch));
        } else if (!token.empty()) {
            tokens.insert(token);
            token.clear();
        }
    }
    if (!token.empty()) {
        tokens.insert(token);
    }

    static const std::set<std::string> sensitive_tokens = {
        "cert", "certs", "certificate", "certificates", "credential",
        "credentials", "keys", "keystore", "passwd", "password", "passwords",
        "privatekey", "secret", "secrets", "token", "tokens", "truststore"
    };
    for (const auto& word : tokens) {
        if (sensitive_tokens.find(word) != sensitive_tokens.end()) {
            return true;
        }
    }

    return name.find("apikey") != std::string::npos ||
           name.find("api_key") != std::string::npos ||
           name.find("api-key") != std::string::npos ||
           name.find("clientsecret") != std::string::npos ||
           name.find("client_secret") != std::string::npos ||
           name.find("client-secret") != std::string::npos ||
           name.find("privatekey") != std::string::npos ||
           name.find("private_key") != std::string::npos ||
           name.find("private-key") != std::string::npos;
}

bool has_ignored_component(const fs::path& relative_path) {
    for (const auto& component : relative_path) {
        if (is_ignored_component(component)) {
            return true;
        }
    }
    return false;
}

bool has_sensitive_component(const fs::path& relative_path) {
    for (const auto& component : relative_path) {
        if (is_sensitive_component(component)) {
            return true;
        }
    }
    return false;
}

bool is_allowed_text_path(const fs::path& relative_path) {
    const auto filename = lowercase(relative_path.filename().generic_string());
    static const std::set<std::string> allowed_extensionless_names = {
        "dockerfile", "license", "makefile", "notice", "readme"
    };
    if (allowed_extensionless_names.find(filename) != allowed_extensionless_names.end()) {
        return true;
    }

    static const std::set<std::string> allowed_dotfiles = {
        ".clang-format", ".clang-tidy", ".dockerignore", ".editorconfig", ".gitignore"
    };
    if (allowed_dotfiles.find(filename) != allowed_dotfiles.end()) {
        return true;
    }

    static const std::set<std::string> allowed_extensions = {
        ".c", ".cc", ".cmake", ".cpp", ".css", ".cu", ".cuh", ".go",
        ".h", ".hh", ".hpp", ".html", ".in", ".java", ".js", ".json",
        ".jsx", ".md", ".proto", ".py", ".rb", ".rs", ".rst", ".sh",
        ".sql", ".toml", ".ts", ".tsx", ".txt", ".xml", ".yaml", ".yml"
    };
    return allowed_extensions.find(lowercase(relative_path.extension().generic_string())) !=
           allowed_extensions.end();
}

bool is_sensitive_query(const std::string& query) {
    const auto value = lowercase(query);
    static const char* patterns[] = {
        "api key", "api.key", "api_key", "api-key", "apikey",
        "access key", "access_token", "access-token",
        "auth_token", "auth-token", "bearer token", "client_secret",
        "client-secret", "clientsecret", "credential", "private_key",
        "private-key", "private key", "password", "passwd", "refresh_token",
        "refresh-token", "secret", "security_token", "security-token"
    };
    for (const auto* pattern : patterns) {
        if (value.find(pattern) != std::string::npos) {
            return true;
        }
    }
    return false;
}

bool contains_prefixed_token(const std::string& value,
                             const std::string& prefix,
                             std::size_t minimum_suffix_length) {
    std::size_t position = value.find(prefix);
    while (position != std::string::npos) {
        std::size_t length = 0;
        const auto start = position + prefix.size();
        while (start + length < value.size() &&
               std::isalnum(static_cast<unsigned char>(value[start + length]))) {
            ++length;
        }
        if (length >= minimum_suffix_length) {
            return true;
        }
        position = value.find(prefix, position + 1);
    }
    return false;
}

bool line_contains_sensitive_material(const std::string& line) {
    const auto value = lowercase(line);
    static const char* patterns[] = {
        "api key", "api.key", "api_key", "api-key", "apikey",
        "access key", "access_token", "access-token",
        "authorization:", "auth_token", "auth-token", "bearer ",
        "client_secret", "client-secret", "clientsecret", "credential",
        "database_url", "database-url", "github_pat_", "gho_", "ghp_",
        "ghr_", "ghs_", "ghu_", "password",
        "passwd", "private_key", "private-key", "private key", "pwd=",
        "refresh_token", "refresh-token", "secret", "security_token",
        "security-token", "sk-proj-", "token=", "token:", "token\"",
        "xoxb-", "xoxp-"
    };
    for (const auto* pattern : patterns) {
        if (value.find(pattern) != std::string::npos) {
            return true;
        }
    }

    // Common AWS access key identifier. Values are never returned once this
    // marker is present, even if the search term itself is unrelated.
    return contains_prefixed_token(value, "akia", 16) ||
           contains_prefixed_token(value, "asia", 16) ||
           contains_prefixed_token(value, "aiza", 20) ||
           contains_prefixed_token(value, "sk-", 20);
}

bool starts_pem_block(const std::string& line) {
    const auto value = lowercase(line);
    return value.find("-----begin ") != std::string::npos &&
           (value.find("private key-----") != std::string::npos ||
            value.find("certificate-----") != std::string::npos);
}

struct BoundedLine {
    std::string text;
    bool had_newline = false;
    bool too_long = false;
    bool binary = false;
};

bool read_bounded_line(std::istream& input, BoundedLine& line) {
    line = BoundedLine{};
    bool saw_input = false;
    char ch = '\0';
    while (input.get(ch)) {
        saw_input = true;
        if (ch == '\0') {
            line.binary = true;
        }
        if (ch == '\n') {
            line.had_newline = true;
            break;
        }
        if (line.text.size() < kMaxLineBytes) {
            line.text.push_back(ch);
        } else {
            line.too_long = true;
        }
    }

    if (!line.text.empty() && line.text.back() == '\r') {
        line.text.pop_back();
    }
    return saw_input;
}

std::size_t checked_file_size(const fs::path& path) {
    std::error_code error;
    const auto size = fs::file_size(path, error);
    if (error) {
        throw std::runtime_error("failed to inspect file size: " + path.generic_string());
    }
    return static_cast<std::size_t>(size);
}

std::string bounded_match_text(const std::string& line, std::size_t match_position) {
    if (line.size() <= kMaxMatchTextBytes) {
        return line;
    }

    const std::size_t half = kMaxMatchTextBytes / 2;
    const std::size_t start = match_position > half ? match_position - half : 0;
    const std::size_t count = std::min(kMaxMatchTextBytes, line.size() - start);
    return (start == 0 ? "" : "...") + line.substr(start, count) +
           (start + count == line.size() ? "" : "...");
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
        if (current.size() >= 4 && stop_words.find(current) == stop_words.end() &&
            !is_sensitive_query(current) && seen.insert(current).second) {
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
    const auto effective_max = std::min(max_entries, kMaxListedFiles);
    if (effective_max == 0) {
        return files;
    }

    fs::recursive_directory_iterator it(project_root_, fs::directory_options::skip_permission_denied);
    const fs::recursive_directory_iterator end;
    for (; it != end; ++it) {
        std::error_code error;
        const auto relative = fs::relative(it->path(), project_root_, error);
        if (error) {
            continue;
        }

        const auto status = it->symlink_status(error);
        if (error) {
            continue;
        }
        if (fs::is_symlink(status)) {
            if (it->is_directory(error) && !error) {
                it.disable_recursion_pending();
            }
            continue;
        }

        const bool blocked = has_ignored_component(relative) || has_sensitive_component(relative);
        if (fs::is_directory(status)) {
            if (blocked) {
                it.disable_recursion_pending();
            }
            continue;
        }
        if (!fs::is_regular_file(status) || blocked || !is_allowed_text_path(relative)) {
            continue;
        }

        files.push_back(relative.generic_string());
    }

    std::sort(files.begin(), files.end());
    if (files.size() > effective_max) {
        files.resize(effective_max);
    }
    return files;
}

std::string ProjectInspector::read_file(const std::string& relative_path, std::size_t max_bytes) const {
    return read_file_content(resolve_safe_path(relative_path),
                             std::min(max_bytes, kMaxReadOutputBytes));
}

std::vector<SearchMatch> ProjectInspector::search_text(const std::string& needle,
                                                       std::size_t max_matches,
                                                       std::size_t max_file_bytes) const {
    std::vector<SearchMatch> matches;
    if (needle.empty() || max_matches == 0) {
        return matches;
    }
    if (needle.size() > kMaxQueryBytes) {
        throw std::runtime_error("search query exceeds the safe length limit");
    }
    if (is_sensitive_query(needle)) {
        throw std::runtime_error("searching for credential or secret material is not allowed");
    }

    const auto effective_max_matches = std::min(max_matches, kMaxSearchMatches);
    const auto effective_file_bytes =
        std::min({max_file_bytes, kMaxSearchFileBytes, kMaxInspectableFileBytes});
    if (effective_file_bytes == 0) {
        return matches;
    }

    std::size_t total_input_bytes = 0;
    std::size_t total_output_bytes = 0;
    const auto files = list_files(kMaxListedFiles);
    for (const auto& file : files) {
        const auto absolute_path = resolve_safe_path(file);
        const auto file_size = checked_file_size(absolute_path);
        if (file_size > effective_file_bytes ||
            file_size > kMaxSearchTotalInputBytes - total_input_bytes) {
            continue;
        }
        total_input_bytes += file_size;

        std::ifstream input(absolute_path, std::ios::binary);
        if (!input.is_open()) {
            continue;
        }

        BoundedLine line;
        std::size_t line_number = 0;
        std::size_t file_output_bytes = 0;
        std::vector<SearchMatch> file_matches;
        bool unsafe_file = false;
        while (read_bounded_line(input, line)) {
            ++line_number;
            if (line.binary || line.too_long || starts_pem_block(line.text) ||
                line_contains_sensitive_material(line.text)) {
                unsafe_file = true;
                break;
            }

            const auto position = line.text.find(needle);
            if (position == std::string::npos) {
                continue;
            }

            const auto safe_text = bounded_match_text(line.text, position);
            const auto result_bytes = file.size() + safe_text.size() + 32;
            if (result_bytes > kMaxSearchOutputBytes - file_output_bytes ||
                file_matches.size() >= effective_max_matches - matches.size()) {
                continue;
            }
            file_output_bytes += result_bytes;
            file_matches.push_back(SearchMatch{file, line_number, safe_text});
        }

        // A key label may be followed by its value on another line. To avoid
        // leaking that value through an unrelated search, reject the entire
        // file if any sensitive marker, PEM block, binary byte, or overlong
        // line is encountered.
        if (unsafe_file) {
            continue;
        }
        if (file_output_bytes > kMaxSearchOutputBytes - total_output_bytes) {
            return matches;
        }
        total_output_bytes += file_output_bytes;
        matches.insert(matches.end(), file_matches.begin(), file_matches.end());
        if (matches.size() >= effective_max_matches) {
            return matches;
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

    if (is_sensitive_query(query)) {
        summary << "\nCredential and secret searches are intentionally unavailable.\n";
        return summary.str();
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
    for (const auto& component : requested) {
        if (component == "..") {
            throw std::runtime_error("parent path components are not allowed: " + relative_path);
        }
    }

    const auto normalized = requested.lexically_normal();
    if (normalized.empty() || normalized == ".") {
        throw std::runtime_error("a regular file path is required");
    }
    if (has_ignored_component(normalized) || has_sensitive_component(normalized) ||
        !is_allowed_text_path(normalized)) {
        throw std::runtime_error("path is not available to Code Agent: " + relative_path);
    }

    // Reject every symlink component, even when its resolved target remains in
    // the repository. This also prevents a symlink from escaping the root.
    fs::path current = project_root_;
    for (const auto& component : normalized) {
        if (component == ".") {
            continue;
        }
        current /= component;
        std::error_code error;
        const auto status = fs::symlink_status(current, error);
        if (error) {
            throw std::runtime_error("path is not available to Code Agent: " + relative_path);
        }
        if (fs::is_symlink(status)) {
            throw std::runtime_error("symbolic links are not available to Code Agent: " + relative_path);
        }
    }

    std::error_code error;
    const auto absolute_path = fs::canonical(current, error);
    if (error || !path_is_within(absolute_path, project_root_)) {
        throw std::runtime_error("path escapes project root: " + relative_path);
    }
    const auto status = fs::symlink_status(absolute_path, error);
    if (error || !fs::is_regular_file(status)) {
        throw std::runtime_error("path is not a regular file: " + relative_path);
    }

    return absolute_path;
}

std::string ProjectInspector::read_file_content(const fs::path& absolute_path,
                                                std::size_t max_bytes) const {
    const auto file_size = checked_file_size(absolute_path);
    if (file_size > kMaxInspectableFileBytes) {
        throw std::runtime_error("file exceeds the safe inspection size limit: " +
                                 absolute_path.filename().generic_string());
    }
    if (max_bytes == 0) {
        return {};
    }

    std::ifstream input(absolute_path, std::ios::binary);
    if (!input.is_open()) {
        throw std::runtime_error("failed to open file: " + absolute_path.generic_string());
    }

    std::string output;
    output.reserve(std::min(file_size, max_bytes));
    BoundedLine line;
    while (read_bounded_line(input, line)) {
        if (line.binary) {
            throw std::runtime_error("binary files are not available to Code Agent");
        }
        if (starts_pem_block(line.text)) {
            throw std::runtime_error("file contains key or certificate material");
        }
        if (line.too_long) {
            throw std::runtime_error("file contains a line exceeding the safe length limit");
        }
        if (line_contains_sensitive_material(line.text)) {
            // Reject the whole file. Redacting only the key line is insufficient
            // for JSON/YAML where its value may appear on the following line.
            throw std::runtime_error("file contains credential or secret material");
        }

        std::string safe_line = line.text;
        if (line.had_newline) {
            safe_line.push_back('\n');
        }
        if (safe_line.size() > max_bytes - output.size()) {
            static const std::string marker = "... (truncated)\n";
            if (marker.size() <= max_bytes - output.size()) {
                output += marker;
            }
            break;
        }
        output += safe_line;
    }

    return output;
}

}  // namespace code_agent
