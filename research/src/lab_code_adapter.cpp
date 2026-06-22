#include "agent_rpc/research/lab_code_adapter.h"

#include <nlohmann/json.hpp>

#include <algorithm>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <regex>
#include <sstream>
#include <utility>

namespace agent_rpc::research {

namespace {

using json = nlohmann::json;

const std::vector<std::string>& execution_fields() {
    static const std::vector<std::string> fields = {
        "submit_command",
        "ssh_host",
        "slurm_partition",
        "pbs_queue",
        "remote_host",
        "execution_command"
    };
    return fields;
}

std::filesystem::path resolve_existing_path(const std::string& path) {
    const std::filesystem::path requested(path);
    if (std::filesystem::exists(requested)) {
        return requested;
    }

    auto current = std::filesystem::current_path();
    for (int depth = 0; depth < 6; ++depth) {
        const auto candidate = current / requested;
        if (std::filesystem::exists(candidate)) {
            return candidate;
        }
        if (!current.has_parent_path() || current == current.parent_path()) {
            break;
        }
        current = current.parent_path();
    }

    return requested;
}

std::vector<std::string> unsafe_template_path_errors(const std::string& path) {
    std::vector<std::string> errors;
    const std::filesystem::path requested(path);
    if (requested.is_absolute()) {
        errors.push_back("absolute paths are not allowed for config templates");
    }
    for (const auto& part : requested) {
        if (part == "..") {
            errors.push_back("path traversal is not allowed for config templates");
            break;
        }
    }
    return errors;
}

ConfigPlaceholder placeholder_from_json(const json& value) {
    ConfigPlaceholder placeholder;
    placeholder.name = value.value("name", "");
    placeholder.type = value.value("type", "");
    placeholder.required = value.value("required", false);
    placeholder.description = value.value("description", "");
    return placeholder;
}

ConfigTemplate config_template_from_json(const json& value) {
    ConfigTemplate config_template;
    config_template.id = value.value("id", "");
    config_template.algorithm_id = value.value("algorithm_id", "");
    config_template.description = value.value("description", "");
    config_template.format = value.value("format", "");

    if (value.contains("placeholders") && value["placeholders"].is_array()) {
        for (const auto& placeholder_value : value["placeholders"]) {
            config_template.placeholders.push_back(
                placeholder_from_json(placeholder_value));
        }
    }

    return config_template;
}

std::vector<std::string> forbidden_execution_field_errors(const json& value) {
    std::vector<std::string> errors;
    for (const auto& field : execution_fields()) {
        if (value.contains(field)) {
            errors.push_back("execution field is not allowed in v0.6: " + field);
        }
    }
    return errors;
}

std::string to_lower(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(),
                   [](unsigned char c) {
                       return static_cast<char>(std::tolower(c));
                   });
    return value;
}

bool contains_case_insensitive(const std::string& haystack,
                               const std::string& needle) {
    return to_lower(haystack).find(to_lower(needle)) != std::string::npos;
}

std::string first_matching_line(const std::vector<std::string>& lines,
                                const std::vector<std::string>& needles) {
    for (const auto& line : lines) {
        for (const auto& needle : needles) {
            if (contains_case_insensitive(line, needle)) {
                return line;
            }
        }
    }
    return "";
}

double value_or_zero(const std::map<std::string, std::string>& values,
                     const std::string& key) {
    const auto it = values.find(key);
    if (it == values.end()) {
        return 0.0;
    }
    try {
        return std::stod(it->second);
    } catch (const std::exception&) {
        return 0.0;
    }
}

void add_finding(std::vector<FailureFinding>* findings,
                 std::string code,
                 std::string severity,
                 std::string evidence,
                 std::string suggested_next_check) {
    findings->push_back({
        std::move(code),
        std::move(severity),
        std::move(evidence),
        std::move(suggested_next_check)
    });
}

std::string likely_cause_for(const std::string& code) {
    if (code == "loss_stagnation") {
        return "Optimization may be stuck because frequency schedule, step length, normalization, or gradient scaling is not appropriate.";
    }
    if (code == "cycle_skipping_risk") {
        return "Starting frequency may be too high or low-frequency content may be missing, increasing cycle-skipping risk.";
    }
    if (code == "nan_or_inf_instability") {
        return "Numerical instability may come from step length, scaling, boundary conditions, or invalid model values.";
    }
    if (code == "resource_limit") {
        return "The requested model size, memory layout, or MPI/GPU mapping may exceed available local resources.";
    }
    return "The adapter detected a diagnostic signal that should be checked before any real execution.";
}

}  // namespace

