/**
 * @file knowledge_base.h
 * @brief Small, deterministic and path-confined FWI knowledge base.
 */

#pragma once

#include <algorithm>
#include <array>
#include <cerrno>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <set>
#include <sstream>
#include <string>
#include <system_error>
#include <unordered_set>
#include <utility>
#include <vector>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

namespace agent_rpc {
namespace orchestrator {

struct KnowledgeDocument {
    // A path relative to the configured resource root. Absolute host paths are
    // deliberately not exposed to callers or prompts.
    std::string path;
    std::string title;
    std::string content;
    std::string category;
    float relevance_score = 0.0F;
};

/**
 * A deliberately small local retriever for the checked-in FWI material.
 *
 * It is independent of the Agent-RAG embedding service. Loading is confined
 * to four fixed child directories, rejects symlinks, accepts only text/JSON
 * files, and bounds both file size and total corpus size.
 */
class KnowledgeBase {
public:
    static constexpr std::size_t kMaxDocumentBytes = 256U * 1024U;
    static constexpr std::size_t kMaxCorpusBytes = 4U * 1024U * 1024U;
    static constexpr std::size_t kMaxDocuments = 256U;
    static constexpr std::size_t kMaxQueryBytes = 8192U;

    KnowledgeBase() = default;

    bool load(const std::string& resource_dir) {
        documents_.clear();
        resource_root_.clear();

        std::error_code ec;
        const std::filesystem::path requested(resource_dir);
        if (requested.empty() ||
            std::filesystem::is_symlink(
                std::filesystem::symlink_status(requested, ec))) {
            return false;
        }
        ec.clear();
        const auto canonical_root = std::filesystem::canonical(requested, ec);
        if (ec || !std::filesystem::is_directory(canonical_root, ec)) {
            return false;
        }

        resource_root_ = canonical_root;
        std::size_t corpus_bytes = 0;
        load_directory("fwi_knowledge", "knowledge", corpus_bytes);
        load_directory("fwi_models", "models", corpus_bytes);
        load_directory("fwi_datasets", "datasets", corpus_bytes);
        load_directory("fwi_notes", "notes", corpus_bytes);
        return !documents_.empty();
    }

    /**
     * Keyword/concept retrieval tuned for the small bilingual FWI corpus.
     * A query with no domain match returns no documents instead of injecting
     * unrelated context merely because it contains generic Chinese chars.
     */
    std::vector<KnowledgeDocument> search(const std::string& query,
                                          int top_k = 3) const {
        if (top_k <= 0 || documents_.empty() || query.empty()) return {};

        const std::string bounded_query = utf8_prefix(query, kMaxQueryBytes);
        const std::string normalized_query = ascii_lower(bounded_query);
        const auto concepts = matched_concepts(normalized_query);
        const auto keywords = query_keywords(normalized_query);
        if (concepts.empty() && keywords.empty()) return {};

        std::vector<KnowledgeDocument> results;
        results.reserve(documents_.size());
        float best_score = 0.0F;
        for (const auto& doc : documents_) {
            const float score = compute_relevance(
                normalized_query, concepts, keywords, doc);
            if (score < 1.0F) continue;
            KnowledgeDocument result = doc;
            result.relevance_score = score;
            best_score = std::max(best_score, score);
            results.push_back(std::move(result));
        }
        if (results.empty()) return {};

        // Discard documents that only contain a passing mention when another
        // document is a substantially stronger topic match.
        const float relative_floor = std::max(1.0F, best_score * 0.18F);
        results.erase(
            std::remove_if(results.begin(), results.end(),
                           [relative_floor](const KnowledgeDocument& doc) {
                               return doc.relevance_score < relative_floor;
                           }),
            results.end());
        std::sort(results.begin(), results.end(),
                  [](const KnowledgeDocument& lhs,
                     const KnowledgeDocument& rhs) {
                      if (std::fabs(lhs.relevance_score - rhs.relevance_score) >
                          0.0001F) {
                          return lhs.relevance_score > rhs.relevance_score;
                      }
                      return lhs.path < rhs.path;
                  });
        if (results.size() > static_cast<std::size_t>(top_k)) {
            results.resize(static_cast<std::size_t>(top_k));
        }
        return results;
    }

