#include "agent_rpc/common/logger.h"

#include <cstdio>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <thread>

namespace agent_rpc {
namespace common {

namespace {

std::mutex g_logger_mutex;
std::shared_ptr<Logger> g_logger;
LogConfig g_config;

std::string basename(const std::string& path) {
    try {
        return std::filesystem::path(path).filename().string();
    } catch (...) {
        return path;
    }
}

std::shared_ptr<Logger> getOrCreateLogger() {
    std::lock_guard<std::mutex> lock(g_logger_mutex);
    if (!g_logger) {
        g_logger = std::make_shared<AsyncLogger>(g_config);
    }
    return g_logger;
}

}  // namespace

std::string LogFormatter::format(const LogEntry& entry) const {
    auto time_t = std::chrono::system_clock::to_time_t(entry.timestamp);
    auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                  entry.timestamp.time_since_epoch()) %
              1000;

    std::ostringstream oss;
    oss << "["
        << std::put_time(std::localtime(&time_t), "%Y-%m-%d %H:%M:%S")
        << "." << std::setfill('0') << std::setw(3) << ms.count() << "] "
        << "[" << getLevelString(entry.level) << "] "
        << "[" << entry.thread_id << "]";

    if (!entry.source_file.empty()) {
        oss << " [" << basename(entry.source_file);
        if (entry.line_number > 0) {
            oss << ":" << entry.line_number;
        }
        if (!entry.function_name.empty()) {
            oss << " " << entry.function_name;
        }
        oss << "]";
    }

    oss << " " << entry.message;
    if (!entry.fields.empty()) {
        oss << " {";
        bool first = true;
        for (const auto& pair : entry.fields) {
            if (!first) {
                oss << ", ";
            }
            oss << pair.first << "=" << pair.second;
            first = false;
        }
        oss << "}";
    }

    auto formatted = oss.str();
    if (!use_color_) {
        return formatted;
    }