std::vector<std::string> ConfigTemplate::validate() const {
    std::vector<std::string> errors;

    if (id.empty()) errors.push_back("id is required");
    if (algorithm_id.empty()) errors.push_back("algorithm_id is required");
    if (description.empty()) errors.push_back("description is required");
    if (placeholders.empty()) errors.push_back("placeholders must not be empty");

    for (const auto& placeholder : placeholders) {
        if (placeholder.name.empty()) {
            errors.push_back("placeholder name is required");
        }
        if (placeholder.type.empty()) {
            errors.push_back("placeholder type is required: " + placeholder.name);
        }
        if (placeholder.description.empty()) {
            errors.push_back("placeholder description is required: " +
                             placeholder.name);
        }
    }

    return errors;
}

ConfigTemplateLoadResult load_config_template(const std::string& path) {
    ConfigTemplateLoadResult result;
    result.errors = unsafe_template_path_errors(path);
    if (!result.errors.empty()) {
        return result;
    }

    const auto resolved_path = resolve_existing_path(path);

    std::ifstream input(resolved_path);
    if (!input) {
        result.errors.push_back("failed to open config template: " +
                                resolved_path.string());
        return result;
    }

    try {
        json value;
        input >> value;

        result.errors = forbidden_execution_field_errors(value);
        result.config_template = config_template_from_json(value);
        const auto validation_errors = result.config_template.validate();
        result.errors.insert(result.errors.end(),
                             validation_errors.begin(),
                             validation_errors.end());
    } catch (const std::exception& ex) {
        result.errors.push_back("failed to parse config template " +
                                resolved_path.filename().string() + ": " +
                                ex.what());
    }

    return result;
}

ConfigRenderResult render_config_preview(
    const ConfigTemplate& config_template,
    const std::map<std::string, std::string>& values) {
    ConfigRenderResult result;

    const auto validation_errors = config_template.validate();
    result.errors.insert(result.errors.end(),
                         validation_errors.begin(),
                         validation_errors.end());

    std::ostringstream preview;
    preview << "dry_run: true\n";
    preview << "template_id: " << config_template.id << "\n";
    preview << "algorithm_id: " << config_template.algorithm_id << "\n";
    preview << "format: " << config_template.format << "\n";
    preview << "[parameters]\n";

    for (const auto& placeholder : config_template.placeholders) {
        const auto it = values.find(placeholder.name);
        if (it == values.end()) {
            if (placeholder.required) {
                result.errors.push_back("missing required value: " +
                                        placeholder.name);
            }
            continue;
        }
        preview << placeholder.name << " = " << it->second << "\n";
    }

    result.preview_text = preview.str();
    return result;
}

LabLogParseResult parse_lab_log(const std::string& log_text) {
    LabLogParseResult result;
    std::istringstream input(log_text);
    std::string line;
    std::regex iter_pattern(
        R"(ITER\s+([0-9]+)\s+FREQ\s+([0-9]+(?:\.[0-9]+)?)\s+LOSS\s+([0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?))");
    std::regex status_pattern(R"(STATUS\s+([A-Za-z0-9_\-]+))");

    while (std::getline(input, line)) {
        std::smatch match;
        if (std::regex_search(line, match, iter_pattern)) {
            LossCurvePoint point;
            point.iteration = std::stoi(match[1].str());
            point.frequency_hz = std::stod(match[2].str());
            point.loss = std::stod(match[3].str());
            result.loss_curve.push_back(point);
            continue;
        }

        if (line.rfind("WARN", 0) == 0 ||
            line.find(" WARN ") != std::string::npos) {
            result.warnings.push_back(line);
            result.diagnostic_lines.push_back(line);
            continue;
        }

        if (line.rfind("ERROR", 0) == 0 ||
            line.find(" ERROR ") != std::string::npos) {
            result.diagnostic_lines.push_back(line);
            continue;
        }

        if (std::regex_search(line, match, status_pattern)) {
            result.final_status = match[1].str();
        }
    }

    if (log_text.empty()) {
        result.errors.push_back("log text is empty");
    }

    return result;
}

