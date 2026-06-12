#pragma once

#include "agent_rpc/research/experiment_spec.h"
#include "agent_rpc/research/job_spec.h"
#include "agent_rpc/research/planner_context.h"

#include <string>
#include <vector>

namespace agent_rpc::research {

struct PlannerParameter {
    std::string name;
    std::string value;
    std::string rationale;
};

struct PlannerRisk {
    std::string name;
    std::string evidence;
    std::string mitigation;
};

struct PlannerAnswer {
    std::string algorithm_id;
    std::string algorithm_name;
    std::string recommendation;
    std::vector<std::string> assumptions;
    std::vector<PlannerParameter> parameters;
    std::vector<PlannerRisk> risks;
    std::vector<std::string> next_steps;
    ExperimentSpec experiment_spec;
    JobSpec job_spec;
    std::string dry_run_job_text;

    json experiment_record_json() const;
    std::string render_markdown() const;
};

PlannerAnswer build_planner_answer(
    const PlannerContextRequest& request,
    const PlannerContext& context);

}  // namespace agent_rpc::research