    /**
     * Read only an already-loaded document by its relative path. This method
     * never opens a caller-supplied filesystem path.
     */
    std::string read(const std::string& relative_path) const {
        const std::filesystem::path requested(relative_path);
        if (requested.empty() || requested.is_absolute()) return {};
        const auto normalized = requested.lexically_normal();
        for (const auto& component : normalized) {
            if (component == "..") return {};
        }
        const std::string key = normalized.generic_string();
        const auto found = std::find_if(
            documents_.begin(), documents_.end(),
            [&key](const KnowledgeDocument& doc) { return doc.path == key; });
        return found == documents_.end() ? std::string{} : found->content;
    }

    const std::vector<KnowledgeDocument>& get_all_documents() const {
        return documents_;
    }

    std::size_t get_document_count() const { return documents_.size(); }

private:
    struct Concept {
        std::vector<std::string> aliases;
    };

    struct WeightedKeyword {
        std::string text;
        float weight = 1.0F;
    };

    static const std::vector<Concept>& domain_concepts() {
        static const std::vector<Concept> concepts = {
            {{"cycle skipping", "cycle-skipping", "周波跳跃", "周期跳跃", "跳周"}},
            {{"adjoint state", "adjoint-state", "伴随状态", "伴随法"}},
            {{"adaptive waveform inversion", "自适应波形反演", "awi"}},
            {{"multiscale", "multi-scale", "多尺度反演", "多尺度", "频率递进"}},
            {{"full waveform inversion", "全波形反演", "fwi"}},
            {{"envelope inversion", "包络反演", "包络"}},
            {{"tikhonov", "total variation", "正则化", "总变分", "tv"}},
            {{"frechet", "fréchet", "梯度", "目标函数", "伴随源"}},
            {{"wave equation", "波动方程", "声波方程", "正演"}},
            {{"velocity model", "速度模型", "初始模型", "慢度"}},
            {{"source wavelet", "ricker", "震源子波", "雷克子波"}},
            {{"shot gather", "炮集", "检波器", "观测系统", "采集几何"}},
            {{"marmousi", "overthrust", "模型元数据", "数据集"}},
            {{"local minimum", "局部极小", "非线性"}},
            {{"low frequency", "低频", "频带", "频率"}},
            {{"residual", "残差", "走时差", "相位", "振幅"}},
        };
        return concepts;
    }

    static bool is_ascii_word_char(unsigned char value) {
        return std::isalnum(value) != 0 || value == '_';
    }

    static bool contains_term(const std::string& text,
                              const std::string& term) {
        if (term.empty()) return false;
        const bool ascii_term = std::all_of(
            term.begin(), term.end(), [](unsigned char value) {
                return value < 0x80U;
            });
        std::size_t position = 0;
        while ((position = text.find(term, position)) != std::string::npos) {
            if (!ascii_term) return true;
            const std::size_t end = position + term.size();
            const bool left_ok = position == 0 ||
                !is_ascii_word_char(static_cast<unsigned char>(text[position - 1]));
            const bool right_ok = end == text.size() ||
                !is_ascii_word_char(static_cast<unsigned char>(text[end]));
            if (left_ok && right_ok) return true;
            position = end;
        }
        return false;
    }

    static std::vector<std::size_t> matched_concepts(
        const std::string& query) {
        std::vector<std::size_t> matches;
        const auto& concepts = domain_concepts();
        for (std::size_t index = 0; index < concepts.size(); ++index) {
            if (std::any_of(concepts[index].aliases.begin(),
                            concepts[index].aliases.end(),
                            [&query](const std::string& alias) {
                                return contains_term(query, alias);
                            })) {
                matches.push_back(index);
            }
        }
        return matches;
    }

