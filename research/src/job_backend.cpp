#include "agent_rpc/research/job_backend.h"

#include <array>
#include <sstream>
#include <utility>

namespace agent_rpc::research {
namespace {

const std::array<std::pair<const char*, JobBackendType>, 5> kBackendTypes = {{
    {"dry_run", JobBackendType::DryRun},
    {"local", JobBackendType::Local},
    {"ssh", JobBackendType::Ssh},
    {"slurm", JobBackendType::Slurm},
    {"pbs", JobBackendType::Pbs},
}};

std::string supported_backend_list() {
    std::ostringstream out;
    for (std::size_t i = 0; i < kBackendTypes.size(); ++i) {
        if (i > 0) {
            out << ", ";
        }
        out << kBackendTypes[i].first;
    }
    return out.str();
}

}  // namespace

std::string to_string(JobBackendType type) {
    for (const auto& [name, backend_type] : kBackendTypes) {
        if (backend_type == type) {
            return name;
        }
    }
    return "unknown";
}

JobBackendType parse_job_backend_type(const std::string& value) {
    for (const auto& [name, backend_type] : kBackendTypes) {
        if (value == name) {
            return backend_type;
        }
    }
    return JobBackendType::Unknown;
}

std::vector<std::string> supported_job_backend_names() {
    std::vector<std::string> names;
    names.reserve(kBackendTypes.size());
    for (const auto& backend : kBackendTypes) {
        names.emplace_back(backend.first);
    }
    return names;
}

std::vector<std::string> validate_backend_enabled(JobBackendType type) {
    if (type == JobBackendType::DryRun) {
        return {};
    }

    if (type == JobBackendType::Unknown) {
        return {"unknown backend 'unknown'; supported backend values are: " +
                supported_backend_list()};
    }

    return {"backend '" + to_string(type) +
            "' is reserved for future server execution; only dry_run is enabled"};
}

std::vector<std::string> validate_backend_enabled(const std::string& backend) {
    const auto type = parse_job_backend_type(backend);
    if (type == JobBackendType::Unknown) {
        return {"unknown backend '" + backend +
                "'; supported backend values are: " + supported_backend_list()};
    }
    return validate_backend_enabled(type);
}

}  // namespace agent_rpc::research
