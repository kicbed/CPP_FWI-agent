/**
 * @file FWIRunner.cpp
 * @brief Fixed-whitelist MCP bridge for the experimental Deepwave FWI worker.
 *
 * This plugin deliberately contains no numerical code and exposes no arbitrary
 * paths, executables, or command-line fragments supplied by callers.
 */

#include "PluginAPI.h"
#include "json.hpp"

#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstdlib>
#include <cstring>
#include <condition_variable>
#include <cstdint>
#include <ctime>
#include <filesystem>
#include <fcntl.h>
#include <fstream>
#include <functional>
#include <iomanip>
#include <mutex>
#include <random>
#include <regex>
#include <spawn.h>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>
#include <utility>
#include <vector>

using json = nlohmann::json;
namespace fs = std::filesystem;

namespace {

constexpr const char* kPython = "/root/.venvs/cpp-fwi-agent/bin/python";
constexpr const char* kModule = "fwi_worker";
constexpr const char* kRunRoot = "/root/fwi-runs";
constexpr const char* kModelId = "marmousi_94_288";
constexpr std::size_t kMaxJsonBytes = 8U * 1024U * 1024U;

#ifndef FWI_PROJECT_ROOT
#define FWI_PROJECT_ROOT "/root/projects/project/agent-communication-main-v2"
#endif

const std::regex kJobIdPattern(
    R"(^fwi-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$)",
    std::regex::ECMAScript);

struct ReaperState {
    std::mutex mutex;
    std::condition_variable cv;
    std::size_t active = 0;
};

ReaperState& reaper_state() {
    // Intentionally process-lifetime storage. The server's SIGINT handler uses
    // exit(), so leaking this tiny state avoids destroying synchronization
    // primitives while a detached worker reaper might still be finishing.
    static ReaperState* state = new ReaperState();
    return *state;
}

static PluginTool methods[] = {
    {
        "fwi_submit_demo",
        "运行固定白名单 Marmousi 二维常密度声学 Deepwave 演示。仅在用户明确要求运行正演、FWI smoke 或 FWI demo 时调用；FWI 理论问题不要调用。提交后立即返回异步 job_id。",
        R"({"type":"object","additionalProperties":false,"properties":{"model_id":{"type":"string","enum":["marmousi_94_288"],"description":"固定白名单模型"},"preset":{"type":"string","enum":["forward","fwi_smoke","fwi_demo"],"description":"forward=合成正演，fwi_smoke=2次迭代链路检查，fwi_demo=5次迭代演示"},"device":{"type":"string","enum":["cuda","cpu"],"description":"单 CUDA GPU 或 CPU"}},"required":["model_id","preset","device"]})"
    },
    {
        "fwi_get_status",
        "查询已提交 FWI 演示任务的真实状态。用于“查看刚才 FWI 任务状态”或给定严格 job_id 的状态查询，不启动计算。",
        R"({"type":"object","additionalProperties":false,"properties":{"job_id":{"type":"string","pattern":"^fwi-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$","description":"fwi_submit_demo 返回的 job_id"}},"required":["job_id"]})"
    },
    {
        "fwi_get_result",
        "读取成功 FWI 演示任务的 manifest 和 metrics，返回图片 URL、数值指标与结果摘要。用于显示反演结果、炮集或损失曲线，不启动计算。",
        R"({"type":"object","additionalProperties":false,"properties":{"job_id":{"type":"string","pattern":"^fwi-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$","description":"已成功任务的 job_id"}},"required":["job_id"]})"
    }
};

std::string utc_now() {
    const auto now = std::chrono::system_clock::now();
    const std::time_t value = std::chrono::system_clock::to_time_t(now);
    std::tm tm{};
    gmtime_r(&value, &tm);
    std::ostringstream out;
    out << std::put_time(&tm, "%Y-%m-%dT%H:%M:%SZ");
    return out.str();
}

std::string job_timestamp() {
    const auto now = std::chrono::system_clock::now();
    const std::time_t value = std::chrono::system_clock::to_time_t(now);
    std::tm tm{};
    gmtime_r(&value, &tm);
    std::ostringstream out;
    out << std::put_time(&tm, "%Y%m%dT%H%M%SZ");
    return out.str();
}

bool valid_job_id(const std::string& job_id) {
    return std::regex_match(job_id, kJobIdPattern);
}

bool is_within(const fs::path& child, const fs::path& parent) {
    auto child_it = child.begin();
    auto parent_it = parent.begin();
    for (; parent_it != parent.end(); ++parent_it, ++child_it) {
        if (child_it == child.end() || *child_it != *parent_it) {
            return false;
        }
    }
    return true;
}

fs::path canonical_run_root() {
    std::error_code ec;
    fs::create_directories(kRunRoot, ec);
    if (ec) {
        throw std::runtime_error("cannot create FWI run root");
    }
    const fs::path root = fs::canonical(kRunRoot, ec);
    if (ec || !fs::is_directory(root)) {
        throw std::runtime_error("FWI run root is unavailable");
    }
    return root;
}

fs::path existing_job_dir(const std::string& job_id) {
    if (!valid_job_id(job_id)) {
        throw std::invalid_argument("invalid job_id format");
    }

    const fs::path root = canonical_run_root();
    const fs::path candidate = root / job_id;
    std::error_code ec;
    const auto status = fs::symlink_status(candidate, ec);
    if (ec || status.type() != fs::file_type::directory || fs::is_symlink(status)) {
        throw std::runtime_error("FWI job not found");
    }
    const fs::path resolved = fs::canonical(candidate, ec);
    if (ec || !is_within(resolved, root) || resolved.parent_path() != root) {
        throw std::runtime_error("FWI job path is outside the run root");
    }
    return resolved;
}

fs::path safe_job_file(const fs::path& job_dir, const char* filename) {
    const fs::path candidate = job_dir / filename;
    std::error_code ec;
    const auto link_status = fs::symlink_status(candidate, ec);
    if (ec || link_status.type() != fs::file_type::regular || fs::is_symlink(link_status)) {
        throw std::runtime_error(std::string(filename) + " is unavailable");
    }
    const fs::path resolved = fs::canonical(candidate, ec);
    if (ec || !is_within(resolved, job_dir) || resolved.parent_path() != job_dir) {
        throw std::runtime_error(std::string(filename) + " escapes the job directory");
    }
    return resolved;
}

json read_json(const fs::path& path) {
    std::error_code ec;
    const auto size = fs::file_size(path, ec);
    if (ec || size > kMaxJsonBytes) {
        throw std::runtime_error("JSON artifact is unavailable or too large");
    }
    std::ifstream input(path);
    if (!input) {
        throw std::runtime_error("cannot open JSON artifact");
    }
    json value;
    input >> value;
    if (!value.is_object()) {
        throw std::runtime_error("JSON artifact must contain an object");
    }
    return value;
}

void atomic_write_json(const fs::path& path, const json& value) {
    const fs::path temp = path.string() + ".tmp-" + std::to_string(::getpid()) + "-" +
                          std::to_string(std::hash<std::thread::id>{}(std::this_thread::get_id()));
    {
        std::ofstream output(temp, std::ios::out | std::ios::trunc);
        if (!output) {
            throw std::runtime_error("cannot create JSON artifact");
        }
        output << value.dump(2) << '\n';
        output.flush();
        if (!output) {
            throw std::runtime_error("cannot write JSON artifact");
        }
    }
    std::error_code ec;
    fs::rename(temp, path, ec);
    if (ec) {
        fs::remove(temp);
        throw std::runtime_error("cannot publish JSON artifact");
    }
}

json status_payload(const std::string& job_id,
                    const std::string& status,
                    const std::string& stage,
                    int iteration,
                    int total_iterations,
                    const std::string& message) {
    return {
        {"job_id", job_id},
        {"status", status},
        {"stage", stage},
        {"iteration", iteration},
        {"total_iterations", total_iterations},
        {"message", message},
        {"updated_at", utc_now()}
    };
}

void mark_unexpected_exit(const fs::path& run_dir, int wait_status) {
    try {
        const fs::path status_path = run_dir / "status.json";
        json status = read_json(status_path);
        const std::string current = status.value("status", "");
        if (current == "succeeded" || current == "failed") {
            return;
        }

        std::string reason;
        if (WIFEXITED(wait_status)) {
            reason = "FWI worker exited with code " + std::to_string(WEXITSTATUS(wait_status));
        } else if (WIFSIGNALED(wait_status)) {
            reason = "FWI worker terminated by signal " + std::to_string(WTERMSIG(wait_status));
        } else {
            reason = "FWI worker exited without a terminal status";
        }
        status["status"] = "failed";
        status["stage"] = "worker_exit";
        status["message"] = reason;
        status["updated_at"] = utc_now();
        atomic_write_json(status_path, status);
    } catch (...) {
        // A worker may atomically replace status.json while the reaper observes it.
        // The run.log still contains the Python process failure in that case.
    }
}

std::vector<std::string> child_environment() {
    // start_system.sh imports .env into the MCP server. Do not propagate that
    // process environment wholesale to numerical jobs: in particular, API
    // keys, tokens, and secrets have no purpose in the worker.
    std::vector<std::string> result = {
        std::string("PYTHONPATH=") + FWI_PROJECT_ROOT,
        std::string("FWI_RUN_ROOT=") + kRunRoot,
        "PYTHONUNBUFFERED=1",
        "PATH=/root/.venvs/cpp-fwi-agent/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME=/root",
        "LANG=C.UTF-8"
    };

    // These exact, non-credential variables can be required by containerized
    // NVIDIA runtimes or an administrator's CUDA library layout. No wildcard
    // copying is used, so names containing KEY/TOKEN/SECRET are excluded.
    const std::vector<std::string> allowed_optional = {
        "CUDA_VISIBLE_DEVICES",
        "NVIDIA_VISIBLE_DEVICES",
        "NVIDIA_DRIVER_CAPABILITIES",
        "LD_LIBRARY_PATH",
        "TMPDIR",
        "OMP_NUM_THREADS"
    };
    for (const auto& name : allowed_optional) {
        if (const char* value = std::getenv(name.c_str()); value && *value) {
            result.push_back(name + "=" + value);
        }
    }
    return result;
}

pid_t spawn_worker(const std::string& command,
                   const fs::path& config_path,
                   const fs::path& run_dir) {
    const fs::path log_path = run_dir / "run.log";
    posix_spawn_file_actions_t actions;
    int rc = posix_spawn_file_actions_init(&actions);
    if (rc != 0) {
        throw std::runtime_error("cannot initialize worker file actions");
    }

    auto destroy_actions = [&actions]() { posix_spawn_file_actions_destroy(&actions); };
    rc = posix_spawn_file_actions_addopen(
        &actions, STDIN_FILENO, "/dev/null", O_RDONLY, 0);
    if (rc == 0) {
        rc = posix_spawn_file_actions_addopen(
            &actions, STDOUT_FILENO, log_path.c_str(), O_WRONLY | O_CREAT | O_APPEND, 0600);
    }
    if (rc == 0) {
        rc = posix_spawn_file_actions_addopen(
            &actions, STDERR_FILENO, log_path.c_str(), O_WRONLY | O_CREAT | O_APPEND, 0600);
    }
    if (rc != 0) {
        destroy_actions();
        throw std::runtime_error("cannot redirect FWI worker log");
    }

    std::vector<std::string> argv_storage = {
        kPython,
        "-m",
        kModule,
        command,
        "--config",
        config_path.string(),
        "--run-dir",
        run_dir.string()
    };
    std::vector<char*> argv;
    argv.reserve(argv_storage.size() + 1);
    for (auto& item : argv_storage) argv.push_back(item.data());
    argv.push_back(nullptr);

    std::vector<std::string> env_storage = child_environment();
    std::vector<char*> envp;
    envp.reserve(env_storage.size() + 1);
    for (auto& item : env_storage) envp.push_back(item.data());
    envp.push_back(nullptr);

    pid_t pid = -1;
    rc = posix_spawn(&pid, kPython, &actions, nullptr, argv.data(), envp.data());
    destroy_actions();
    if (rc != 0) {
        throw std::runtime_error("cannot start fixed FWI worker: " + std::string(std::strerror(rc)));
    }

    ReaperState& state = reaper_state();
    {
        std::lock_guard<std::mutex> lock(state.mutex);
        ++state.active;
    }
    try {
        std::thread([pid, run_dir]() {
            int wait_status = 0;
            pid_t waited_pid = -1;
            do {
                waited_pid = ::waitpid(pid, &wait_status, 0);
            } while (waited_pid < 0 && errno == EINTR);
            if (waited_pid == pid) mark_unexpected_exit(run_dir, wait_status);
            {
                ReaperState& state = reaper_state();
                std::lock_guard<std::mutex> lock(state.mutex);
                --state.active;
            }
            reaper_state().cv.notify_all();
        }).detach();
    } catch (...) {
        {
            std::lock_guard<std::mutex> lock(state.mutex);
            --state.active;
        }
        state.cv.notify_all();
        ::kill(pid, SIGTERM);
        int wait_status = 0;
        while (::waitpid(pid, &wait_status, 0) < 0 && errno == EINTR) {
        }
        throw std::runtime_error("cannot create FWI worker reaper");
    }
    return pid;
}

std::string random_hex_12() {
    std::random_device random;
    const auto ticks = std::chrono::high_resolution_clock::now().time_since_epoch().count();
    std::uint64_t value = (static_cast<std::uint64_t>(random()) << 32U) ^
                          static_cast<std::uint64_t>(random()) ^
                          static_cast<std::uint64_t>(ticks) ^
                          static_cast<std::uint64_t>(::getpid());
    std::ostringstream out;
    out << std::hex << std::setfill('0') << std::setw(12) << (value & 0xffffffffffffULL);
    return out.str();
}

std::pair<std::string, fs::path> create_job_dir() {
    const fs::path root = canonical_run_root();
    for (int attempt = 0; attempt < 32; ++attempt) {
        const std::string job_id = "fwi-" + job_timestamp() + "-" + random_hex_12();
        const fs::path candidate = root / job_id;
        std::error_code ec;
        if (fs::create_directory(candidate, ec)) {
            const fs::path resolved = fs::canonical(candidate, ec);
            if (ec || !is_within(resolved, root) || resolved.parent_path() != root) {
                fs::remove_all(candidate);
                throw std::runtime_error("created FWI job path failed containment validation");
            }
            return {job_id, resolved};
        }
        if (ec && ec != std::errc::file_exists) {
            throw std::runtime_error("cannot create FWI job directory");
        }
    }
    throw std::runtime_error("cannot allocate a unique FWI job_id");
}

void require_exact_keys(const json& args, const std::vector<std::string>& expected) {
    if (!args.is_object()) {
        throw std::invalid_argument("arguments must be a JSON object");
    }
    if (args.size() != expected.size()) {
        throw std::invalid_argument("arguments contain missing or unsupported fields");
    }
    for (const auto& key : expected) {
        if (!args.contains(key) || !args.at(key).is_string()) {
            throw std::invalid_argument(key + " must be a string");
        }
    }
}

std::string string_arg(const json& args, const char* key) {
    const std::string value = args.at(key).get<std::string>();
    if (value.empty()) throw std::invalid_argument(std::string(key) + " must not be empty");
    return value;
}

json submit_demo(const json& args) {
    require_exact_keys(args, {"model_id", "preset", "device"});
    const std::string model_id = string_arg(args, "model_id");
    const std::string preset = string_arg(args, "preset");
    const std::string device = string_arg(args, "device");

    if (model_id != kModelId) {
        throw std::invalid_argument("model_id is not in the fixed whitelist");
    }
    if (preset != "forward" && preset != "fwi_smoke" && preset != "fwi_demo") {
        throw std::invalid_argument("preset must be forward, fwi_smoke, or fwi_demo");
    }
    if (device != "cuda" && device != "cpu") {
        throw std::invalid_argument("device must be cuda or cpu");
    }

    auto [job_id, run_dir] = create_job_dir();
    const int iterations = preset == "fwi_smoke" ? 2 : (preset == "fwi_demo" ? 5 : 0);
    try {
        const json config = {
            {"job_id", job_id},
            {"model_id", model_id},
            {"preset", preset},
            {"device", device}
        };
        atomic_write_json(run_dir / "config.original.json", config);
        atomic_write_json(
            run_dir / "status.json",
            status_payload(job_id, "queued", "queued", 0, iterations, "FWI job queued"));

        const std::string command = preset == "forward" ? "forward" : "invert";
        spawn_worker(command, run_dir / "config.original.json", run_dir);
    } catch (...) {
        try {
            atomic_write_json(
                run_dir / "status.json",
                status_payload(job_id, "failed", "submit", 0, iterations,
                               "FWI worker could not be started"));
        } catch (...) {
        }
        throw;
    }

    return {
        {"type", "fwi_job_submitted"},
        {"job_id", job_id},
        {"status", "queued"},
        {"status_url", "/fwi-artifacts/" + job_id + "/status.json"}
    };
}

json get_status(const json& args) {
    require_exact_keys(args, {"job_id"});
    const std::string job_id = string_arg(args, "job_id");
    const fs::path job_dir = existing_job_dir(job_id);
    json result = read_json(safe_job_file(job_dir, "status.json"));
    if (result.value("job_id", "") != job_id) {
        throw std::runtime_error("status.json job_id does not match the requested job");
    }
    result["type"] = "fwi_job_status";
    result["job_id"] = job_id;
    result["status_url"] = "/fwi-artifacts/" + job_id + "/status.json";
    if (result.value("status", "") == "succeeded") {
        result["result_url"] = "/fwi-artifacts/" + job_id + "/manifest.json";
    }
    return result;
}

json get_result(const json& args) {
    require_exact_keys(args, {"job_id"});
    const std::string job_id = string_arg(args, "job_id");
    const fs::path job_dir = existing_job_dir(job_id);
    const json status = read_json(safe_job_file(job_dir, "status.json"));
    if (status.value("job_id", "") != job_id) {
        throw std::runtime_error("status.json job_id does not match the requested job");
    }
    if (status.value("status", "") != "succeeded") {
        throw std::runtime_error("FWI result is available only after the job succeeds");
    }

    json manifest = read_json(safe_job_file(job_dir, "manifest.json"));
    const json metrics = read_json(safe_job_file(job_dir, "metrics.json"));
    if (manifest.value("job_id", "") != job_id ||
        manifest.value("type", "") != "fwi_result") {
        throw std::runtime_error("manifest.json identity does not match the requested job");
    }
    manifest["type"] = "fwi_result";
    manifest["job_id"] = job_id;
    manifest["status"] = "succeeded";
    manifest["metrics"] = metrics;
    return manifest;
}

char* copy_response(const json& response) {
    const std::string encoded = response.dump();
    char* buffer = new char[encoded.size() + 1];
    std::memcpy(buffer, encoded.c_str(), encoded.size() + 1);
    return buffer;
}

json success_response(const json& payload) {
    return {
        {"content", json::array({{{"type", "text"}, {"text", payload.dump()}}})},
        {"isError", false}
    };
}

json error_response(const std::string& message) {
    const json payload = {{"type", "fwi_error"}, {"message", message}};
    return {
        {"content", json::array({{{"type", "text"}, {"text", payload.dump()}}})},
        {"isError", true}
    };
}

}  // namespace

