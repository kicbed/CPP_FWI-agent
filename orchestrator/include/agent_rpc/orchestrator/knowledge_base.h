/**
 * @file knowledge_base.h
 * @brief KnowledgeBase - improved knowledge base with better search
 */

#pragma once

#include <string>
#include <vector>
#include <fstream>
#include <sstream>
#include <algorithm>
#include <nlohmann/json.hpp>
#include <dirent.h>
#include <sys/stat.h>

using json = nlohmann::json;

namespace agent_rpc {
namespace orchestrator {

struct KnowledgeDocument {
    std::string path;
    std::string title;
    std::string content;
    std::string category;
    float relevance_score;
};

class KnowledgeBase {
public:
    KnowledgeBase() = default;

    bool load(const std::string& resource_dir) {
        resource_dir_ = resource_dir;
        load_directory(resource_dir + "/fwi_knowledge", "knowledge");
        load_directory(resource_dir + "/fwi_models", "models");
        load_directory(resource_dir + "/fwi_datasets", "datasets");
        load_directory(resource_dir + "/fwi_notes", "notes");
        return !documents_.empty();
    }

    /**
     * @brief Search knowledge base with improved matching
     */
    std::vector<KnowledgeDocument> search(const std::string& query, int topK = 3) {
        std::vector<KnowledgeDocument> results;
        std::string lower_query = to_lowercase(query);
        auto keywords = split_keywords(lower_query);

        for (auto& doc : documents_) {
            float score = compute_relevance(lower_query, keywords, doc);
            if (score > 0.0f) {
                KnowledgeDocument result = doc;
                result.relevance_score = score;
                results.push_back(result);
            }
        }

        std::sort(results.begin(), results.end(),
                  [](const KnowledgeDocument& a, const KnowledgeDocument& b) {
                      return a.relevance_score > b.relevance_score;
                  });

        if (results.size() > static_cast<size_t>(topK)) {
            results.resize(topK);
        }

        return results;
    }

    std::string read(const std::string& path) {
        std::string full_path = resource_dir_ + "/" + path;
        return read_file(full_path);
    }

    const std::vector<KnowledgeDocument>& get_all_documents() const {
        return documents_;
    }

    size_t get_document_count() const {
        return documents_.size();
    }

private:
    void load_directory(const std::string& dir_path, const std::string& category) {
        DIR* dir = opendir(dir_path.c_str());
        if (!dir) return;

        struct dirent* entry;
        while ((entry = readdir(dir)) != nullptr) {
            std::string filename = entry->d_name;
            if (filename == "." || filename == "..") continue;

            std::string full_path = dir_path + "/" + filename;
            struct stat st;
            if (stat(full_path.c_str(), &st) != 0 || S_ISDIR(st.st_mode)) continue;

            std::string content = read_file(full_path);
            if (content.empty()) continue;

            std::string title = extract_title(filename, content);

            KnowledgeDocument doc;
            doc.path = full_path;
            doc.title = title;
            doc.content = content;
            doc.category = category;
            doc.relevance_score = 0.0f;
            documents_.push_back(doc);
        }

        closedir(dir);
    }

    std::string read_file(const std::string& path) {
        std::ifstream file(path);
        if (!file.is_open()) return "";
        std::ostringstream oss;
        oss << file.rdbuf();
        return oss.str();
    }

    std::string extract_title(const std::string& filename, const std::string& content) {
        size_t pos = content.find("# ");
        if (pos != std::string::npos) {
            size_t end = content.find("\n", pos);
            if (end != std::string::npos) {
                return content.substr(pos + 2, end - pos - 2);
            }
        }
        std::string title = filename;
        size_t dot_pos = title.rfind(".");
        if (dot_pos != std::string::npos) {
            title = title.substr(0, dot_pos);
        }
        return title;
    }