    static std::vector<WeightedKeyword> query_keywords(
        const std::string& query) {
        static const std::unordered_set<std::string> stop_words = {
            "a", "an", "and", "are", "can", "does", "explain", "for",
            "how", "in", "is", "it", "of", "please", "tell", "the",
            "to", "what", "why", "with", "about", "this", "that"
        };
        static const std::unordered_set<std::string> domain_words = {
            "adjoint", "acoustic", "awi", "cycle", "elastic", "envelope",
            "fd", "fem", "frechet", "fwi", "gradient", "inversion",
            "marmousi", "multiscale", "objective", "overthrust", "residual",
            "ricker", "seismic", "skipping", "source", "tikhonov", "velocity",
            "waveform", "wavelet"
        };

        std::set<std::string> unique;
        std::string token;
        for (unsigned char value : query) {
            if (value < 0x80U && (std::isalnum(value) != 0 || value == '_')) {
                token.push_back(static_cast<char>(value));
            } else if (!token.empty()) {
                if (token.size() >= 2U && stop_words.count(token) == 0U) {
                    unique.insert(token);
                }
                token.clear();
            }
        }
        if (!token.empty() && token.size() >= 2U &&
            stop_words.count(token) == 0U) {
            unique.insert(token);
        }

        std::vector<WeightedKeyword> result;
        for (const auto& word : unique) {
            if (domain_words.count(word) != 0U) {
                result.push_back({word, word == "fwi" ? 1.0F : 1.8F});
            }
        }

        // Curated Chinese phrases avoid the old single-character matching,
        // where generic characters such as “是/的/怎” matched almost every
        // document. Concepts are also useful as direct weighted keywords.
        static const std::array<const char*, 30> chinese_terms = {{
            "全波形反演", "周波跳跃", "周期跳跃", "伴随状态", "自适应波形反演",
            "多尺度", "频率递进", "包络反演", "正则化", "总变分",
            "梯度", "目标函数", "伴随源", "波动方程", "声波方程",
            "正演", "速度模型", "初始模型", "慢度", "震源子波",
            "雷克子波", "炮集", "检波器", "观测系统", "采集几何",
            "局部极小", "低频", "残差", "走时差", "相位"
        }};
        for (const char* term_value : chinese_terms) {
            const std::string term(term_value);
            if (query.find(term) != std::string::npos) {
                result.push_back({term, 2.0F});
            }
        }
        return result;
    }

    float compute_relevance(
        const std::string& query,
        const std::vector<std::size_t>& concepts,
        const std::vector<WeightedKeyword>& keywords,
        const KnowledgeDocument& doc) const {
        float score = 0.0F;
        const std::string title = ascii_lower(doc.title);
        const std::string content = ascii_lower(doc.content);

        for (const std::size_t concept_index : concepts) {
            const auto& concept = domain_concepts().at(concept_index);
            bool title_match = false;
            bool content_match = false;
            for (const auto& alias : concept.aliases) {
                title_match = title_match || contains_term(title, alias);
                content_match = content_match || contains_term(content, alias);
            }
            if (title_match) score += 7.0F;
            if (content_match) score += 1.5F;
        }

        for (const auto& keyword : keywords) {
            if (contains_term(title, keyword.text)) {
                score += 2.5F * keyword.weight;
            }
            if (contains_term(content, keyword.text)) {
                score += 0.75F * keyword.weight;
            }
        }

        const bool asks_for_definition =
            query.find("什么是") != std::string::npos ||
            query.find("是什么") != std::string::npos ||
            contains_term(query, "definition") ||
            contains_term(query, "define") ||
            contains_term(query, "what is");
        const bool asks_about_fwi =
            contains_term(query, "fwi") ||
            query.find("全波形反演") != std::string::npos;
        if (asks_for_definition && asks_about_fwi &&
            doc.path == "fwi_knowledge/fwi_basics.md") {
            score += 10.0F;
        }
        if (score > 0.0F && doc.category == "knowledge") score += 0.25F;
        return score;
    }

