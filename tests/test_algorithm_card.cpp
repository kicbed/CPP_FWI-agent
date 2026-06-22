#include <agent_rpc/research/algorithm_card.h>

#include <gtest/gtest.h>

#include <algorithm>

using agent_rpc::research::AlgorithmCard;
using json = nlohmann::json;

TEST(AlgorithmCardTest, RoundTripsJsonExecutionFields) {
    AlgorithmCard card;
    card.id = "fwi-cuda-mpi";
    card.name = "CUDA-MPI FWI";
    card.domain = "seismic inversion";
    card.description = "Future lab solver represented as metadata only.";
    card.tags = {"fwi", "cuda", "mpi"};
    card.parameters = {"frequency_band", "niter"};
    card.inputs = {"velocity_model", "shot_gather"};
    card.outputs = {"inverted_model", "loss_curve"};
    card.failure_modes = {"cycle_skipping"};
    card.job_spec_supported = true;

    const json serialized = card.to_json();
    const auto parsed = AlgorithmCard::from_json(serialized);

    EXPECT_EQ(parsed.id, "fwi-cuda-mpi");
    EXPECT_EQ(parsed.backend, "dry_run");
    EXPECT_TRUE(parsed.job_spec_supported);
    EXPECT_TRUE(parsed.is_valid());
}

TEST(AlgorithmCardTest, RejectsMissingRequiredFieldsAndUnsafeBackend) {
    const json value = {
        {"id", "unsafe"},
        {"name", "Unsafe Backend"},
        {"domain", "seismic inversion"},
        {"parameters", {"niter"}},
        {"inputs", {"shot_gather"}},
        {"outputs", {"model"}},
        {"execution", {{"backend", "slurm"}, {"job_spec_supported", true}}}
    };

    const auto card = AlgorithmCard::from_json(value);
    const auto errors = card.validate();

    EXPECT_NE(std::find(errors.begin(), errors.end(),
                        "backend 'slurm' is reserved for future server execution; only dry_run is enabled"),
              errors.end());

    const auto missing = AlgorithmCard::from_json(json::object());
    const auto missing_errors = missing.validate();

    EXPECT_NE(std::find(missing_errors.begin(), missing_errors.end(),
                        "id is required"),
              missing_errors.end());
    EXPECT_NE(std::find(missing_errors.begin(), missing_errors.end(),
                        "name is required"),
              missing_errors.end());
}

TEST(AlgorithmCardTest, RejectsUnknownBackendWithSupportedValues) {
    const json value = {
        {"id", "unknown-backend"},
        {"name", "Unknown Backend"},
        {"domain", "seismic inversion"},
        {"parameters", {"niter"}},
        {"inputs", {"shot_gather"}},
        {"outputs", {"model"}},
        {"execution", {{"backend", "kubernetes"}, {"job_spec_supported", true}}}
    };

    const auto card = AlgorithmCard::from_json(value);
    const auto errors = card.validate();

    ASSERT_EQ(errors.size(), 1u);
    EXPECT_NE(errors[0].find("unknown backend 'kubernetes'"), std::string::npos);
    EXPECT_NE(errors[0].find("dry_run, local, ssh, slurm, pbs"), std::string::npos);
}
