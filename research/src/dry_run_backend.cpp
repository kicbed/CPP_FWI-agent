#include "agent_rpc/research/job_backend.h"

#include <sstream>

namespace agent_rpc::research {

std::vector<std::string> DryRunBackend::validate(const JobSpec& job) const {
    return job.validate();
}

std::string DryRunBackend::render(const JobSpec& job) const {
    std::ostringstream out;
    out << "dry_run: true\n";
    out << "backend: dry_run\n";
    out << "command: " << job.command << "\n";
    out << "working_dir: " << job.working_dir << "\n";
    out << "mpi_processes: " << job.mpi_processes << "\n";
    out << "gpu_count: " << job.gpu_count << "\n";
    out << "time_limit_minutes: " << job.time_limit_minutes << "\n";

    if (!job.env.empty()) {
        out << "env:\n";
        for (const auto& [key, value] : job.env) {
            out << "  " << key << ": " << value << "\n";
        }
    }

    if (!job.artifact_paths.empty()) {
        out << "artifacts:\n";
        for (const auto& path : job.artifact_paths) {
            out << "  - " << path << "\n";
        }
    }

    return out.str();
}

std::string DryRunBackend::explain(const JobSpec& job) const {
    std::ostringstream out;
    out << "This is a dry-run job preview. It renders the command and expected "
        << "artifacts but does not execute anything.\n";
    out << "Command preview: " << job.command;
    return out.str();
}

}  // namespace agent_rpc::research
