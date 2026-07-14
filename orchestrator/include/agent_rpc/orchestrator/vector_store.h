/**
 * @file vector_store.h
 * @brief Symlink-safe, file-backed embedding cache.
 *
 * The cache is an optimization only.  Files are opened relative to a verified
 * directory descriptor, never by following a caller-controlled path, and
 * updates use a private temporary file followed by an atomic rename.
 */

#pragma once

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cctype>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <limits>
#include <map>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>

#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#include <nlohmann/json.hpp>

namespace agent_rpc {
namespace orchestrator {

class VectorStore {
public:
    static constexpr std::size_t kMaxVectorFileBytes = 64U * 1024U * 1024U;

    explicit VectorStore(const std::string& store_dir)
        : store_dir_(validate_store_directory(store_dir)) {}

    bool load(const std::string& name) {
        if (!is_safe_store_name(name)) return false;
        std::lock_guard<std::mutex> lock(mutex_);

        nlohmann::json data;
        if (!load_document_unlocked(name + ".json", &data,
                                    kMaxVectorFileBytes) || !data.is_object() ||
            data.size() > 10000U) {
            return false;
        }

        std::map<std::string, std::vector<float>> loaded;
        try {
            for (const auto& item : data.items()) {
                if (item.key().empty() || item.key().size() > 512U ||
                    !item.value().is_array() || item.value().empty() ||
                    item.value().size() > 65536U) {
                    return false;
                }
                auto vector = item.value().get<std::vector<float>>();
                if (!std::all_of(vector.begin(), vector.end(), [](float value) {
                        return std::isfinite(value);
                    })) {
                    return false;
                }
                loaded.emplace(item.key(), std::move(vector));
            }
        } catch (const nlohmann::json::exception&) {
            return false;
        }

        stores_[name] = std::move(loaded);
        return true;
    }

    bool save(const std::string& name) {
        if (!is_safe_store_name(name)) return false;
        std::lock_guard<std::mutex> lock(mutex_);

        const auto found = stores_.find(name);
        if (found == stores_.end() || found->second.size() > 10000U) {
            return false;
        }
        nlohmann::json data = nlohmann::json::object();
        for (const auto& [key, vector] : found->second) {
            if (key.empty() || key.size() > 512U || vector.empty() ||
                vector.size() > 65536U ||
                !std::all_of(vector.begin(), vector.end(), [](float value) {
                    return std::isfinite(value);
                })) {
                return false;
            }
            data[key] = vector;
        }
        return save_document_unlocked(name + ".json", data,
                                      kMaxVectorFileBytes);
    }

    // Metadata shares exactly the same no-follow and atomic-write policy as
    // vectors.  `filename` is a basename, not a path.
    bool loadJsonDocument(const std::string& filename, nlohmann::json* output,
                          std::size_t max_bytes) {
        if (output == nullptr || !is_safe_filename(filename)) return false;
        std::lock_guard<std::mutex> lock(mutex_);
        return load_document_unlocked(filename, output, max_bytes);
    }

    bool saveJsonDocument(const std::string& filename,
                          const nlohmann::json& document,
                          std::size_t max_bytes) {
        if (!is_safe_filename(filename)) return false;
        std::lock_guard<std::mutex> lock(mutex_);
        return save_document_unlocked(filename, document, max_bytes);
    }

    std::vector<float> get(const std::string& name, const std::string& key) {
        std::lock_guard<std::mutex> lock(mutex_);
        const auto store = stores_.find(name);
        if (store == stores_.end()) return {};
        const auto value = store->second.find(key);
        return value == store->second.end() ? std::vector<float>{}
                                            : value->second;
    }

    void put(const std::string& name, const std::string& key,
             const std::vector<float>& embedding) {
        std::lock_guard<std::mutex> lock(mutex_);
        stores_[name][key] = embedding;
    }

    void remove(const std::string& name, const std::string& key) {
        std::lock_guard<std::mutex> lock(mutex_);
        const auto store = stores_.find(name);
        if (store != stores_.end()) store->second.erase(key);
    }

    std::vector<std::string> keys(const std::string& name) {
        std::lock_guard<std::mutex> lock(mutex_);
        std::vector<std::string> result;
        const auto store = stores_.find(name);
        if (store != stores_.end()) {
            result.reserve(store->second.size());
            for (const auto& [key, unused] : store->second) {
                (void)unused;
                result.push_back(key);
            }
        }
        return result;
    }

    bool contains(const std::string& name, const std::string& key) {
        std::lock_guard<std::mutex> lock(mutex_);
        const auto store = stores_.find(name);
        return store != stores_.end() &&
               store->second.find(key) != store->second.end();
    }

    std::size_t size(const std::string& name) {
        std::lock_guard<std::mutex> lock(mutex_);
        const auto store = stores_.find(name);
        return store == stores_.end() ? 0U : store->second.size();
    }

    const std::string& storeDirectory() const noexcept { return store_dir_; }

private:
    class ScopedFd {
    public:
        ScopedFd() noexcept = default;
        explicit ScopedFd(int fd) noexcept : fd_(fd) {}
        ~ScopedFd() { if (fd_ >= 0) ::close(fd_); }
        ScopedFd(const ScopedFd&) = delete;
        ScopedFd& operator=(const ScopedFd&) = delete;
        ScopedFd(ScopedFd&& other) noexcept : fd_(other.fd_) {
            other.fd_ = -1;
        }
        ScopedFd& operator=(ScopedFd&& other) noexcept {
            if (this != &other) {
                if (fd_ >= 0) ::close(fd_);
                fd_ = other.fd_;
                other.fd_ = -1;
            }
            return *this;
        }
        int get() const noexcept { return fd_; }
        explicit operator bool() const noexcept { return fd_ >= 0; }
    private:
        int fd_ = -1;
    };

