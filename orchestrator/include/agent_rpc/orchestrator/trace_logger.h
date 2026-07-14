/**
 * @file trace_logger.h
 * @brief TraceLogger - unified structured logging with request context
 *
 * All log lines include request_id, context_id, and agent_id for
 * end-to-end request tracing across distributed agents.
 */

#pragma once

#include "request_context.h"
#include <string>
#include <iostream>
#include <mutex>
#include <fstream>
#include <chrono>
#include <iomanip>
#include <algorithm>

namespace agent_rpc {
namespace orchestrator {

/**
 * @brief Log level enumeration
 */
enum class LogLevel {
    DEBUG,
    INFO,
    WARN,
    ERROR
};

/**
 * @brief Unified trace logger for request pipeline
 *
 * Every log line includes:
 * - timestamp
 * - log level
 * - request_id (for distributed tracing)
 * - context_id (for session tracing)
 * - agent_id (for agent identification)
 * - message
 */
class TraceLogger {
public:
    /**
     * @brief Construct with agent identifier
     * @param agent_id This agent's identifier (e.g., "orch-1", "math-1")
     * @param log_file Optional log file path (empty = stdout only)
     */
    explicit TraceLogger(const std::string& agent_id, const std::string& log_file = "")
        : agent_id_(agent_id) {
        if (!log_file.empty()) {
            file_stream_.open(log_file, std::ios::app);
        }
    }

    ~TraceLogger() {
        if (file_stream_.is_open()) {
            file_stream_.close();
        }
    }

    // Disable copy
    TraceLogger(const TraceLogger&) = delete;
    TraceLogger& operator=(const TraceLogger&) = delete;

    /**
     * @brief Log a request received event
     */
    void log_request(const RequestContext& ctx, const std::string& message) {
        // User content may contain credentials or unpublished research data.
        // Request logs retain only size and trace identifiers by default.
        log(LogLevel::INFO, ctx, "REQ",
            "received user_text_bytes=" + std::to_string(message.size()));
    }

    /**
     * @brief Log a routing decision
     */
    void log_routing(const RequestContext& ctx, const std::string& target_agent) {
        log(LogLevel::INFO, ctx, "ROUTE", "→ " + target_agent);
    }

    /**
     * @brief Log a tool call attempt
     */
    void log_tool_call(const RequestContext& ctx, const std::string& tool_name,
                       bool success, const std::string& detail = "") {
        std::string status = success ? "OK" : "FAIL";
        std::string msg = "tool=" + tool_name + " status=" + status;
        if (!detail.empty()) {
            msg += " detail=" + detail;
        }
        log(success ? LogLevel::INFO : LogLevel::WARN, ctx, "TOOL", msg);
    }

    /**
     * @brief Log a response sent
     */
    void log_response(const RequestContext& ctx, int64_t duration_ms) {
        log(LogLevel::INFO, ctx, "RESP",
            "completed in " + std::to_string(duration_ms) + "ms");
    }

    /**
     * @brief Log an error
     */
    void log_error(const RequestContext& ctx, const std::string& error) {
        log(LogLevel::ERROR, ctx, "ERROR", error);
    }

    /**
     * @brief Log a generic info message
     */
    void log_info(const RequestContext& ctx, const std::string& tag,
                  const std::string& message) {
        log(LogLevel::INFO, ctx, tag, message);
    }

    /**
     * @brief Log without request context (for startup, etc.)
     */
    void log_system(LogLevel level, const std::string& message) {
        std::lock_guard<std::mutex> lock(mutex_);
        std::string line = format_line(level, "SYSTEM", "-", "-", message);
        output(line);
    }

private:
    std::string format_line(LogLevel level, const std::string& tag,
                           const std::string& request_id,
                           const std::string& context_id,
                           const std::string& message) {
        auto now = std::chrono::system_clock::now();
        auto time_t = std::chrono::system_clock::to_time_t(now);
        auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
            now.time_since_epoch()) % 1000;

        std::ostringstream oss;
        oss << "[" << std::put_time(std::localtime(&time_t), "%Y-%m-%d %H:%M:%S")
            << "." << std::setfill('0') << std::setw(3) << ms.count() << "]"
            << "[" << level_str(level) << "]"
            << "[" << agent_id_ << "]"
            << "[req:" << request_id << "]"
            << "[ctx:" << context_id << "]"
            << "[" << tag << "] "
            << sanitize_log_text(message);
        return oss.str();
    }

    void log(LogLevel level, const RequestContext& ctx,
             const std::string& tag, const std::string& message) {
        std::lock_guard<std::mutex> lock(mutex_);
        std::string line = format_line(level, tag, ctx.request_id,
                                       ctx.context_id, message);
        output(line);
    }

    void output(const std::string& line) {
        // Write to stdout
        std::cout << line << std::endl;

        // Write to file if configured
        if (file_stream_.is_open()) {
            file_stream_ << line << std::endl;
            file_stream_.flush();
        }
    }

    static const char* level_str(LogLevel level) {
        switch (level) {
            case LogLevel::DEBUG: return "DEBUG";
            case LogLevel::INFO:  return "INFO ";
            case LogLevel::WARN:  return "WARN ";
            case LogLevel::ERROR: return "ERROR";
            default:              return "?????";
        }
    }

    static std::string sanitize_log_text(const std::string& value) {
        std::string sanitized;
        sanitized.reserve(std::min<std::size_t>(value.size(), 1024));
        for (const unsigned char c : value) {
            if (sanitized.size() >= 1024) {
                sanitized += "...[truncated]";
                break;
            }
            if (c == '\n') sanitized += "\\n";
            else if (c == '\r') sanitized += "\\r";
            else if (c == '\t') sanitized += "\\t";
            else if (c < 0x20 || c == 0x7f) sanitized += '?';
            else sanitized += static_cast<char>(c);
        }
        return sanitized;
    }

    std::string agent_id_;
    std::ofstream file_stream_;
    std::mutex mutex_;
};

} // namespace orchestrator
} // namespace agent_rpc
