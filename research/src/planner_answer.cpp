#include "agent_rpc/research/planner_answer.h"

#include "agent_rpc/research/job_backend.h"

#include <algorithm>
#include <set>
#include <sstream>

namespace agent_rpc::research {

namespace {

std::string first_non_empty(const std::string& value,
                            const std::string& fallback) {
    return value.empty() ? fallback : value;
}

bool contains_value(const std::vector<std::string>& values,
                    const std::string& value) {
    return std::find(values.begin(), values.end(), value) != values.end();
}

std::string first_parameter_advice(const PlannerContext& context,
                                   const std::string& fallback) {
    return context.parameter_advice.empty() ? fallback : context.parameter_advice[0];
}

std::vector<std::string> collect_assumptions(const PlannerContext& context) {
    std::vector<std::string> assumptions;
    std::set<std::string> seen;

    for (const auto& note : context.notes) {
        for (const auto& assumption : note.assumptions) {
            if (seen.insert(assumption).second) {
                assumptions.push_back(assumption);
            }
        }
    }

    if (assumptions.empty()) {
        assumptions.push_back("input data, wavelet, and initial model are registered");
    }
    assumptions.push_back("real CUDA/MPI execution is not enabled");
    assumptions.push_back("the generated job is a dry-run preview only");
    return assumptions;
}

std::vector<std::string> collect_expected_outputs(const AlgorithmCard& algorithm) {
    std::vector<std::string> outputs = algorithm.outputs;
    if (!contains_value(outputs, "loss_curve")) {
        outputs.push_back("loss_curve");
    }
    if (!contains_value(outputs, "inverted_model")) {
        outputs.push_back("inverted_model");
    }
    return outputs;
}

PlannerRisk risk_for_failure_mode(const std::string& failure_mode,
                                  const PlannerContext& context) {
    if (failure_mode == "cycle_skipping" ||
        failure_mode == "missing_low_frequency") {
        return {
            "cycle_skipping",
            "local notes flag missing low frequencies and event mismatch as a risk",
            "start from the lowest reliable frequency band; compare AWI or "
            "frequency extrapolation before high-frequency FWI"
        };
    }
    if (failure_mode == "unstable_gradient") {
        return {
            "unstable_gradient",
            "algorithm notes mention unstable gradients for rough starting models",
            "use conservative step length, stronger smoothing, and inspect "
            "loss reduction before increasing step size"
        };
    }
    if (failure_mode == "mpi_runtime_error") {
        return {
            "mpi_runtime_error",
            "FWI solver metadata is for future CUDA/MPI integration",
            "keep this as a dry-run JobSpec until the controlled backend exists"
        };
    }

    return {
        failure_mode,
        "local planner context includes this failure mode",
        context.parameter_advice.empty()
            ? "review assumptions and run another dry-run planning pass"
            : context.parameter_advice[0]
    };
}

std::vector<PlannerRisk> collect_risks(const PlannerContext& context) {
    std::vector<PlannerRisk> risks;
    std::set<std::string> seen;

    for (const auto& note : context.notes) {
        for (const auto& failure_mode : note.failure_modes) {
            const auto risk = risk_for_failure_mode(failure_mode, context);
            if (seen.insert(risk.name).second) {
                risks.push_back(risk);
            }
        }
    }

    if (risks.empty()) {
        risks.push_back({
            "insufficient_context",
            "no local failure-mode note matched the request",
            "treat the answer as a draft and add a local knowledge note"
        });
    }
    return risks;
}

json experiment_spec_json(const ExperimentSpec& spec) {
    return {
        {"algorithm_id", spec.algorithm_id},
        {"dataset_id", spec.dataset_id},
        {"parameters", spec.parameters},
        {"resources", {
            {"mpi_processes", spec.resources.mpi_processes},
            {"gpu_count", spec.resources.gpu_count},
            {"time_limit_minutes", spec.resources.time_limit_minutes}
        }},
        {"expected_outputs", spec.expected_outputs}
    };
}

json job_spec_json(const JobSpec& job) {
    return {
        {"backend", "dry_run"},
        {"command", job.command},
        {"working_dir", job.working_dir},
        {"env", job.env},
        {"mpi_processes", job.mpi_processes},
        {"gpu_count", job.gpu_count},
        {"time_limit_minutes", job.time_limit_minutes},
        {"artifact_paths", job.artifact_paths}
    };
}

json parameters_json(const std::vector<PlannerParameter>& parameters) {
    json values = json::array();
    for (const auto& parameter : parameters) {
        values.push_back({
            {"name", parameter.name},
            {"value", parameter.value},
            {"rationale", parameter.rationale}
        });
    }
    return values;
}

json risks_json(const std::vector<PlannerRisk>& risks) {
    json values = json::array();
    for (const auto& risk : risks) {
        values.push_back({
            {"name", risk.name},
            {"evidence", risk.evidence},
            {"mitigation", risk.mitigation}
        });
    }
    return values;
}

}  // namespace

PlannerAnswer build_planner_answer(
    const PlannerContextRequest& request,
    const PlannerContext& context) {
    PlannerAnswer answer;

    const AlgorithmCard fallback_algorithm = {
        "unknown-algorithm",
        "Unknown Algorithm",
        "research computing",
        "No matching AlgorithmCard was found.",
        {},
        {},
        {},
        {"loss_curve", "inverted_model"},
        {},
        false,
        "dry_run"
    };
    const auto& algorithm =
        context.algorithms.empty() ? fallback_algorithm : context.algorithms[0];

    answer.algorithm_id = algorithm.id;
    answer.algorithm_name = algorithm.name;
    answer.recommendation =
        "Use " + algorithm.name +
        " as a dry-run multi-scale planning baseline grounded in local "
        "AlgorithmCards and research notes.";
    answer.assumptions = collect_assumptions(context);

    const std::string dataset = first_non_empty(request.dataset, "marmousi");
    const std::string frequency_advice = first_parameter_advice(
        context,
        "Use the lowest reliable frequency band first, then move upward in stages.");

    answer.parameters = {
        {
            "frequency_band",
            "lowest reliable band -> staged upward bands",
            frequency_advice + " This directly reduces cycle-skipping risk."
        },
        {
            "niter",
            "10 for the first dry-run stage",
            "Use a small iteration count until the plan, data paths, and "
            "artifact expectations are validated."
        },
        {
            "step_length",
            "conservative line-search step",
            "Start conservatively and inspect loss reduction before increasing "
            "the update size."
        },
        {
            "regularization",
            "smooth initial model and gradient stabilization",
            "Use stronger smoothing when gradients are unstable or the starting "
            "model is rough."
        }
    };

    answer.risks = collect_risks(context);
    answer.next_steps = {
        "Confirm shot gather, wavelet, and initial model paths for the same grid.",
        "Review the dry-run ExperimentSpec and JobSpec before any real backend exists.",
        "If loss does not decrease in future real runs, inspect cycle skipping, "
        "step length, gradient scaling, and low-frequency coverage.",
        "Keep all execution disabled until an authenticated backend is added."
    };

    answer.experiment_spec.algorithm_id = algorithm.id;
    answer.experiment_spec.dataset_id = dataset;
    for (const auto& parameter : answer.parameters) {
        answer.experiment_spec.parameters[parameter.name] = parameter.value;
    }
    answer.experiment_spec.resources.mpi_processes = 4;
    answer.experiment_spec.resources.gpu_count = 1;
    answer.experiment_spec.resources.time_limit_minutes = 60;
    answer.experiment_spec.expected_outputs = collect_expected_outputs(algorithm);

    answer.job_spec.command =
        "mpirun -np 4 ./fwi_solver --config experiment.json --dry-run";
    answer.job_spec.working_dir =
        "runs/dry-run/" + dataset + "-multi-scale-fwi";
    answer.job_spec.env = {
        {"DRY_RUN", "true"},
        {"REAL_EXECUTION_ENABLED", "false"}
    };
    answer.job_spec.mpi_processes = 4;
    answer.job_spec.gpu_count = 1;
    answer.job_spec.time_limit_minutes = 60;
    answer.job_spec.artifact_paths = {
        answer.job_spec.working_dir + "/experiment_record.json",
        answer.job_spec.working_dir + "/loss_curve.csv",
        answer.job_spec.working_dir + "/inverted_model.bin"
    };

    DryRunBackend backend;
    answer.dry_run_job_text = backend.render(answer.job_spec);
    return answer;
}

json PlannerAnswer::experiment_record_json() const {
    return {
        {"schema", "lab-agent-experiment-record-v0.4"},
        {"dry_run", true},
        {"real_execution_enabled", false},
        {"algorithm", {
            {"id", algorithm_id},
            {"name", algorithm_name}
        }},
        {"recommendation", recommendation},
        {"assumptions", assumptions},
        {"parameters", parameters_json(parameters)},
        {"risks", risks_json(risks)},
        {"next_steps", next_steps},
        {"experiment_spec", experiment_spec_json(experiment_spec)},
        {"job_spec", job_spec_json(job_spec)}
    };
}

std::string PlannerAnswer::render_markdown() const {
    std::ostringstream out;
    out << "## Algorithm Recommendation\n\n";
    out << "- Algorithm: " << algorithm_name << " (`" << algorithm_id << "`)\n";
    out << "- Recommendation: " << recommendation << "\n";
    out << "- Safety: real CUDA/MPI execution is not enabled; this is a dry-run "
        << "planning record only.\n\n";

    out << "## Assumptions\n\n";
    for (const auto& assumption : assumptions) {
        out << "- " << assumption << "\n";
    }
    out << "\n";

    out << "## Parameter Plan\n\n";
    out << "| Parameter | Value | Rationale |\n";
    out << "| --- | --- | --- |\n";
    for (const auto& parameter : parameters) {
        out << "| " << parameter.name
            << " | " << parameter.value
            << " | " << parameter.rationale << " |\n";
    }
    out << "\n";

    out << "## Risk Analysis\n\n";
    for (const auto& risk : risks) {
        out << "- " << risk.name << ": " << risk.evidence
            << " Mitigation: " << risk.mitigation << "\n";
    }
    out << "\n";

    out << "## Next Steps\n\n";
    for (const auto& step : next_steps) {
        out << "- " << step << "\n";
    }
    out << "\n";

    out << "## ExperimentSpec\n\n";
    out << "```json\n" << experiment_spec_json(experiment_spec).dump(2)
        << "\n```\n\n";

    out << "## Dry-run JobSpec\n\n";
    out << "```yaml\n" << dry_run_job_text << "```\n\n";

    out << "## Reproducible Experiment Record\n\n";
    out << "```json\n" << experiment_record_json().dump(2) << "\n```\n";

    return out.str();
}

}  // namespace agent_rpc::research
