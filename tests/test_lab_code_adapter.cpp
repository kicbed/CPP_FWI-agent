#include <agent_rpc/research/lab_code_adapter.h>

#include <gtest/gtest.h>

#include <algorithm>
#include <filesystem>
#include <fstream>
#include <map>
#include <sstream>

using namespace agent_rpc::research;

namespace {

const std::string kTemplatePath =
    "resources/lab_code_adapter/config_templates/fwi_marmousi_multiscale.json";
const std::string kLossStagnationLogPath =
    "resources/lab_code_adapter/logs/fwi_loss_stagnation.log";
const std::string kNanInstabilityLogPath =
    "resources/lab_code_adapter/logs/fwi_nan_instability.log";

bool has_error(const std::vector<std::string>& errors, const std::string& needle) {
    return std::any_of(errors.begin(), errors.end(), [&](const auto& error) {
        return error.find(needle) != std::string::npos;
    });
}

bool has_finding(const std::vector<FailureFinding>& findings,
                 const std::string& code) {
    return std::any_of(findings.begin(), findings.end(), [&](const auto& finding) {
        return finding.code == code;
    });
}

std::string read_fixture_text(const std::string& path) {
    std::ifstream input(path);
    if (!input) {
        input.open(std::string("../") + path);
    }
    if (!input) {
        input.open(std::string("../../") + path);
    }
    std::ostringstream contents;
    contents << input.rdbuf();
    return contents.str();
}

}  // namespace

TEST(LabCodeAdapterTest, LoadsConfigTemplatePlaceholdersDeterministically) {
    const auto result = load_config_template(kTemplatePath);

    ASSERT_TRUE(result.errors.empty()) << result.errors.front();
    EXPECT_EQ(result.config_template.id, "fwi-marmousi-multiscale-template");
    EXPECT_EQ(result.config_template.algorithm_id, "fwi-cuda-mpi");
    EXPECT_NE(result.config_template.description.find("Marmousi"), std::string::npos);
    ASSERT_EQ(result.config_template.placeholders.size(), 5u);

    const auto& dataset = result.config_template.placeholders.at(0);
    EXPECT_EQ(dataset.name, "dataset");
    EXPECT_EQ(dataset.type, "string");
    EXPECT_TRUE(dataset.required);
    EXPECT_FALSE(dataset.description.empty());

    std::vector<std::string> names;
    for (const auto& placeholder : result.config_template.placeholders) {
        names.push_back(placeholder.name);
        EXPECT_FALSE(placeholder.type.empty());
        EXPECT_FALSE(placeholder.description.empty());
    }

    EXPECT_EQ(names, (std::vector<std::string>{
                         "dataset",
                         "start_frequency_hz",
                         "max_frequency_hz",
                         "grid_spacing_m",
                         "iteration_count",
                     }));
}

TEST(LabCodeAdapterTest, RejectsConfigTemplateExecutionFields) {
    const std::string unsafe_path =
        "test_lab_code_adapter_unsafe_template.json";
    std::ofstream out(unsafe_path);
    out << R"json({
  "id": "unsafe-template",
  "algorithm_id": "fwi-cuda-mpi",
  "description": "Should be rejected.",
  "submit_command": "sbatch run.sh",
  "ssh_host": "cluster.example.edu",
  "placeholders": [
    {
      "name": "dataset",
      "type": "string",
      "required": true,
      "description": "Dataset identifier."
    }
  ]
})json";
    out.close();

    const auto result = load_config_template(unsafe_path);

    EXPECT_TRUE(has_error(result.errors, "submit_command"));
    EXPECT_TRUE(has_error(result.errors, "ssh_host"));
}

TEST(LabCodeAdapterTest, RejectsAbsoluteConfigTemplatePaths) {
    const auto absolute_path = std::filesystem::absolute(
        "test_lab_code_adapter_absolute_template.json");
    std::ofstream out(absolute_path);
    out << R"json({
  "id": "absolute-template",
  "algorithm_id": "fwi-cuda-mpi",
  "description": "Should be rejected before parsing.",
  "placeholders": [
    {
      "name": "dataset",
      "type": "string",
      "required": true,
      "description": "Dataset identifier."
    }
  ]
})json";
    out.close();

    const auto result = load_config_template(absolute_path.string());
    std::filesystem::remove(absolute_path);

    EXPECT_TRUE(has_error(result.errors, "absolute paths are not allowed"));
}