    /**
     * @brief Improved relevance scoring
     */
    float compute_relevance(const std::string& lower_query,
                           const std::vector<std::string>& keywords,
                           const KnowledgeDocument& doc) {
        float score = 0.0f;

        std::string lower_title = to_lowercase(doc.title);
        std::string lower_content = to_lowercase(doc.content);

        // 1. Exact phrase match in title (highest weight)
        if (lower_title.find(lower_query) != std::string::npos) {
            score += 2.0f;
        }

        // 2. Keyword match in title
        for (const auto& kw : keywords) {
            if (kw.length() >= 2 && lower_title.find(kw) != std::string::npos) {
                score += 0.8f;
            }
        }

        // 3. Exact phrase match in content
        if (lower_content.find(lower_query) != std::string::npos) {
            score += 1.5f;
        }

        // 4. Keyword match in content (count occurrences)
        for (const auto& kw : keywords) {
            if (kw.length() >= 2) {
                size_t pos = 0;
                int count = 0;
                while ((pos = lower_content.find(kw, pos)) != std::string::npos) {
                    count++;
                    pos += kw.length();
                }
                score += std::min(1.0f, count * 0.2f);
            }
        }

        // 5. Category boost
        if (doc.category == "knowledge") {
            // Boost knowledge documents for theory questions
            if (lower_query.find("理论") != std::string::npos ||
                lower_query.find("概念") != std::string::npos ||
                lower_query.find("解释") != std::string::npos ||
                lower_query.find("什么是") != std::string::npos) {
                score += 0.5f;
            }
        }

        // 6. Acronym matching (AWI, FWI, etc.)
        std::string upper_query = to_uppercase(lower_query);
        if (upper_query.find("AWI") != std::string::npos ||
            upper_query.find("FWI") != std::string::npos) {
            std::string upper_content = to_uppercase(doc.content);
            if (upper_content.find("AWI") != std::string::npos ||
                upper_content.find("FWI") != std::string::npos) {
                score += 1.0f;
            }
        }

        return score;
    }

    static std::string to_lowercase(const std::string& str) {
        std::string result;
        result.reserve(str.size());
        for (char c : str) {
            result += static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        }
        return result;
    }

    static std::string to_uppercase(const std::string& str) {
        std::string result;
        result.reserve(str.size());
        for (char c : str) {
            result += static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
        }
        return result;
    }

    static std::vector<std::string> split_keywords(const std::string& text) {
        std::vector<std::string> keywords;

        // 分割中文和英文
        std::string current_word;
        for (size_t i = 0; i < text.length(); ++i) {
            char c = text[i];

            // 空格或标点符号作为分隔符
            if (std::isspace(c) || std::ispunct(c)) {
                if (!current_word.empty()) {
                    keywords.push_back(current_word);
                    current_word.clear();
                }
                continue;
            }

            // 中文字符（UTF-8 多字节）
            if (static_cast<unsigned char>(c) >= 0x80) {
                // 提取完整的 UTF-8 字符
                std::string utf8_char;
                utf8_char += c;
                int bytes = 1;
                if ((c & 0xE0) == 0xC0) bytes = 2;
                else if ((c & 0xF0) == 0xE0) bytes = 3;
                else if ((c & 0xF8) == 0xF0) bytes = 4;

                for (int j = 1; j < bytes && (i + j) < text.length(); ++j) {
                    utf8_char += text[i + j];
                }
                i += bytes - 1;

                // 中文字符作为单独的关键词
                if (utf8_char.length() >= 3) {  // 中文字符通常是3字节
                    keywords.push_back(utf8_char);
                }
                continue;
            }

            // 英文字符
            current_word += c;
        }

        if (!current_word.empty()) {
            keywords.push_back(current_word);
        }

        // 添加完整的查询作为关键词
        if (text.length() >= 2) {
            keywords.push_back(text);
        }

        return keywords;
    }

    std::string resource_dir_;
    std::vector<KnowledgeDocument> documents_;
};

} // namespace orchestrator
} // namespace agent_rpc
