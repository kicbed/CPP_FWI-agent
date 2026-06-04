/**
 * @file knowledge_base.h
 * @brief KnowledgeBase - local knowledge base for FWI research
 *
 * Provides keyword-based search over local Markdown/JSON knowledge files.
 * First version uses simple keyword matching, future versions can use embeddings.
 *
 * Directory structure:
 * resources/
 * ├── fwi_knowledge/        # FWI theory (Markdown)
 * ├── fwi_models/           # Model metadata (JSON)
 * ├── fwi_datasets/         # Dataset metadata (JSON)
 * └── fwi_notes/            # Research notes (Markdown)
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

/**
 * @brief Knowledge document
 */
struct KnowledgeDocument {
    std::string path;           // File path
    std::string title;          // Document title
    std::string content;        // Full content
    std::string category;       // Category (knowledge/models/datasets/notes)
    float relevance_score;      // Relevance score (for search results)
};

/**
 * @brief Local knowledge base
 *
 * Loads and searches local Markdown/JSON knowledge files.
 * Supports keyword-based search with relevance scoring.
 */
class KnowledgeBase {
public:
    KnowledgeBase() = default;

    /**
     * @brief Load knowledge base from directory
     * @param resource_dir Root resource directory
     * @return true if loaded successfully
     *
     * Expected structure:
     * resource_dir/
     * ├── fwi_knowledge/*.md
     * ├── fwi_models/*.json
     * ├── fwi_datasets/*.json
     * └── fwi_notes/*.md
     */
    bool load(const std::string& resource_dir) {
        resource_dir_ = resource_dir;

        // Load all categories
        load_directory(resource_dir + "/fwi_knowledge", "knowledge");
        load_directory(resource_dir + "/fwi_models", "models");
        load_directory(resource_dir + "/fwi_datasets", "datasets");
        load_directory(resource_dir + "/fwi_notes", "notes");

        return !documents_.empty();
    }

    /**
     * @brief Search knowledge base
     * @param query Search query
     * @param topK Maximum results to return
     * @return Relevant documents sorted by score
     */
    std::vector<KnowledgeDocument> search(const std::string& query, int topK = 3) {
        std::vector<KnowledgeDocument> results;

        // Normalize query
        std::string lower_query = to_lowercase(query);
        auto keywords = split_keywords(lower_query);

        // Score each document
        for (auto& doc : documents_) {
            float score = compute_relevance(lower_query, keywords, doc);
            if (score > 0.0f) {
                KnowledgeDocument result = doc;
                result.relevance_score = score;
                results.push_back(result);
            }
        }

        // Sort by score descending
        std::sort(results.begin(), results.end(),
                  [](const KnowledgeDocument& a, const KnowledgeDocument& b) {
                      return a.relevance_score > b.relevance_score;
                  });

        // Return topK
        if (results.size() > static_cast<size_t>(topK)) {
            results.resize(topK);
        }

        return results;
    }

    /**
     * @brief Read a specific file
     * @param path File path (relative to resource_dir)
     * @return File content
     */
    std::string read(const std::string& path) {
        std::string full_path = resource_dir_ + "/" + path;
        return read_file(full_path);
    }

    /**
     * @brief Get all documents
     */
    const std::vector<KnowledgeDocument>& get_all_documents() const {
        return documents_;
    }

    /**
     * @brief Get document count
     */
    size_t get_document_count() const {
        return documents_.size();
    }

private:
    /**
     * @brief Load all files from a directory
     */
    void load_directory(const std::string& dir_path, const std::string& category) {
        DIR* dir = opendir(dir_path.c_str());
        if (!dir) return;

        struct dirent* entry;
        while ((entry = readdir(dir)) != nullptr) {
            std::string filename = entry->d_name;

            // Skip . and ..
            if (filename == "." || filename == "..") continue;

            std::string full_path = dir_path + "/" + filename;

            // Check if it's a file
            struct stat st;
            if (stat(full_path.c_str(), &st) != 0 || S_ISDIR(st.st_mode)) continue;

            // Read file
            std::string content = read_file(full_path);
            if (content.empty()) continue;

            // Extract title from filename or first line
            std::string title = extract_title(filename, content);

            // Add document
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

    /**
     * @brief Read file content
     */
    std::string read_file(const std::string& path) {
        std::ifstream file(path);
        if (!file.is_open()) return "";

        std::ostringstream oss;
        oss << file.rdbuf();
        return oss.str();
    }

    /**
     * @brief Extract title from filename or content
     */
    std::string extract_title(const std::string& filename, const std::string& content) {
        // Try to extract from first line (Markdown heading)
        size_t pos = content.find("# ");
        if (pos != std::string::npos) {
            size_t end = content.find("\n", pos);
            if (end != std::string::npos) {
                return content.substr(pos + 2, end - pos - 2);
            }
        }

        // Use filename
        std::string title = filename;
        size_t dot_pos = title.rfind(".");
        if (dot_pos != std::string::npos) {
            title = title.substr(0, dot_pos);
        }
        return title;
    }

    /**
     * @brief Compute relevance score
     */
    float compute_relevance(const std::string& lower_query,
                           const std::vector<std::string>& keywords,
                           const KnowledgeDocument& doc) {
        float score = 0.0f;

        std::string lower_title = to_lowercase(doc.title);
        std::string lower_content = to_lowercase(doc.content);

        // Title match (high weight)
        for (const auto& kw : keywords) {
            if (lower_title.find(kw) != std::string::npos) {
                score += 0.5f;
            }
        }

        // Content keyword match
        for (const auto& kw : keywords) {
            size_t pos = 0;
            int count = 0;
            while ((pos = lower_content.find(kw, pos)) != std::string::npos) {
                count++;
                pos += kw.length();
            }
            score += std::min(0.3f, count * 0.05f);
        }

        // Category boost
        if (doc.category == "knowledge" && lower_query.find("理论") != std::string::npos) {
            score += 0.2f;
        }
        if (doc.category == "models" && lower_query.find("模型") != std::string::npos) {
            score += 0.2f;
        }
        if (doc.category == "datasets" && lower_query.find("数据") != std::string::npos) {
            score += 0.2f;
        }

        return score;
    }

    // Utility
    static std::string to_lowercase(const std::string& str) {
        std::string result;
        result.reserve(str.size());
        for (char c : str) {
            result += static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        }
        return result;
    }

    static std::vector<std::string> split_keywords(const std::string& text) {
        std::vector<std::string> keywords;
        std::istringstream iss(text);
        std::string word;
        while (iss >> word) {
            if (word.length() > 1) {
                keywords.push_back(word);
            }
        }
        return keywords;
    }

    std::string resource_dir_;
    std::vector<KnowledgeDocument> documents_;
};

} // namespace orchestrator
} // namespace agent_rpc
