#pragma once

#include <map>
#include <string>
#include <vector>

namespace agent_rpc::research {

struct ConfigPlaceholder {
    std::string name;
    std::string type;
    bool required = false;
    std::string description;
};

struct ConfigTemplate {
    std::string id;
    std::string algorithm_id;
    std::string description;
    std::string format;
    std::vector<ConfigPlaceholder> placeholders;

    std::vector<std::string> validate() const;
};

struct ConfigTemplateLoadResult {
    ConfigTemplate config_template;
    std::vector<std::string> errors;
};

struct ConfigRenderResult {
    std::string preview_text;
    std::vector<std::string> errors;
};

struct LossCurvePoint {
    int iteration = 0;
    double frequency_hz = 0.0;
    double loss = 0.0;
};

struct LabLogParseResult {
    std::vector<LossCurvePoint> loss_curve;
    std::vector<std::string> warnings;
    std::vector<std::string> diagnostic_lines;
    std::string final_status;
    std::vector<std::string> errors;
};

struct FailureFinding {
    std::string code;
    std::string severity;
    std::string evidence;
    std::string suggested_next_check;
};

struct PlannerDiagnosticSummary {
    std::vector<std::string> observed_symptoms;
    std::vector<std::string> likely_causes;
    std::vector<std::string> parameter_tuning_suggestions;
    bool dry_run_only = true;
    std::string safety_boundary;
};

ConfigTemplateLoadResult load_config_template(const std::string& path);
ConfigRenderResult render_config_preview(
    const ConfigTemplate& config_template,
    const std::map<std::string, std::string>& values);
LabLogParseResult parse_lab_log(const std::string& log_text);
std::vector<FailureFinding> recognize_failure_modes(
    const LabLogParseResult& parsed_log,
    const std::map<std::string, std::string>& config_values);
PlannerDiagnosticSummary build_planner_diagnostic_summary(
    const ConfigTemplate& config_template,
    const LabLogParseResult& parsed_log,
    const std::vector<FailureFinding>& findings);

}  // namespace agent_rpc::research