    const auto color = getLevelColor(entry.level);
    if (color.empty()) {
        return formatted;
    }
    return color + formatted + "\033[0m";
}

std::string LogFormatter::getLevelString(LogLevel level) const {
    switch (level) {
        case LogLevel::Level_TRACE:
            return "TRACE";
        case LogLevel::Level_DEBUG:
            return "DEBUG";
        case LogLevel::Level_INFO:
            return "INFO";
        case LogLevel::Level_WARN:
            return "WARN";
        case LogLevel::Level_ERROR:
            return "ERROR";
        case LogLevel::Level_FATAL:
            return "FATAL";
        default:
            return "UNKNOWN";
    }
}

std::string LogFormatter::getLevelColor(LogLevel level) const {
    switch (level) {
        case LogLevel::Level_TRACE:
            return "\033[90m";
        case LogLevel::Level_DEBUG:
            return "\033[36m";
        case LogLevel::Level_INFO:
            return "\033[32m";
        case LogLevel::Level_WARN:
            return "\033[33m";
        case LogLevel::Level_ERROR:
            return "\033[31m";
        case LogLevel::Level_FATAL:
            return "\033[35m";
        default:
            return "";
    }
}

ConsoleAppender::ConsoleAppender(bool use_color)
    : use_color_(use_color), formatter_(use_color) {}

void ConsoleAppender::append(const LogEntry& entry) {
    std::lock_guard<std::mutex> lock(output_mutex_);
    auto& stream = (entry.level >= LogLevel::Level_ERROR) ? std::cerr : std::cout;
    stream << formatter_.format(entry) << std::endl;
}

void ConsoleAppender::flush() {
    std::lock_guard<std::mutex> lock(output_mutex_);
    std::cout.flush();
    std::cerr.flush();
}

FileAppender::FileAppender(const std::string& file_path, size_t max_size, int max_files)
    : file_path_(file_path), max_size_(max_size), max_files_(max_files) {
    file_.open(file_path_, std::ios::app);
    if (file_.is_open()) {
        file_.seekp(0, std::ios::end);
        current_size_ = static_cast<size_t>(file_.tellp());
    }
}

FileAppender::~FileAppender() {
    flush();
    if (file_.is_open()) {
        file_.close();
    }
}

void FileAppender::append(const LogEntry& entry) {
    std::lock_guard<std::mutex> lock(file_mutex_);
    if (!file_.is_open()) {
        return;
    }

    const auto formatted = formatter_.format(entry);
    file_ << formatted << std::endl;
    current_size_ += formatted.size() + 1;

    if (current_size_ >= max_size_) {
        rotateFiles();
    }
}

void FileAppender::flush() {
    std::lock_guard<std::mutex> lock(file_mutex_);
    if (file_.is_open()) {
        file_.flush();
    }
}

void FileAppender::rotateFiles() {
    if (file_.is_open()) {
        file_.flush();
        file_.close();
    }

    for (int i = max_files_ - 1; i >= 1; --i) {
        const auto src = file_path_ + "." + std::to_string(i);
        const auto dst = file_path_ + "." + std::to_string(i + 1);
        std::remove(dst.c_str());
        std::rename(src.c_str(), dst.c_str());
    }

    const auto first_rotated = file_path_ + ".1";
    std::remove(first_rotated.c_str());
    std::rename(file_path_.c_str(), first_rotated.c_str());

    file_.open(file_path_, std::ios::trunc);
    current_size_ = 0;
}

AsyncLogger::AsyncLogger(const LogConfig& config) : config_(config) {
    if (config_.console_output) {
        appenders_.push_back(std::make_unique<ConsoleAppender>(config_.color_output));
    }
    if (config_.file_output && !config_.log_file.empty()) {
        appenders_.push_back(
            std::make_unique<FileAppender>(config_.log_file, config_.max_file_size, config_.max_files));
    }
    if (appenders_.empty()) {
        appenders_.push_back(std::make_unique<ConsoleAppender>(config_.color_output));
    }

    if (config_.async_logging) {
        processing_thread_ = std::thread([this]() { processingLoop(); });
    }
}

AsyncLogger::~AsyncLogger() {
    running_ = false;
    queue_cv_.notify_all();
    if (processing_thread_.joinable()) {
        processing_thread_.join();
    }
    flush();
}

void AsyncLogger::trace(const std::string& message,
                        const std::string& source_file,
                        int line_number,
                        const std::string& function_name) {
    logImpl(LogLevel::Level_TRACE, message, source_file, line_number, function_name);
}

void AsyncLogger::debug(const std::string& message,
                        const std::string& source_file,
                        int line_number,
                        const std::string& function_name) {
    logImpl(LogLevel::Level_DEBUG, message, source_file, line_number, function_name);
}

void AsyncLogger::info(const std::string& message,
                       const std::string& source_file,
                       int line_number,
                       const std::string& function_name) {
    logImpl(LogLevel::Level_INFO, message, source_file, line_number, function_name);
}

void AsyncLogger::warn(const std::string& message,
                       const std::string& source_file,
                       int line_number,
                       const std::string& function_name) {
    logImpl(LogLevel::Level_WARN, message, source_file, line_number, function_name);
}

void AsyncLogger::error(const std::string& message,
                        const std::string& source_file,
                        int line_number,
                        const std::string& function_name) {
    logImpl(LogLevel::Level_ERROR, message, source_file, line_number, function_name);
}

void AsyncLogger::fatal(const std::string& message,
                        const std::string& source_file,
                        int line_number,
                        const std::string& function_name) {
    logImpl(LogLevel::Level_FATAL, message, source_file, line_number, function_name);
}

void AsyncLogger::flush() {
    if (config_.async_logging) {
        for (;;) {
            bool empty = false;
            {
                std::lock_guard<std::mutex> lock(queue_mutex_);
                empty = log_queue_.empty();
            }
            if (empty) {
                break;
            }
            queue_cv_.notify_all();
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
        }
    }

    for (auto& appender : appenders_) {
        appender->flush();
    }
}

void AsyncLogger::setLogLevel(LogLevel level) {
    config_.level = level;
}

void AsyncLogger::logImpl(LogLevel level,
                          const std::string& message,
                          const std::string& source_file,
                          int line_number,
                          const std::string& function_name) {
    if (level < config_.level) {
        return;
    }

    LogEntry entry;
    entry.level = level;
    entry.message = message;
    entry.source_file = source_file;
    entry.line_number = line_number;
    entry.function_name = function_name;
    entry.thread_id = std::this_thread::get_id();
    entry.timestamp = std::chrono::system_clock::now();

    if (!config_.async_logging) {
        writeEntry(entry);
        return;
    }

    {
        std::lock_guard<std::mutex> lock(queue_mutex_);
        log_queue_.push(entry);
    }
    queue_cv_.notify_one();
}

void AsyncLogger::processingLoop() {
    while (running_ || !log_queue_.empty()) {
        std::unique_lock<std::mutex> lock(queue_mutex_);
        queue_cv_.wait(lock, [this]() { return !running_ || !log_queue_.empty(); });

        while (!log_queue_.empty()) {
            auto entry = log_queue_.front();
            log_queue_.pop();
            lock.unlock();
            writeEntry(entry);
            lock.lock();
        }
    }
}

void AsyncLogger::writeEntry(const LogEntry& entry) {
    for (auto& appender : appenders_) {
        appender->append(entry);
    }
}

void initializeAdvancedLogger(const LogConfig& config) {
    std::lock_guard<std::mutex> lock(g_logger_mutex);
    g_config = config;
    g_logger = std::make_shared<AsyncLogger>(config);
}

void setLogLevel(LogLevel level) {
    std::shared_ptr<Logger> logger;
    {
        std::lock_guard<std::mutex> lock(g_logger_mutex);
        g_config.level = level;
        if (!g_logger) {
            g_logger = std::make_shared<AsyncLogger>(g_config);
        }
        logger = g_logger;
    }
    logger->setLogLevel(level);
}

void setLogFile(const std::string& filename) {
    std::lock_guard<std::mutex> lock(g_logger_mutex);
    g_config.log_file = filename;
    g_config.file_output = !filename.empty();
    g_logger = std::make_shared<AsyncLogger>(g_config);
}

void logTrace(const std::string& message) {
    getOrCreateLogger()->trace(message);
}

void logDebug(const std::string& message) {
    getOrCreateLogger()->debug(message);
}

void logInfo(const std::string& message) {
    getOrCreateLogger()->info(message);
}

void logWarn(const std::string& message) {
    getOrCreateLogger()->warn(message);
}

void logError(const std::string& message) {
    getOrCreateLogger()->error(message);
}

void logFatal(const std::string& message) {
    getOrCreateLogger()->fatal(message);
}

void flushLogger() {
    getOrCreateLogger()->flush();
}

}  // namespace common
}  // namespace agent_rpc
