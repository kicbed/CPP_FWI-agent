#include <agent_rpc/research/algorithm_registry.h>
#include <agent_rpc/research/planner_context.h>
#include <agent_rpc/research/research_knowledge.h>

#include <gtest/gtest.h>

#include <algorithm>
#include <filesystem>
#include <string>

using agent_rpc::research::AlgorithmRegistry;
using agent_rpc::research::ResearchKnowledgeBase;
using agent_rpc::research::build_planner_context;
using agent_rpc::research::infer_planner_context_request;

namespace {

std::filesystem::path repo_root() {
    return std::filesystem::path(__FILE__).parent_path().parent_path();
}

}  // namespace

TEST(PlannerContextTest, InfersMarmousiMultiScaleFwiRequest) {
    const auto request = infer_planner_context_request(
        "Plan a Marmousi multi-scale FWI experiment with low frequency "
        "missing and cycle skipping risk.");

    EXPECT_EQ(request.algorithm_tag, "fwi");
    EXPECT_EQ(request.method, "multi-scale-fwi");
    EXPECT_EQ(request.dataset, "marmousi");
    EXPECT_EQ(request.failure_mode, "cycle_skipping");
    EXPECT_EQ(request.parameter, "frequency_band");
}

TEST(PlannerContextTest, BuildsDeterministicAlgorithmAndKnowledgeContext) {
    AlgorithmRegistry registry;
    ResearchKnowledgeBase knowledge;
    std::string error;

    ASSERT_TRUE(registry.load_from_directory(
        repo_root() / "resources" / "algorithms", &error)) << error;
    ASSERT_TRUE(knowledge.load_from_directory(
        repo_root() / "resources" / "research_knowledge", &error)) << error;

    const auto request = infer_planner_context_request(
        "Plan a Marmousi multi-scale FWI experiment with low frequency "
        "missing and cycle skipping risk.");
    const auto context = build_planner_context(registry, knowledge, request);

    ASSERT_FALSE(context.algorithms.empty());
    EXPECT_EQ(context.algorithms[0].id, "fwi-cuda-mpi");

    EXPECT_NE(std::find_if(
        context.notes.begin(), context.notes.end(),
        [](const auto& note) {
            return note.id == "algorithm.multi_scale_fwi";
        }), context.notes.end());
    EXPECT_NE(std::find_if(
        context.notes.begin(), context.notes.end(),
        [](const auto& note) {
            return note.note_type == "failure_case" &&
                std::find(note.failure_modes.begin(), note.failure_modes.end(),
                          "cycle_skipping") != note.failure_modes.end();
        }), context.notes.end());

    ASSERT_FALSE(context.parameter_advice.empty());
    EXPECT_NE(context.parameter_advice[0].find("lowest reliable"),
              std::string::npos);

    const auto rendered = context.render_prompt_context();
    EXPECT_NE(rendered.find("dry_run_only: true"), std::string::npos);
    EXPECT_NE(rendered.find("real_execution_enabled: false"), std::string::npos);
    EXPECT_NE(rendered.find("fwi-cuda-mpi"), std::string::npos);
    EXPECT_NE(rendered.find("algorithm.multi_scale_fwi"), std::string::npos);
}