const char* GetNameImpl() { return "fwi-runner"; }
const char* GetVersionImpl() { return "1.0.0"; }
PluginType GetTypeImpl() { return PLUGIN_TYPE_TOOLS; }
int InitializeImpl() {
    try {
        canonical_run_root();
        return 1;
    } catch (...) {
        return 0;
    }
}

char* HandleRequestImpl(const char* req) {
    try {
        if (!req) throw std::invalid_argument("request is null");
        const json request = json::parse(req);
        if (!request.contains("params") || !request.at("params").is_object() ||
            !request.at("params").contains("name") ||
            !request.at("params").at("name").is_string() ||
            !request.at("params").contains("arguments")) {
            throw std::invalid_argument("invalid MCP tool request");
        }
        const std::string name = request.at("params").at("name").get<std::string>();
        const json& args = request.at("params").at("arguments");

        if (name == "fwi_submit_demo") return copy_response(success_response(submit_demo(args)));
        if (name == "fwi_get_status") return copy_response(success_response(get_status(args)));
        if (name == "fwi_get_result") return copy_response(success_response(get_result(args)));
        throw std::invalid_argument("unknown FWI runner tool");
    } catch (const std::exception& error) {
        return copy_response(error_response(error.what()));
    }
}

void ShutdownImpl() {
    ReaperState& state = reaper_state();
    std::unique_lock<std::mutex> lock(state.mutex);
    state.cv.wait(lock, [&state]() { return state.active == 0; });
}

int GetToolCountImpl() { return static_cast<int>(sizeof(methods) / sizeof(methods[0])); }
const PluginTool* GetToolImpl(int index) {
    if (index < 0 || index >= GetToolCountImpl()) return nullptr;
    return &methods[index];
}

static PluginAPI plugin = {
    GetNameImpl,
    GetVersionImpl,
    GetTypeImpl,
    InitializeImpl,
    HandleRequestImpl,
    ShutdownImpl,
    GetToolCountImpl,
    GetToolImpl,
    nullptr,
    nullptr,
    nullptr,
    nullptr
};

extern "C" PLUGIN_API PluginAPI* CreatePlugin() { return &plugin; }
extern "C" PLUGIN_API void DestroyPlugin(PluginAPI*) {}