    static std::string validate_store_directory(const std::string& value) {
        if (value.empty() || value.find('\0') != std::string::npos ||
            value.find_first_of("\r\n") != std::string::npos) {
            throw std::invalid_argument("embedding cache directory is invalid");
        }
        const std::filesystem::path input(value);
        if (!input.is_absolute()) {
            throw std::invalid_argument(
                "embedding cache directory must be absolute");
        }
        for (const auto& component : input) {
            if (component == "..") {
                throw std::invalid_argument(
                    "embedding cache directory may not contain '..'");
            }
        }
        const auto normalized = input.lexically_normal();
        if (normalized == normalized.root_path()) {
            throw std::invalid_argument(
                "embedding cache directory may not be the filesystem root");
        }
        return normalized.string();
    }

    static bool is_safe_store_name(const std::string& value) {
        if (value.empty() || value.size() > 64U) return false;
        return std::all_of(value.begin(), value.end(), [](unsigned char c) {
            return std::isalnum(c) != 0 || c == '_' || c == '-';
        });
    }

    static bool is_safe_filename(const std::string& value) {
        if (value.empty() || value.size() > 128U || value == "." ||
            value == ".." || value.front() == '.') {
            return false;
        }
        return std::all_of(value.begin(), value.end(), [](unsigned char c) {
            return std::isalnum(c) != 0 || c == '_' || c == '-' || c == '.';
        });
    }

    ScopedFd open_store_directory(bool create) const {
        ScopedFd current(::open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC));
        if (!current) return {};

        const auto relative = std::filesystem::path(store_dir_).relative_path();
        for (const auto& part_path : relative) {
            const std::string part = part_path.string();
            if (part.empty() || part == ".") continue;
            if (part == ".." || part.find('/') != std::string::npos) return {};

            int next = ::openat(current.get(), part.c_str(),
                                O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
            if (next < 0 && create && errno == ENOENT) {
                if (::mkdirat(current.get(), part.c_str(), 0700) != 0 &&
                    errno != EEXIST) {
                    return {};
                }
                next = ::openat(current.get(), part.c_str(),
                                O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
            }
            if (next < 0) return {};
            current = ScopedFd(next);
        }

        struct stat info {};
        if (::fstat(current.get(), &info) != 0 || !S_ISDIR(info.st_mode) ||
            info.st_uid != ::geteuid()) {
            return {};
        }
        // Never "repair" an arbitrary existing directory: doing so could
        // change permissions on a caller-selected location such as /tmp.
        if ((info.st_mode & 077U) != 0U) return {};
        return current;
    }

    bool load_document_unlocked(const std::string& filename,
                                nlohmann::json* output,
                                std::size_t max_bytes) const {
        if (!is_safe_filename(filename) || output == nullptr || max_bytes == 0U) {
            return false;
        }
        ScopedFd directory = open_store_directory(false);
        if (!directory) return false;
        ScopedFd file(::openat(directory.get(), filename.c_str(),
                               O_RDONLY | O_NOFOLLOW | O_CLOEXEC));
        if (!file) return false;

        struct stat info {};
        if (::fstat(file.get(), &info) != 0 || !S_ISREG(info.st_mode) ||
            info.st_uid != ::geteuid() || (info.st_mode & 077U) != 0U ||
            info.st_size <= 0 ||
            static_cast<std::uintmax_t>(info.st_size) > max_bytes) {
            return false;
        }

        std::string contents(static_cast<std::size_t>(info.st_size), '\0');
        std::size_t offset = 0;
        while (offset < contents.size()) {
            const ssize_t count = ::read(file.get(), contents.data() + offset,
                                         contents.size() - offset);
            if (count < 0 && errno == EINTR) continue;
            if (count <= 0) return false;
            offset += static_cast<std::size_t>(count);
        }
        try {
            *output = nlohmann::json::parse(contents);
            return true;
        } catch (const nlohmann::json::exception&) {
            return false;
        }
    }

    bool save_document_unlocked(const std::string& filename,
                                const nlohmann::json& document,
                                std::size_t max_bytes) const {
        if (!is_safe_filename(filename) || max_bytes == 0U) return false;
        const std::string contents = document.dump(2) + "\n";
        if (contents.empty() || contents.size() > max_bytes) return false;

        ScopedFd directory = open_store_directory(true);
        if (!directory) return false;

        static std::atomic<std::uint64_t> sequence{0};
        const std::string temporary = ".vector-store-tmp-" +
            std::to_string(static_cast<unsigned long long>(::getpid())) + "-" +
            std::to_string(sequence.fetch_add(1, std::memory_order_relaxed));
        ScopedFd file(::openat(directory.get(), temporary.c_str(),
                               O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW |
                                   O_CLOEXEC,
                               0600));
        if (!file) return false;

        bool success = true;
        std::size_t offset = 0;
        while (offset < contents.size()) {
            const ssize_t count = ::write(file.get(), contents.data() + offset,
                                          contents.size() - offset);
            if (count < 0 && errno == EINTR) continue;
            if (count <= 0) {
                success = false;
                break;
            }
            offset += static_cast<std::size_t>(count);
        }
        if (success && ::fsync(file.get()) != 0) success = false;
        if (success && ::renameat(directory.get(), temporary.c_str(),
                                  directory.get(), filename.c_str()) != 0) {
            success = false;
        }
        if (success) {
            (void)::fsync(directory.get());
        } else {
            (void)::unlinkat(directory.get(), temporary.c_str(), 0);
        }
        return success;
    }

    std::string store_dir_;
    std::map<std::string, std::map<std::string, std::vector<float>>> stores_;
    std::mutex mutex_;
};

}  // namespace orchestrator
}  // namespace agent_rpc
