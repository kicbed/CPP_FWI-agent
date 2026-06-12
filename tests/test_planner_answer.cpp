#include <agent_rpc/research/algorithm_registry.h>
#include <agent_rpc/research/planner_answer.h>
#include <agent_rpc/research/planner_context.h>
#include <agent_rpc/research/research_knowledge.h>

#include <gtest/gtest.h>

#include <algorithm>
#include <filesystem>
#include <string>

using agent_rpc::research::AlgorithmRegistry;
using agent_rpc::research::ResearchKnowledgeBase;
using agent_rpc::research::build_planner_answer;
using agent_rpc::research::build_planner_context;
using agent_rpc::research::infer_planner_context_request;

namespace {

std::filesystem::path repo_root() {
    return std::filesystem::path(__FILE__).parent_path().parent_path();
}

bool contains_text(const std::string& text, const std::string& needle) {
    return text.find(needle) != std::string::npos;
}

}  // namespace

TEST(PlannerAnswerTest, BuildsStructuredMarmousiFwiPlan) {
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
    const auto answer = build_planner_answer(request, context);

    EXPECT_EQ(answer.algorithm_id, "fwi-cuda-mpi");
    EXPECT_TRUE(contains_text(answer.algorithm_name, "FWI"));
    EXPECT_TRUE(contains_text(answer.recommendation, "multi-scale"));

    ASSERT_FALSE(answer.parameters.empty());
    const auto frequency_parameter = std::find_if(
        answer.parameters.begin(), answer.parameters.end(),
        [](const auto& parameter) {
            return parameter.name == "frequency_band";
        });
    ASSERT_NE(frequency_parameter, answer.parameters.end());
    EXPECT_TRUE(contains_text(frequency_parameter->value, "lowest reliable"));
    EXPECT_TRUE(contains_text(frequency_parameter->rationale, "cycle"));

    ASSERT_FALSE(answer.risks.empty());
    EXPECT_NE(std::find_if(
        answer.risks.begin(), answer.risks.end(),
        [](const auto& risk) {
            return risk.name == "cycle_skipping" &&
                contains_text(risk.mitigation, "lowest reliable");
        }), answer.risks.end());

    EXPECT_EQ(answer.experiment_spec.algorithm_id, "fwi-cuda-mpi");
    EXPECT_EQ(answer.experiment_spec.dataset_id, "marmousi");
    EXPECT_EQ(answer.experiment_spec.resources.mpi_processes, 4);
    EXPECT_EQ(answer.experiment_spec.resources.gpu_count, 1);
    EXPECT_TRUE(answer.experiment_spec.validate().empty());
    EXPECT_NE(std::find(answer.experiment_spec.expected_outputs.begin(),
                        answer.experiment_spec.expected_outputs.end(),
                        "loss_curve"),
              answer.experiment_spec.expected_outputs.end());

    EXPECT_TRUE(answer.job_spec.validate().empty());
    EXPECT_TRUE(contains_text(answer.job_spec.command, "mpirun -np 4"));
    EXPECT_TRUE(contains_text(answer.job_spec.command, "--dry-run"));
    EXPECT_TRUE(contains_text(answer.dry_run_job_text, "dry_run: true"));

    const auto record = answer.experiment_record_json();
    EXPECT_EQ(record["schema"], "lab-agent-experiment-record-v0.4");
    EXPECT_TRUE(record["dry_run"]);
    EXPECT_FALSE(record["real_execution_enabled"]);
    EXPECT_EQ(record["experiment_spec"]["algorithm_id"], "fwi-cuda-mpi");
    EXPECT_EQ(record["job_spec"]["backend"], "dry_run");
}

TEST(PlannerAnswerTest, RendersMarkdownWithSafetyAndReproducibilitySections) {
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
    const auto answer = build_planner_answer(request, context);
    const auto markdown = answer.render_markdown();

    EXPECT_TRUE(contains_text(markdown, "## Algorithm Recommendation"));
    EXPECT_TRUE(contains_text(markdown, "| Parameter | Value | Rationale |"));
    EXPECT_TRUE(contains_text(markdown, "## Risk Analysis"));
    EXPECT_TRUE(contains_text(markdown, "## ExperimentSpec"));
    EXPECT_TRUE(contains_text(markdown, "## Dry-run JobSpec"));
    EXPECT_TRUE(contains_text(markdown, "## Reproducible Experiment Record"));
    EXPECT_TRUE(contains_text(markdown, "real CUDA/MPI execution is not enabled"));
    EXPECT_TRUE(contains_text(markdown, "dry_run: true"));
}
