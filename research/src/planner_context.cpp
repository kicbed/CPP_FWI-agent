#include "agent_rpc/research/planner_context.h"

#include <algorithm>
#include <cctype>
#include <set>
#include <sstream>

namespace agent_rpc::research {

namespace {

std::string lower_ascii(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(),
                   [](unsigned char ch) {
                       return static_cast<char>(std::tolower(ch));
                   });
    return value;
}

bool contains_text(const std::string& text, const std::string& needle) {
    return text.find(needle) != std::string::npos;
}

void append_unique_notes(std::vector<ResearchKnowledgeNote>* destination,
                         const std::vector<ResearchKnowledgeNote>& source,
                         std::set<std::string>* seen_ids) {
    for (const auto& note : source) {
        if (seen_ids->insert(note.id).second) {
            destination->push_back(note);
        }
    }
}

json algorithm_summary(const AlgorithmCard& card) {
    return {
        {"id", card.id},
        {"name", card.name},
        {"domain", card.domain},
        {"tags", card.tags},
        {"parameters", card.parameters},
        {"failure_modes", card.failure_modes},
        {"backend", card.backend},
        {"job_spec_supported", card.job_spec_supported}
    };
}

json note_summary(const ResearchKnowledgeNote& note) {
    return {
        {"id", note.id},
        {"title", note.title},
        {"note_type", note.note_type},
        {"summary", note.summary},
        {"methods", note.methods},
        {"datasets", note.datasets},
        {"assumptions", note.assumptions},
        {"parameters", note.parameters},
        {"failure_modes", note.failure_modes},
        {"parameter_advice", note.parameter_advice},
        {"tags", note.tags},
        {"source", note.source}
    };
}

}  // namespace

PlannerContextRequest infer_planner_context_request(
    const std::string& user_request) {
    const auto text = lower_ascii(user_request);

    PlannerContextRequest request;

    const bool mentions_fwi =
        contains_text(text, "fwi") ||
        contains_text(text, "full waveform") ||
        contains_text(text, "多尺度") ||
        contains_text(text, "反演");
    const bool mentions_awi = contains_text(text, "awi");

    if (mentions_fwi || mentions_awi) {
        request.algorithm_tag = "fwi";
    }

    if (mentions_awi) {
        request.method = "awi";
    } else if (mentions_fwi ||
               contains_text(text, "multi-scale") ||
               contains_text(text, "multiscale")) {
        request.method = "multi-scale-fwi";
    }

    if (contains_text(text, "marmousi")) {
        request.dataset = "marmousi";
    } else if (contains_text(text, "field") ||
               contains_text(text, "shot gather")) {
        request.dataset = "field-shot-gather";
    }

    if (contains_text(text, "cycle skipping") ||
        contains_text(text, "cycle_skipping") ||
        contains_text(text, "low frequency") ||
        contains_text(text, "低频")) {
        request.failure_mode = "cycle_skipping";
    }

    if (contains_text(text, "frequency") ||
        contains_text(text, "频率") ||
        contains_text(text, "低频")) {
        request.parameter = "frequency_band";
    } else if (contains_text(text, "gradient") ||
               contains_text(text, "梯度")) {
        request.parameter = "gradient_check";
    }

    return request;
}

PlannerContext build_planner_context(
    const AlgorithmRegistry& algorithms,
    const ResearchKnowledgeBase& knowledge,
    const PlannerContextRequest& request) {
    PlannerContext context;

    if (!request.algorithm_tag.empty()) {
        context.algorithms = algorithms.filter_by_tag(request.algorithm_tag);
    } else {
        context.algorithms = algorithms.cards();
    }

    std::set<std::string> seen_note_ids;
    if (!request.method.empty()) {
        append_unique_notes(&context.notes,
                            knowledge.filter_by_method(request.method),
                            &seen_note_ids);
    }
    if (!request.dataset.empty()) {
        append_unique_notes(&context.notes,
                            knowledge.filter_by_dataset(request.dataset),
                            &seen_note_ids);
    }
    if (!request.failure_mode.empty()) {
        append_unique_notes(&context.notes,
                            knowledge.find_by_failure_mode(request.failure_mode),
                            &seen_note_ids);
    }
    if (!request.method.empty() && !request.parameter.empty()) {
        context.parameter_advice =
            knowledge.parameter_advice_for(request.method, request.parameter);
    }

    return context;
}

json PlannerContext::to_json() const {
    json algorithm_values = json::array();
    for (const auto& algorithm : algorithms) {
        algorithm_values.push_back(algorithm_summary(algorithm));
    }

    json note_values = json::array();
    for (const auto& note : notes) {
        note_values.push_back(note_summary(note));
    }

    return {
        {"dry_run_only", true},
        {"real_execution_enabled", false},
        {"algorithms", algorithm_values},
        {"knowledge_notes", note_values},
        {"parameter_advice", parameter_advice}
    };
}

std::string PlannerContext::render_prompt_context() const {
    std::ostringstream rendered;
    rendered << "dry_run_only: true\n";
    rendered << "real_execution_enabled: false\n";
    rendered << "safety_boundary: Do not execute CUDA/MPI, SSH, Slurm, PBS, "
             << "remote jobs, or shell commands.\n";
    rendered << "context_json:\n" << to_json().dump(2);
    return rendered.str();
}

}  // namespace agent_rpc::research
