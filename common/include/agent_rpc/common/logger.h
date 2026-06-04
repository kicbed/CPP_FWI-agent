#pragma once

#include "types.h"

#include <atomic>
#include <condition_variable>
#include <fstream>
#include <map>
#include <memory>
#include <mutex>
#include <queue>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

namespace agent_rpc {
namespace common {

enum class LogLevel {
    Level_TRACE = 0,
    Level_DEBUG = 1,
    Level_INFO = 2,
    Level_WARN = 3,
    Level_ERROR = 4,
    Level_FATAL = 5
};

struct LogConfig {
    LogLevel level = LogLevel::Level_INFO;
    std::string log_file = "";
    bool console_output = true;
    bool file_output = false;
    bool async_logging = true;
    size_t max_file_size = 100 * 1024 * 1024;
    int max_files = 10;
    std::string log_format = "[%Y-%m-%d %H:%M:%S.%f] [%l] [%t] [%s:%n] %v";
    bool color_output = true;
};

struct LogEntry {
    LogLevel level;
    std::string message;
    std::string source_file;
    int line_number = 0;
    std::string function_name;
    std::thread::id thread_id;
    std::chrono::system_clock::time_point timestamp;
    std::map<std::string, std::string> fields;
};

class Logger {
public:
    virtual ~Logger() = default;

    virtual void trace(const std::string& message,
                       const std::string& source_file = "",
                       int line_number = 0,
                       const std::string& function_name = "") = 0;
    virtual void debug(const std::string& message,
                       const std::string& source_file = "",
                       int line_number = 0,
                       const std::string& function_name = "") = 0;
    virtual void info(const std::string& message,
                      const std::string& source_file = "",
                      int line_number = 0,
                      const std::string& function_name = "") = 0;
    virtual void warn(const std::string& message,
                      const std::string& source_file = "",
                      int line_number = 0,
                      const std::string& function_name = "") = 0;
    virtual void error(const std::string& message,
                       const std::string& source_file = "",
                       int line_number = 0,
                       const std::string& function_name = "") = 0;
    virtual void fatal(const std::string& message,
                       const std::string& source_file = "",
                       int line_number = 0,
                       const std::string& function_name = "") = 0;
    virtual void flush() = 0;
    virtual void setLogLevel(LogLevel level) = 0;
    virtual LogLevel getLogLevel() const = 0;
};

class LogFormatter {
public:
    explicit LogFormatter(bool use_color = true) : use_color_(use_color) {}

    std::string format(const LogEntry& entry) const;
    std::string getLevelString(LogLevel level) const;
    std::string getLevelColor(LogLevel level) const;

private:
    bool use_color_ = true;
};

class LogAppender {
public:
    virtual ~LogAppender() = default;
    virtual void append(const LogEntry& entry) = 0;
    virtual void flush() = 0;
};

class ConsoleAppender : public LogAppender {
public:
    explicit ConsoleAppender(bool use_color = true);

    void append(const LogEntry& entry) override;
    void flush() override;

private:
    bool use_color_;
    LogFormatter formatter_;
    std::mutex output_mutex_;
};

class FileAppender : public LogAppender {
public:
    FileAppender(const std::string& file_path,
                 size_t max_size = 100 * 1024 * 1024,
                 int max_files = 10);
    ~FileAppender();

    void append(const LogEntry& entry) override;
    void flush() override;

private:
    void rotateFiles();

    std::string file_path_;
    size_t max_size_;
    int max_files_;
    std::ofstream file_;
    size_t current_size_ = 0;
    mutable std::mutex file_mutex_;
    LogFormatter formatter_{false};
};

class AsyncLogger final : public Logger {
public:
    explicit AsyncLogger(const LogConfig& config);
    ~AsyncLogger();

    void trace(const std::string& message,
               const std::string& source_file = "",
               int line_number = 0,
               const std::string& function_name = "") override;
    void debug(const std::string& message,
               const std::string& source_file = "",
               int line_number = 0,
               const std::string& function_name = "") override;
    void info(const std::string& message,
              const std::string& source_file = "",
              int line_number = 0,
              const std::string& function_name = "") override;
    void warn(const std::string& message,
              const std::string& source_file = "",
              int line_number = 0,
              const std::string& function_name = "") override;
    void error(const std::string& message,
               const std::string& source_file = "",
               int line_number = 0,
               const std::string& function_name = "") override;
    void fatal(const std::string& message,
               const std::string& source_file = "",
               int line_number = 0,
               const std::string& function_name = "") override;

    void flush() override;
    void setLogLevel(LogLevel level) override;
    LogLevel getLogLevel() const override { return config_.level; }

private:
    void logImpl(LogLevel level,
                 const std::string& message,
                 const std::string& source_file,
                 int line_number,
                 const std::string& function_name);
    void processingLoop();
    void writeEntry(const LogEntry& entry);

    LogConfig config_;
    std::vector<std::unique_ptr<LogAppender>> appenders_;
    std::queue<LogEntry> log_queue_;
    std::mutex queue_mutex_;
    std::condition_variable queue_cv_;
    std::thread processing_thread_;
    std::atomic<bool> running_{true};
};

void initializeAdvancedLogger(const LogConfig& config);
void setLogLevel(LogLevel level);
void setLogFile(const std::string& filename);
void logTrace(const std::string& message);
void logDebug(const std::string& message);
void logInfo(const std::string& message);
void logWarn(const std::string& message);
void logError(const std::string& message);
void logFatal(const std::string& message);
void flushLogger();

#define LOG_TRACE(msg) agent_rpc::common::logTrace(msg)
#define LOG_DEBUG(msg) agent_rpc::common::logDebug(msg)
#define LOG_INFO(msg) agent_rpc::common::logInfo(msg)
#define LOG_WARN(msg) agent_rpc::common::logWarn(msg)
#define LOG_ERROR(msg) agent_rpc::common::logError(msg)
#define LOG_FATAL(msg) agent_rpc::common::logFatal(msg)

}  // namespace common
}  // namespace agent_rpc
