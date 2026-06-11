#include "agent_rpc/research/algorithm_card.h"

namespace agent_rpc::research {

json AlgorithmCard::to_json() const {
    return {
        {"id", id},
        {"name", name},
        {"domain", domain},
        {"description", description},
        {"tags", tags},
        {"parameters", parameters},
        {"inputs", inputs},
        {"outputs", outputs},
        {"failure_modes", failure_modes},
        {"execution", {
            {"backend", backend},
            {"job_spec_supported", job_spec_supported}
        }}
    };
}

AlgorithmCard AlgorithmCard::from_json(const json& value) {
    AlgorithmCard card;
    card.id = value.value("id", "");
    card.name = value.value("name", "");
    card.domain = value.value("domain", "");
    card.description = value.value("description", "");
    card.tags = value.value("tags", std::vector<std::string>{});
    card.parameters = value.value("parameters", std::vector<std::string>{});
    card.inputs = value.value("inputs", std::vector<std::string>{});
    card.outputs = value.value("outputs", std::vector<std::string>{});
    card.failure_modes = value.value("failure_modes", std::vector<std::string>{});

    if (value.contains("execution")) {
        const auto& execution = value["execution"];
        card.backend = execution.value("backend", "dry_run");
        card.job_spec_supported = execution.value("job_spec_supported", false);
    }

    return card;
}

std::vector<std::string> AlgorithmCard::validate() const {
    std::vector<std::string> errors;

    if (id.empty()) errors.push_back("id is required");
    if (name.empty()) errors.push_back("name is required");
    if (domain.empty()) errors.push_back("domain is required");
    if (parameters.empty()) errors.push_back("parameters must not be empty");
    if (inputs.empty()) errors.push_back("inputs must not be empty");
    if (outputs.empty()) errors.push_back("outputs must not be empty");
    if (backend != "dry_run") {
        errors.push_back("only dry_run backend is enabled in v0.2");
    }

    return errors;
}

bool AlgorithmCard::is_valid() const {
    return validate().empty();
}

}  // namespace agent_rpc::research
