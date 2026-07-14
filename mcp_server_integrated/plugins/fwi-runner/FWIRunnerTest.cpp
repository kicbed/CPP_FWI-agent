#include "PluginAPI.h"
#include "json.hpp"

#include <algorithm>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <cstdint>
#include <iostream>
#include <iomanip>
#include <random>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>

using json = nlohmann::json;
namespace fs = std::filesystem;

namespace {

constexpr const char* kRunRoot = "/root/fwi-runs";

void require(bool condition, const std::string& message) {
    if (!condition) throw std::runtime_error(message);
}

json call(PluginAPI* plugin, const std::string& name, const json& arguments) {
    const json request = {
        {"jsonrpc", "2.0"},
        {"id", 1},
        {"method", "tools/call"},
        {"params", {{"name", name}, {"arguments", arguments}}}
    };
    char* raw = plugin->HandleRequest(request.dump().c_str());
    require(raw != nullptr, "plugin returned a null response");
    const json response = json::parse(raw);
    delete[] raw;
    return response;
}

json payload(const json& response) {
    return json::parse(response.at("content").at(0).at("text").get<std::string>());
}

void write_json(const fs::path& path, const json& value) {
    std::ofstream output(path);
    output << value.dump(2) << '\n';
}

std::string unique_job_id() {
    std::random_device random;
    for (int attempt = 0; attempt < 32; ++attempt) {
        const std::uint64_t value = (static_cast<std::uint64_t>(random()) << 32U) ^ random();
        std::ostringstream suffix;
        suffix << std::hex << std::setfill('0') << std::setw(12)
               << (value & 0xffffffffffffULL);
        const std::string job_id = "fwi-20000101T000000Z-" + suffix.str();
        if (!fs::exists(fs::path(kRunRoot) / job_id)) return job_id;
    }
    throw std::runtime_error("could not allocate a unique test job_id");
}

class CreatedPath {
public:
    explicit CreatedPath(fs::path path) : path_(std::move(path)) {}
    CreatedPath(const CreatedPath&) = delete;
    CreatedPath& operator=(const CreatedPath&) = delete;
    ~CreatedPath() {
        std::error_code ec;
        fs::remove_all(path_, ec);
    }

private:
    fs::path path_;
};

}  // namespace