TEST(LabCodeAdapterTest, RendersDryRunConfigPreviewWithFilledPlaceholders) {
    const auto loaded = load_config_template(kTemplatePath);
    ASSERT_TRUE(loaded.errors.empty()) << loaded.errors.front();

    const auto rendered = render_config_preview(
        loaded.config_template,
        {
            {"dataset", "marmousi2_synthetic"},
            {"start_frequency_hz", "3.0"},
            {"max_frequency_hz", "12.0"},
            {"grid_spacing_m", "20"},
            {"iteration_count", "30"},
        });

    ASSERT_TRUE(rendered.errors.empty()) << rendered.errors.front();
    EXPECT_NE(rendered.preview_text.find("dry_run: true"), std::string::npos);
    EXPECT_NE(rendered.preview_text.find("template_id: fwi-marmousi-multiscale-template"),
              std::string::npos);
    EXPECT_NE(rendered.preview_text.find("algorithm_id: fwi-cuda-mpi"),
              std::string::npos);
    EXPECT_NE(rendered.preview_text.find("dataset = marmousi2_synthetic"),
              std::string::npos);
    EXPECT_NE(rendered.preview_text.find("start_frequency_hz = 3.0"),
              std::string::npos);
    EXPECT_NE(rendered.preview_text.find("iteration_count = 30"),
              std::string::npos);
}

TEST(LabCodeAdapterTest, ParsesLogTextIntoLossCurveWarningsAndStatus) {
    const auto log_text = read_fixture_text(kLossStagnationLogPath);
    ASSERT_FALSE(log_text.empty());

    const auto parsed = parse_lab_log(log_text);

    ASSERT_TRUE(parsed.errors.empty()) << parsed.errors.front();
    ASSERT_EQ(parsed.loss_curve.size(), 4u);
    EXPECT_EQ(parsed.loss_curve.front().iteration, 1);
    EXPECT_DOUBLE_EQ(parsed.loss_curve.front().frequency_hz, 3.0);
    EXPECT_DOUBLE_EQ(parsed.loss_curve.front().loss, 1.25);
    EXPECT_EQ(parsed.loss_curve.back().iteration, 4);
    EXPECT_DOUBLE_EQ(parsed.loss_curve.back().loss, 1.2048);
    ASSERT_EQ(parsed.warnings.size(), 1u);
    EXPECT_NE(parsed.warnings.front().find("loss plateau"), std::string::npos);
    EXPECT_EQ(parsed.final_status, "completed");
}

TEST(LabCodeAdapterTest, RecognizesCommonFailureModesFromLogsAndConfig) {
    const auto stagnation = parse_lab_log(read_fixture_text(kLossStagnationLogPath));
    const auto stagnation_findings = recognize_failure_modes(
        stagnation,
        {
            {"start_frequency_hz", "8.0"},
            {"min_available_frequency_hz", "8.0"},
        });

    EXPECT_TRUE(has_finding(stagnation_findings, "loss_stagnation"));
    EXPECT_TRUE(has_finding(stagnation_findings, "cycle_skipping_risk"));

    const auto nan = parse_lab_log(read_fixture_text(kNanInstabilityLogPath));
    const auto nan_findings = recognize_failure_modes(nan, {});
    EXPECT_TRUE(has_finding(nan_findings, "nan_or_inf_instability"));

    const auto resource_limit = parse_lab_log(
        "ERROR CUDA out of memory while allocating wavefield buffer\n"
        "STATUS failed\n");
    const auto resource_findings = recognize_failure_modes(resource_limit, {});
    ASSERT_TRUE(has_finding(resource_findings, "resource_limit"));

    const auto it = std::find_if(resource_findings.begin(),
                                 resource_findings.end(),
                                 [](const auto& finding) {
                                     return finding.code == "resource_limit";
                                 });
    ASSERT_NE(it, resource_findings.end());
    EXPECT_EQ(it->severity, "high");
    EXPECT_NE(it->suggested_next_check.find("GPU"), std::string::npos);
}

TEST(LabCodeAdapterTest, BuildsPlannerFacingDiagnosticSummary) {
    const auto loaded = load_config_template(kTemplatePath);
    ASSERT_TRUE(loaded.errors.empty()) << loaded.errors.front();

    const auto parsed = parse_lab_log(read_fixture_text(kLossStagnationLogPath));
    const auto findings = recognize_failure_modes(
        parsed,
        {
            {"start_frequency_hz", "8.0"},
            {"min_available_frequency_hz", "8.0"},
        });

    const auto summary = build_planner_diagnostic_summary(
        loaded.config_template,
        parsed,
        findings);

    EXPECT_TRUE(summary.dry_run_only);
    EXPECT_NE(summary.safety_boundary.find("No real CUDA/MPI"),
              std::string::npos);
    EXPECT_NE(summary.safety_boundary.find("SSH"), std::string::npos);
    EXPECT_NE(summary.safety_boundary.find("Slurm/PBS"), std::string::npos);
    EXPECT_NE(summary.observed_symptoms.front().find("loss_stagnation"),
              std::string::npos);
    EXPECT_FALSE(summary.likely_causes.empty());
    EXPECT_FALSE(summary.parameter_tuning_suggestions.empty());
    EXPECT_NE(summary.parameter_tuning_suggestions.front().find("frequency"),
              std::string::npos);
}
