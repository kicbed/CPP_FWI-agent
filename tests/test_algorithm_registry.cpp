#include <agent_rpc/research/algorithm_registry.h>
#include <agent_rpc/research/algorithm_listing_tool.h>

#include <gtest/gtest.h>

#include <filesystem>
#include <fstream>
#include <string>

using agent_rpc::research::AlgorithmRegistry;

namespace {

std::filesystem::path repo_root() {
    return std::filesystem::path(__FILE__).parent_path().parent_path();
}

}  // namespace

TEST(AlgorithmRegistryTest, LoadsSeedCardsAndFiltersByDomainAndTag) {
    AlgorithmRegistry registry;
    std::string error;

    ASSERT_TRUE(registry.load_from_directory(repo_root() / "resources" / "algorithms", &error))
        << error;

    EXPECT_EQ(registry.cards().size(), 3u);
    ASSERT_NE(registry.find_by_id("fwi-cuda-mpi"), nullptr);
    EXPECT_EQ(registry.find_by_id("fwi-cuda-mpi")->backend, "dry_run");

    const auto seismic_cards = registry.filter_by_domain("seismic inversion");
    EXPECT_EQ(seismic_cards.size(), 2u);

    const auto fwi_cards = registry.filter_by_tag("fwi");
    ASSERT_EQ(fwi_cards.size(), 1u);
    EXPECT_EQ(fwi_cards[0].id, "fwi-cuda-mpi");
}

TEST(AlgorithmRegistryTest, RejectsInvalidCardsWithClearError) {
    const auto temp_dir = std::filesystem::temp_directory_path() /
        "agent_rpc_algorithm_registry_invalid_test";
    std::filesystem::remove_all(temp_dir);
    std::filesystem::create_directories(temp_dir);

    std::ofstream invalid(temp_dir / "unsafe.json");
    invalid << R"({
        "id": "unsafe",
        "name": "Unsafe Backend",
        "domain": "seismic inversion",
        "parameters": ["niter"],
        "inputs": ["shot_gather"],
        "outputs": ["model"],
        "execution": {
            "backend": "slurm",
            "job_spec_supported": true
        }
    })";
    invalid.close();

    AlgorithmRegistry registry;
    std::string error;
    EXPECT_FALSE(registry.load_from_directory(temp_dir, &error));
    EXPECT_NE(error.find("unsafe.json"), std::string::npos);
    EXPECT_NE(error.find("backend 'slurm' is reserved"), std::string::npos);
    EXPECT_NE(error.find("only dry_run is enabled"), std::string::npos);

    std::filesystem::remove_all(temp_dir);
}

TEST(AlgorithmListingToolTest, ListsAlgorithmSummariesAsToolJson) {
    AlgorithmRegistry registry;
    std::string error;
    ASSERT_TRUE(registry.load_from_directory(repo_root() / "resources" / "algorithms", &error))
        << error;

    const auto result = agent_rpc::research::list_algorithms_for_tool(registry);

    EXPECT_EQ(result["tool"], "list_algorithms");
    EXPECT_EQ(result["count"], 3);
    ASSERT_TRUE(result["algorithms"].is_array());
    EXPECT_EQ(result["algorithms"][0]["backend"], "dry_run");
    EXPECT_TRUE(result["algorithms"][0].contains("id"));
    EXPECT_TRUE(result["algorithms"][0].contains("tags"));
    EXPECT_FALSE(result["algorithms"][0].contains("parameters"));
}