int main() {
    PluginAPI* plugin = CreatePlugin();
    require(plugin != nullptr, "CreatePlugin failed");
    require(plugin->Initialize() == 1, "plugin initialization failed");
    require(std::string(plugin->GetName()) == "fwi-runner", "unexpected plugin name");
    require(plugin->GetToolCount() == 3, "unexpected tool count");

    std::set<std::string> names;
    for (int i = 0; i < plugin->GetToolCount(); ++i) {
        const PluginTool* tool = plugin->GetTool(i);
        require(tool != nullptr, "missing tool descriptor");
        names.emplace(tool->name);
        const json schema = json::parse(tool->inputSchema);
        require(schema.at("additionalProperties") == false,
                "FWI schemas must reject additional properties");
    }
    require(names == std::set<std::string>({"fwi_submit_demo", "fwi_get_status", "fwi_get_result"}),
            "unexpected FWI tool names");

    const json submit_schema = json::parse(plugin->GetTool(0)->inputSchema);
    require(submit_schema.at("properties").contains("iterations"),
            "submit schema must expose iterations");
    require(submit_schema.at("properties").at("iterations").at("type") == "integer",
            "submit schema must require integer iterations");
    require(submit_schema.at("properties").at("iterations").at("minimum") == 1,
            "submit schema iteration minimum mismatch");
    require(submit_schema.at("properties").at("iterations").at("maximum") == 100,
            "submit schema iteration bound mismatch");
    require(std::find(submit_schema.at("required").begin(),
                      submit_schema.at("required").end(), "iterations") ==
                submit_schema.at("required").end(),
            "iterations must remain optional so preset defaults still work");

    const json invalid_model = call(plugin, "fwi_submit_demo", {
        {"model_id", "../../etc/passwd"}, {"preset", "forward"}, {"device", "cpu"}
    });
    require(invalid_model.at("isError") == true, "invalid model must be rejected");

    const json extra_arg = call(plugin, "fwi_submit_demo", {
        {"model_id", "marmousi_94_288"}, {"preset", "forward"}, {"device", "cpu"},
        {"extra_args", "--arbitrary-shell-text"}
    });
    require(extra_arg.at("isError") == true, "extra arguments must be rejected");

    const json too_many_iterations = call(plugin, "fwi_submit_demo", {
        {"model_id", "marmousi_94_288"}, {"preset", "fwi_demo"}, {"device", "cpu"},
        {"iterations", 101}
    });
    require(too_many_iterations.at("isError") == true,
            "iteration counts above the safety bound must be rejected");

    for (const int invalid_iterations : {-1, 0}) {
        const json invalid_count = call(plugin, "fwi_submit_demo", {
            {"model_id", "marmousi_94_288"}, {"preset", "fwi_demo"},
            {"device", "cpu"}, {"iterations", invalid_iterations}
        });
        require(invalid_count.at("isError") == true,
                "inversion iteration counts below one must be rejected");
    }

    const json fractional_iterations = call(plugin, "fwi_submit_demo", {
        {"model_id", "marmousi_94_288"}, {"preset", "fwi_demo"}, {"device", "cpu"},
        {"iterations", 2.5}
    });
    require(fractional_iterations.at("isError") == true,
            "fractional iteration counts must be rejected");

    for (const json& invalid_type : {json("50"), json(true), json(nullptr)}) {
        const json invalid_count = call(plugin, "fwi_submit_demo", {
            {"model_id", "marmousi_94_288"}, {"preset", "fwi_demo"},
            {"device", "cpu"}, {"iterations", invalid_type}
        });
        require(invalid_count.at("isError") == true,
                "non-integer iteration values must be rejected");
    }

    const json invalid_forward_iterations = call(plugin, "fwi_submit_demo", {
        {"model_id", "marmousi_94_288"}, {"preset", "forward"}, {"device", "cpu"},
        {"iterations", 1}
    });
    require(invalid_forward_iterations.at("isError") == true,
            "forward must reject an iterations argument");

    const json invalid_forward_zero = call(plugin, "fwi_submit_demo", {
        {"model_id", "marmousi_94_288"}, {"preset", "forward"}, {"device", "cpu"},
        {"iterations", 0}
    });
    require(invalid_forward_zero.at("isError") == true,
            "forward must require callers to omit iterations");

    const json traversal = call(plugin, "fwi_get_status", {{"job_id", "../../root"}});
    require(traversal.at("isError") == true, "path traversal job_id must be rejected");

    require(::setenv("FWI_RUN_ROOT", "/", 1) == 0,
            "could not set unsafe FWI_RUN_ROOT for test");
    const json unsafe_root = call(plugin, "fwi_get_status", {
        {"job_id", "fwi-20000101T000000Z-000000000000"}
    });
    require(unsafe_root.at("isError") == true,
            "filesystem root must be rejected as FWI_RUN_ROOT");
    require(payload(unsafe_root).at("message").get<std::string>().find("dedicated") !=
                std::string::npos,
            "unsafe run-root rejection must be explicit");
    require(::setenv("FWI_RUN_ROOT", kRunRoot, 1) == 0,
            "could not restore FWI_RUN_ROOT after test");

    const fs::path root(kRunRoot);
    fs::create_directories(root);
    const std::string job_id = unique_job_id();
    const fs::path job_dir = root / job_id;
    require(fs::create_directory(job_dir), "could not create unique test job directory");
    CreatedPath job_cleanup(job_dir);
    write_json(job_dir / "status.json", {
        {"job_id", job_id}, {"status", "succeeded"}, {"stage", "complete"},
        {"iteration", 2}, {"total_iterations", 2}, {"message", "done"},
        {"updated_at", "2000-01-01T00:00:00Z"}
    });
    write_json(job_dir / "manifest.json", {
        {"type", "fwi_result"}, {"schema_version", "1"}, {"job_id", job_id},
        {"status", "succeeded"}, {"figures", json::array()}
    });
    write_json(job_dir / "metrics.json", {{"initial_loss", 2.0}, {"final_loss", 1.0}});

    const json status_response = call(plugin, "fwi_get_status", {{"job_id", job_id}});
    require(status_response.at("isError") == false, "valid status should succeed");
    const json status = payload(status_response);
    require(status.at("type") == "fwi_job_status", "status response type mismatch");
    require(status.at("result_url") == "/fwi-artifacts/" + job_id + "/manifest.json",
            "status result URL mismatch");

    const json result_response = call(plugin, "fwi_get_result", {{"job_id", job_id}});
    require(result_response.at("isError") == false, "valid result should succeed");
    const json result = payload(result_response);
    require(result.at("type") == "fwi_result", "result response type mismatch");
    require(result.at("metrics").at("final_loss") == 1.0, "metrics were not loaded");

    const std::string link_id = unique_job_id();
    const fs::path link_path = root / link_id;
    fs::create_directory_symlink("/root", link_path);
    CreatedPath link_cleanup(link_path);
    const json symlink_escape = call(plugin, "fwi_get_status", {{"job_id", link_id}});
    require(symlink_escape.at("isError") == true, "symlink job directory must be rejected");
    plugin->Shutdown();
    DestroyPlugin(plugin);
    std::cout << "FWIRunnerPluginTest passed\n";
    return 0;
}
