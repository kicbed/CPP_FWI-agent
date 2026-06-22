#include <agent_rpc/research/experiment_spec.h>
#include <agent_rpc/research/job_backend.h>
#include <agent_rpc/research/job_spec.h>

#include <gtest/gtest.h>

#include <algorithm>

using namespace agent_rpc::research;

TEST(ExperimentSpecTest, ValidSpecPassesValidation) {
    ExperimentSpec spec;
    spec.algorithm_id = "fwi-cuda-mpi";
    spec.dataset_id = "marmousi2_synthetic";
    spec.parameters["niter"] = "20";
    spec.resources.gpu_count = 1;
    spec.resources.mpi_processes = 4;
    spec.expected_outputs = {"loss_curve", "inverted_model"};

    EXPECT_TRUE(spec.validate().empty());
}

TEST(ExperimentSpecTest, MissingAlgorithmFailsValidation) {
    ExperimentSpec spec;
    spec.dataset_id = "marmousi2_synthetic";
    spec.resources.gpu_count = 1;

    const auto errors = spec.validate();
    EXPECT_NE(std::find(errors.begin(), errors.end(), "algorithm_id is required"),
              errors.end());
}

TEST(ExperimentSpecTest, NegativeGpuCountFailsValidation) {
    ExperimentSpec spec;
    spec.algorithm_id = "fwi-cuda-mpi";
    spec.dataset_id = "marmousi2_synthetic";
    spec.resources.gpu_count = -1;

    const auto errors = spec.validate();
    EXPECT_NE(std::find(errors.begin(), errors.end(), "gpu_count must be >= 0"),
              errors.end());
}

TEST(JobSpecTest, MissingCommandAndWorkingDirectoryFailValidation) {
    JobSpec job;

    const auto errors = job.validate();
    EXPECT_NE(std::find(errors.begin(), errors.end(), "command is required"),
              errors.end());
    EXPECT_NE(std::find(errors.begin(), errors.end(), "working_dir is required"),
              errors.end());
}

TEST(DryRunBackendTest, RenderIncludesDryRunMarkerAndNeverExecutes) {
    JobSpec job;
    job.command = "mpirun -np 4 ./fwi_solver --config experiment.json";
    job.working_dir = "runs/dry-run";
    job.mpi_processes = 4;
    job.gpu_count = 1;
    job.artifact_paths = {"runs/dry-run/loss.csv", "runs/dry-run/model.bin"};

    DryRunBackend backend;
    const auto rendered = backend.render(job);

    EXPECT_NE(rendered.find("dry_run: true"), std::string::npos);
    EXPECT_NE(rendered.find("backend: dry_run"), std::string::npos);
    EXPECT_NE(rendered.find("command: mpirun -np 4"), std::string::npos);
    EXPECT_NE(rendered.find("loss.csv"), std::string::npos);
    EXPECT_TRUE(backend.validate(job).empty());
}

TEST(JobBackendInterfaceTest, DryRunBackendCanBeUsedThroughInterface) {
    JobSpec job;
    job.command = "mpirun -np 4 ./fwi_solver --config experiment.json";
    job.working_dir = "runs/dry-run";
    job.mpi_processes = 4;
    job.gpu_count = 1;

    DryRunBackend dry_run_backend;
    const JobBackend& backend = dry_run_backend;

    EXPECT_EQ(backend.type(), JobBackendType::DryRun);

    const auto errors = backend.validate(job);
    const auto rendered = backend.render(job);
    const auto explanation = backend.explain(job);

    EXPECT_TRUE(errors.empty());
    EXPECT_NE(rendered.find("dry_run: true"), std::string::npos);
    EXPECT_NE(rendered.find("backend: dry_run"), std::string::npos);
    EXPECT_NE(explanation.find("does not execute anything"), std::string::npos);
}

TEST(JobBackendTypeTest, ParsesAllReservedBackendNames) {
    EXPECT_EQ(parse_job_backend_type("dry_run"), JobBackendType::DryRun);
    EXPECT_EQ(parse_job_backend_type("local"), JobBackendType::Local);
    EXPECT_EQ(parse_job_backend_type("ssh"), JobBackendType::Ssh);
    EXPECT_EQ(parse_job_backend_type("slurm"), JobBackendType::Slurm);
    EXPECT_EQ(parse_job_backend_type("pbs"), JobBackendType::Pbs);
    EXPECT_EQ(parse_job_backend_type("kubernetes"), JobBackendType::Unknown);

    EXPECT_EQ(to_string(JobBackendType::DryRun), "dry_run");
    EXPECT_EQ(to_string(JobBackendType::Local), "local");
    EXPECT_EQ(to_string(JobBackendType::Ssh), "ssh");
    EXPECT_EQ(to_string(JobBackendType::Slurm), "slurm");
    EXPECT_EQ(to_string(JobBackendType::Pbs), "pbs");
    EXPECT_EQ(to_string(JobBackendType::Unknown), "unknown");
}

TEST(JobBackendSafetyTest, RejectsReservedNonDryRunBackendsAtRuntime) {
    const std::vector<std::string> reserved_backends = {"local", "ssh", "slurm", "pbs"};

    EXPECT_TRUE(validate_backend_enabled("dry_run").empty());

    for (const auto& backend : reserved_backends) {
        const auto errors = validate_backend_enabled(backend);
        ASSERT_EQ(errors.size(), 1u) << backend;
        EXPECT_NE(errors[0].find("backend '" + backend + "' is reserved"),
                  std::string::npos);
        EXPECT_NE(errors[0].find("only dry_run is enabled"),
                  std::string::npos);
    }
}

TEST(JobBackendSafetyTest, RejectsUnknownBackendValuesWithSupportedList) {
    const auto errors = validate_backend_enabled("kubernetes");

    ASSERT_EQ(errors.size(), 1u);
    EXPECT_NE(errors[0].find("unknown backend 'kubernetes'"), std::string::npos);
    EXPECT_NE(errors[0].find("dry_run, local, ssh, slurm, pbs"), std::string::npos);
}