    void load_directory(const std::filesystem::path& relative_directory,
                        const std::string& category,
                        std::size_t& corpus_bytes) {
        if (documents_.size() >= kMaxDocuments || resource_root_.empty()) return;
        const auto directory = resource_root_ / relative_directory;
        std::error_code ec;
        if (std::filesystem::is_symlink(
                std::filesystem::symlink_status(directory, ec)) || ec ||
            !std::filesystem::is_directory(directory, ec)) {
            return;
        }
        const auto canonical_directory = std::filesystem::canonical(directory, ec);
        if (ec || !is_within_root(canonical_directory)) return;

        std::vector<std::filesystem::path> entries;
        for (std::filesystem::directory_iterator iterator(
                 canonical_directory,
                 std::filesystem::directory_options::skip_permission_denied,
                 ec), end;
             !ec && iterator != end; iterator.increment(ec)) {
            entries.push_back(iterator->path());
        }
        std::sort(entries.begin(), entries.end());

        for (const auto& entry : entries) {
            if (documents_.size() >= kMaxDocuments ||
                corpus_bytes >= kMaxCorpusBytes) {
                break;
            }
            ec.clear();
            const auto status = std::filesystem::symlink_status(entry, ec);
            if (ec || std::filesystem::is_symlink(status) ||
                !std::filesystem::is_regular_file(status)) {
                continue;
            }
            std::string extension = ascii_lower(entry.extension().string());
            const bool json_only = category == "models" || category == "datasets";
            if ((json_only && extension != ".json") ||
                (!json_only && extension != ".md" && extension != ".txt" &&
                 extension != ".json")) {
                continue;
            }
            const auto canonical_entry = std::filesystem::canonical(entry, ec);
            if (ec || !is_within_root(canonical_entry)) continue;
            const auto file_size = std::filesystem::file_size(canonical_entry, ec);
            if (ec || file_size == 0U || file_size > kMaxDocumentBytes ||
                file_size > kMaxCorpusBytes - corpus_bytes) {
                continue;
            }
            const std::string content = read_bounded(canonical_entry, file_size);
            if (content.empty()) continue;

            const auto relative = std::filesystem::relative(
                canonical_entry, resource_root_, ec);
            if (ec || relative.empty() || relative.is_absolute()) continue;
            bool traverses = false;
            for (const auto& component : relative) {
                if (component == "..") traverses = true;
            }
            if (traverses) continue;

            documents_.push_back({relative.generic_string(),
                                  extract_title(entry.filename().string(), content),
                                  content, category, 0.0F});
            corpus_bytes += content.size();
        }
    }

    bool is_within_root(const std::filesystem::path& candidate) const {
        const auto relative = candidate.lexically_relative(resource_root_);
        if (relative.empty() && candidate != resource_root_) return false;
        if (relative.is_absolute()) return false;
        for (const auto& component : relative) {
            if (component == "..") return false;
        }
        return true;
    }

    static std::string read_bounded(const std::filesystem::path& path,
                                    std::uintmax_t expected_size) {
        if (expected_size > kMaxDocumentBytes) return {};
        const int descriptor = ::open(
            path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
        if (descriptor < 0) return {};

        struct stat status {};
        if (::fstat(descriptor, &status) != 0 || !S_ISREG(status.st_mode) ||
            status.st_size <= 0 ||
            static_cast<std::uintmax_t>(status.st_size) != expected_size ||
            static_cast<std::uintmax_t>(status.st_size) > kMaxDocumentBytes) {
            ::close(descriptor);
            return {};
        }

        std::string content(static_cast<std::size_t>(status.st_size), '\0');
        std::size_t offset = 0;
        while (offset < content.size()) {
            const ssize_t bytes = ::read(
                descriptor, content.data() + offset, content.size() - offset);
            if (bytes > 0) {
                offset += static_cast<std::size_t>(bytes);
                continue;
            }
            if (bytes < 0 && errno == EINTR) continue;
            ::close(descriptor);
            return {};
        }
        ::close(descriptor);
        return content;
    }

    static std::string extract_title(const std::string& filename,
                                     const std::string& content) {
        const std::size_t marker = content.find("# ");
        if (marker != std::string::npos &&
            (marker == 0U || content[marker - 1U] == '\n')) {
            const std::size_t end = content.find('\n', marker);
            const std::size_t length =
                (end == std::string::npos ? content.size() : end) - marker - 2U;
            if (length > 0U && length <= 512U) {
                return content.substr(marker + 2U, length);
            }
        }
        const std::filesystem::path path(filename);
        return path.stem().string();
    }

    static std::string ascii_lower(const std::string& value) {
        std::string result = value;
        for (char& character : result) {
            const auto byte = static_cast<unsigned char>(character);
            if (byte < 0x80U) {
                character = static_cast<char>(std::tolower(byte));
            }
        }
        return result;
    }

    static std::string utf8_prefix(const std::string& value,
                                   std::size_t max_bytes) {
        if (value.size() <= max_bytes) return value;
        std::size_t end = max_bytes;
        while (end > 0U && end < value.size() &&
               (static_cast<unsigned char>(value[end]) & 0xC0U) == 0x80U) {
            --end;
        }
        return value.substr(0U, end);
    }

    std::filesystem::path resource_root_;
    std::vector<KnowledgeDocument> documents_;
};

}  // namespace orchestrator
}  // namespace agent_rpc
