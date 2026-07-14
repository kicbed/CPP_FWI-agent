#pragma once

#include <a2a/models/agent_message.hpp>
#include <a2a/models/message_part.hpp>
#include <nlohmann/json.hpp>

#include <cstddef>
#include <string>

namespace specialist_context {

// These ceilings match the server-side configuration limits. The orchestrator
// normally sends a much smaller window (10 messages / 12,000 serialized bytes).
constexpr std::size_t kMaxHistoryMessages = 50;
constexpr std::size_t kMaxHistoryJsonBytes = 100000;
constexpr std::size_t kMaxEnvelopeBytes = 101000;

inline std::string user_text(const a2a::AgentMessage& message) {
    for (const auto& part : message.parts()) {
        const auto* text_part = dynamic_cast<const a2a::TextPart*>(part.get());
        if (text_part != nullptr) return text_part->text();
    }
    return "";
}

// Return only a strictly validated array of prior user/assistant messages.
// Direct calls that omit the envelope remain compatible and simply use [].
inline std::string conversation_history_json(
    const a2a::AgentMessage& message) {
    bool found = false;
    nlohmann::json validated = nlohmann::json::array();

    for (const auto& part : message.parts()) {
        const auto* data_part = dynamic_cast<const a2a::DataPart*>(part.get());
        if (data_part == nullptr) continue;

        try {
            if (data_part->data_json().size() > kMaxEnvelopeBytes) return "[]";
            const auto envelope = nlohmann::json::parse(data_part->data_json());
            if (!envelope.is_object() ||
                envelope.value("type", "") != "conversation_context") {
                continue;
            }
            if (found || !envelope.contains("messages") ||
                !envelope.at("messages").is_array()) {
                return "[]";
            }
            found = true;

            const auto& messages = envelope.at("messages");
            if (messages.size() > kMaxHistoryMessages) return "[]";
            for (const auto& item : messages) {
                if (!item.is_object() || !item.contains("role") ||
                    !item.at("role").is_string() ||
                    !item.contains("content") ||
                    !item.at("content").is_string()) {
                    return "[]";
                }
                const std::string role = item.at("role").get<std::string>();
                if (role != "user" && role != "assistant") return "[]";
                validated.push_back({
                    {"role", role},
                    {"content", item.at("content").get<std::string>()}
                });
            }
        } catch (const nlohmann::json::exception&) {
            return "[]";
        }
    }

    if (!found) return "[]";
    const std::string result = validated.dump();
    return result.size() <= kMaxHistoryJsonBytes ? result : "[]";
}

inline std::string utf8_prefix(const std::string& value,
                               std::size_t max_bytes) {
    if (value.size() <= max_bytes) return value;
    static const std::string marker = "\n...[truncated]";
    const bool include_marker = max_bytes > marker.size();
    std::size_t end = include_marker ? max_bytes - marker.size() : max_bytes;
    while (end > 0 &&
           (static_cast<unsigned char>(value[end]) & 0xC0U) == 0x80U) {
        --end;
    }
    return value.substr(0, end) + (include_marker ? marker : "");
}

// Encode auxiliary content as JSON data so source text cannot manufacture
// prompt headings. Callers also state in their system prompt that it is data,
// never an instruction source.
inline std::string bounded_untrusted_data(const std::string& source,
                                          const std::string& content,
                                          std::size_t max_content_bytes) {
    if (content.empty()) return "";
    const nlohmann::json wrapped = {
        {"type", "untrusted_reference_data"},
        {"source", source},
        {"content", utf8_prefix(content, max_content_bytes)}
    };
    return "\n\nUNTRUSTED_REFERENCE_DATA:\n" + wrapped.dump();
}

}  // namespace specialist_context