std::vector<FailureFinding> recognize_failure_modes(
    const LabLogParseResult& parsed_log,
    const std::map<std::string, std::string>& config_values) {
    std::vector<FailureFinding> findings;

    const auto plateau_line = first_matching_line(parsed_log.warnings, {
        "plateau",
        "stagnat",
        "not decreasing"
    });
    if (!plateau_line.empty()) {
        add_finding(&findings,
                    "loss_stagnation",
                    "medium",
                    plateau_line,
                    "Check frequency schedule, step length, source wavelet, and gradient scaling before increasing iterations.");
    } else if (parsed_log.loss_curve.size() >= 3) {
        const double first = parsed_log.loss_curve.front().loss;
        const double last = parsed_log.loss_curve.back().loss;
        if (first > 0.0 && ((first - last) / first) < 0.05) {
            add_finding(&findings,
                        "loss_stagnation",
                        "medium",
                        "loss decreased less than 5% across parsed iterations",
                        "Inspect line search, data normalization, and whether the starting frequency is too high.");
        }
    }

    const auto nan_line = first_matching_line(parsed_log.diagnostic_lines, {
        "nan",
        "inf",
        "overflow"
    });
    if (!nan_line.empty()) {
        add_finding(&findings,
                    "nan_or_inf_instability",
                    "high",
                    nan_line,
                    "Reduce step length, validate input scaling, and inspect absorbing boundary or gradient normalization.");
    }

    const double start_frequency = value_or_zero(config_values, "start_frequency_hz");
    const double min_available_frequency =
        value_or_zero(config_values, "min_available_frequency_hz");
    if (start_frequency >= 6.0 || min_available_frequency >= 6.0) {
        add_finding(&findings,
                    "cycle_skipping_risk",
                    "medium",
                    "start_frequency_hz or min_available_frequency_hz is at least 6 Hz",
                    "Try lower-frequency data, AWI/envelope objectives, or a stronger multi-scale warm start before high-frequency FWI.");
    }

    const auto resource_line = first_matching_line(parsed_log.diagnostic_lines, {
        "out of memory",
        "oom",
        "resource",
        "allocation"
    });
    if (!resource_line.empty()) {
        add_finding(&findings,
                    "resource_limit",
                    "high",
                    resource_line,
                    "Check GPU memory, domain decomposition, batch size, model grid dimensions, and MPI/GPU mapping before any real run.");
    }

    return findings;
}

PlannerDiagnosticSummary build_planner_diagnostic_summary(
    const ConfigTemplate& config_template,
    const LabLogParseResult& parsed_log,
    const std::vector<FailureFinding>& findings) {
    PlannerDiagnosticSummary summary;
    summary.dry_run_only = true;
    summary.safety_boundary =
        "No real CUDA/MPI execution is enabled. Do not use SSH, Slurm/PBS, remote servers, arbitrary shell execution, or automatic Code Agent patch application.";

    for (const auto& finding : findings) {
        summary.observed_symptoms.push_back(
            finding.code + " [" + finding.severity + "]: " + finding.evidence);
        summary.likely_causes.push_back(likely_cause_for(finding.code));
        if (!finding.suggested_next_check.empty()) {
            summary.parameter_tuning_suggestions.push_back(
                finding.suggested_next_check);
        }
    }

    if (summary.observed_symptoms.empty() && !parsed_log.loss_curve.empty()) {
        summary.observed_symptoms.push_back(
            "parsed_loss_curve: " +
            std::to_string(parsed_log.loss_curve.size()) +
            " loss points available for " + config_template.algorithm_id);
    }

    return summary;
}

}  // namespace agent_rpc::research
