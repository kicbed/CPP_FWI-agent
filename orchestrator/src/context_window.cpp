#include "agent_rpc/orchestrator/context_window.h"

#include <nlohmann/json.hpp>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cctype>
#include <iomanip>
#include <random>
#include <sstream>
#include <utility>

namespace agent_rpc {
namespace orchestrator {
namespace {

using json = nlohmann::json;

struct HistoryItem {
    std::string role;
    std::string content;
    bool content_truncated = false;
};

struct RawHistoryItem {
    std::string role;
    std::string content;
};

bool is_utf8_continuation(unsigned char value) {
    return (value & 0xC0U) == 0x80U;
}

std::size_t utf8_prefix_boundary(const std::string& text, std::size_t limit) {
    std::size_t end = std::min(limit, text.size());
    while (end > 0 && end < text.size() &&
           is_utf8_continuation(static_cast<unsigned char>(text[end]))) {
        --end;
    }
    return end;
}

std::size_t utf8_suffix_boundary(const std::string& text, std::size_t start) {
    start = std::min(start, text.size());
    while (start < text.size() &&
           is_utf8_continuation(static_cast<unsigned char>(text[start]))) {
        ++start;
    }
    return start;
}

std::string truncate_message(const std::string& text, std::size_t max_chars,
                             bool* truncated) {
    if (text.size() <= max_chars) return text;
    *truncated = true;
    if (max_chars == 0) return "";

    static const std::string marker = "\n...[message truncated]...\n";
    if (max_chars <= marker.size() + 8) {
        return text.substr(0, utf8_prefix_boundary(text, max_chars));
    }

    const std::size_t payload = max_chars - marker.size();
    const std::size_t prefix_size = payload * 2 / 3;
    const std::size_t suffix_size = payload - prefix_size;
    const std::size_t prefix_end = utf8_prefix_boundary(text, prefix_size);
    const std::size_t suffix_start = utf8_suffix_boundary(
        text, text.size() > suffix_size ? text.size() - suffix_size : 0);
    return text.substr(0, prefix_end) + marker + text.substr(suffix_start);
}

std::string message_text(const a2a::AgentMessage& message) {
    std::string text;
    for (const auto& part : message.parts()) {
        if (!part || part->kind() != a2a::PartKind::Text) continue;
        const auto* text_part = dynamic_cast<const a2a::TextPart*>(part.get());
        if (!text_part) continue;
        if (!text.empty()) text += "\n";
        text += text_part->text();
    }
    return text;
}

json render_items(const std::vector<HistoryItem>& items) {
    json rendered = json::array();
    for (const auto& item : items) {
        rendered.push_back({{"role", item.role}, {"content", item.content}});
    }
    return rendered;
}

std::vector<HistoryItem> render_turn(const RawHistoryItem& user,
                                     const RawHistoryItem& assistant,
                                     std::size_t per_message_limit) {
    std::vector<HistoryItem> turn(2);
    turn[0].role = "user";
    turn[0].content = truncate_message(user.content, per_message_limit,
                                       &turn[0].content_truncated);
    turn[1].role = "assistant";
    turn[1].content = truncate_message(assistant.content, per_message_limit,
                                       &turn[1].content_truncated);
    return turn;
}

bool prepend_turn_within_budget(const RawHistoryItem& user,
                                const RawHistoryItem& assistant,
                                const ContextWindowConfig& config,
                                std::vector<HistoryItem>* selected,
                                bool allow_content_shrink,
                                bool* content_truncated) {
    auto fits = [&](std::size_t per_message_limit,
                    std::vector<HistoryItem>* candidate_out) {
        auto turn = render_turn(user, assistant, per_message_limit);
        std::vector<HistoryItem> candidate;
        candidate.reserve(turn.size() + selected->size());
        candidate.insert(candidate.end(), turn.begin(), turn.end());
        candidate.insert(candidate.end(), selected->begin(), selected->end());
        if (render_items(candidate).dump().size() > config.max_chars) return false;
        if (candidate_out) *candidate_out = std::move(candidate);
        return true;
    };

    std::vector<HistoryItem> candidate;
    if (fits(config.max_message_chars, &candidate)) {
        *content_truncated = candidate[0].content_truncated ||
                             candidate[1].content_truncated;
        *selected = std::move(candidate);
        return true;
    }
    if (!allow_content_shrink) return false;

    // JSON escaping can expand quotes, backslashes and controls by up to six
    // bytes. Find a smaller UTF-8-safe per-message cap for the newest turn so
    // max_chars remains a real serialized-byte ceiling.
    std::size_t low = 0;
    std::size_t high = config.max_message_chars;
    std::optional<std::vector<HistoryItem>> best;
    while (low <= high) {
        const std::size_t middle = low + (high - low) / 2;
        std::vector<HistoryItem> trial;
        if (fits(middle, &trial)) {
            best = std::move(trial);
            if (middle == config.max_message_chars) break;
            low = middle + 1;
        } else {
            if (middle == 0) break;
            high = middle - 1;
        }
    }
    if (!best.has_value()) return false;
    *content_truncated = true;
    *selected = std::move(*best);
    return true;
}

}  // namespace

bool is_valid_context_id(const std::string& context_id) {
    if (context_id.empty() || context_id.size() > 128) return false;
    const auto first = static_cast<unsigned char>(context_id.front());
    if (!std::isalnum(first)) return false;
    return std::all_of(context_id.begin(), context_id.end(), [](unsigned char c) {
        return std::isalnum(c) || c == '-' || c == '_';
    });
}

std::string generate_context_id() {
    static std::atomic<std::uint64_t> counter{0};
    static const std::uint64_t process_nonce = [] {
        std::random_device random;
        return (static_cast<std::uint64_t>(random()) << 32U) ^ random();
    }();
    const auto now = std::chrono::system_clock::now().time_since_epoch().count();
    const auto sequence = counter.fetch_add(1, std::memory_order_relaxed) + 1;

    std::ostringstream id;
    id << "ctx-" << std::hex << static_cast<std::uint64_t>(now) << '-'
       << process_nonce << '-' << sequence;
    return id.str();
}

std::optional<std::string> resolve_context_id(
    const std::optional<std::string>& requested_id) {
    if (!requested_id.has_value() || requested_id->empty()) {
        return generate_context_id();
    }
    if (!is_valid_context_id(*requested_id)) return std::nullopt;
    return *requested_id;
}

ContextWindowResult build_context_window(
    const std::vector<a2a::AgentMessage>& history,
    const ContextWindowConfig& config,
    bool exclude_trailing_user) {
    ContextWindowResult result;
    if (config.max_messages == 0 || config.max_chars == 0 ||
        config.max_message_chars == 0 || history.empty()) {
        result.omitted_messages = history.size();
        result.truncated = !history.empty();
        return result;
    }

    std::size_t usable_end = history.size();
    if (exclude_trailing_user && usable_end > 0 &&
        history[usable_end - 1].role() == a2a::MessageRole::User) {
        --usable_end;
    }

    std::vector<RawHistoryItem> eligible;
    eligible.reserve(usable_end);
    for (std::size_t index = 0; index < usable_end; ++index) {
        const auto& message = history[index];
        if (message.role() == a2a::MessageRole::System) continue;
        std::string text = message_text(message);
        if (text.empty()) continue;
        eligible.push_back({
            message.role() == a2a::MessageRole::User ? "user" : "assistant",
            std::move(text),
        });
    }

    // Form complete user/assistant turns first. Orphan messages from an
    // interrupted stream are deliberately not stitched to unrelated turns.
    std::vector<std::pair<RawHistoryItem, RawHistoryItem>> turns;
    std::optional<RawHistoryItem> pending_user;
    for (auto& item : eligible) {
        if (item.role == "user") {
            pending_user = std::move(item);
        } else if (pending_user.has_value()) {
            turns.emplace_back(std::move(*pending_user), std::move(item));
            pending_user.reset();
        }
    }

    std::vector<HistoryItem> selected;
    selected.reserve(std::min(config.max_messages, eligible.size()));
    for (auto turn = turns.rbegin(); turn != turns.rend(); ++turn) {
        if (selected.size() + 2 > config.max_messages) {
            result.truncated = true;
            break;
        }
        bool content_truncated = false;
        const bool is_newest_selected_turn = selected.empty();
        if (!prepend_turn_within_budget(
                turn->first, turn->second, config, &selected,
                is_newest_selected_turn, &content_truncated)) {
            result.truncated = true;
            break;
        }
        if (content_truncated) result.truncated = true;
    }

    const auto rendered = render_items(selected);
    result.history_json = rendered.dump();
    result.included_messages = selected.size();
    result.omitted_messages = eligible.size() >= result.included_messages
        ? eligible.size() - result.included_messages
        : 0;
    result.truncated = result.truncated || result.omitted_messages > 0;
    return result;
}

}  // namespace orchestrator
}  // namespace agent_rpc
