# Version Roadmap

This file records the long-term version direction. It is safe to commit because
it contains product goals and handoff rules, not personal copy-paste prompts.

Personal prompts should stay in ignored local files such as
`docs/upgrade/local-prompts.md`.

## Version Summary

| Version | Name | Main Outcome |
| --- | --- | --- |
| v0.2 | Lab Agent MVP | Real Code Agent, AlgorithmCard, ExperimentSpec, JobSpec, DryRunBackend, truthful demo docs |
| v0.3 | Research Knowledge Base | PaperNote, AlgorithmNote, ExperimentNote, FailureCase, parameter-advice retrieval |
| v0.4 | Experiment Planner | Structured experiment planning, risk analysis, dry-run jobs, reproducible experiment records |
| v0.5 | Lab Workbench UI | Web workbench with routing, tool calls, specs, parameter tables, dry-run jobs, and status panels |
| v0.6 | Lab Code Adapter | Integrate with lab code shape without submitting jobs: config templates, log parsing, loss analysis |
| v0.8 | Server Backend | Add controlled Slurm/PBS/SSH/server execution with auth, isolation, logs, and artifacts |
| v1.0 | Lab-Usable Platform | New lab members can learn, plan, run, monitor, and analyze real research experiments safely |

## v0.2: Lab Agent MVP

Purpose:

- Turn the project from a rough FWI/multi-agent demo into a usable research
  computing agent prototype.

Must have:

- Code Agent MVP.
- AlgorithmCard model and seed cards.
- ExperimentSpec and JobSpec.
- DryRunBackend that never executes real jobs.
- Experiment Planner Agent skeleton.
- README and demo docs that clearly state current limits.

Not included:

- Real CUDA/MPI execution.
- SSH, Slurm, PBS, or remote server execution.
- Automatic code patch application.

Recommended first task:

- Start with Code Agent MVP because the Orchestrator already has a `code`
  intent branch, but no real Code Agent implementation.

## v0.3: Research Knowledge Base

Purpose:

- Make parameter advice grounded in local paper notes, algorithm notes,
  experiment notes, and failure cases.

Must have:

- `PaperNote` for papers and method claims.
- `AlgorithmNote` for assumptions, parameters, and applicable scenarios.
- `ExperimentNote` for historical experiments and results.
- `FailureCase` for common symptoms and diagnosis.
- Retrieval by method, failure mode, parameter, and dataset.

Example user value:

- "Low frequency is missing. Should I use multi-scale FWI, AWI, envelope
  inversion, or frequency extrapolation?"

## v0.4: Experiment Planner

Purpose:

- Make the planner useful enough for junior lab members to draft experiments.

Must have:

- Algorithm recommendation.
- Parameter table.
- Assumption list.
- Risk analysis.
- Dry-run JobSpec.
- Next-round tuning suggestions.
- Reproducible experiment record.

Example user value:

- "Plan a Marmousi multi-scale FWI experiment and explain what to adjust if
  loss does not decrease."

## v0.5: Lab Workbench UI

Purpose:

- Make the system look and feel like a research workbench, not a generic chat
  page.

Must have:

- Left panel: sessions, algorithms, experiment history.
- Center panel: conversation and plan.
- Right panel: route trace, tool calls, AlgorithmCard, ExperimentSpec, JobSpec,
  parameter table, and artifacts.
- Status: Orchestrator, Registry, MCP, Embedding, Code Agent, Planner Agent.

Example user value:

- A demo viewer can see how agents reason, which tools were used, and what dry
  run would be executed.

## v0.6: Lab Code Adapter

Purpose:

- Adapt to the shape of the lab's CUDA/MPI FWI code without executing server
  jobs yet.

Must have:

- Config template reader.
- Config generator.
- Log parser.
- Loss curve parser.
- Common error/failure recognizer.
- Parameter tuning suggestion based on logs and knowledge.

Not included:

- Job submission to real servers.

Example user value:

- "Here is my FWI log and config. Why is the loss not decreasing?"

## v0.8: Server Backend

Purpose:

- Connect controlled real execution after the planning and safety boundaries are
  stable.

Must have:

- Backend decision: Slurm, PBS, SSH, or lab wrapper scripts.
- Authentication and authorization.
- Workspace isolation.
- Job submission.
- Job status.
- Cancellation.
- Log collection.
- Artifact collection.
- Audit logging.

Example user value:

- "Submit this approved FWI dry-run plan to the lab queue and monitor it."

## v1.0: Lab-Usable Platform

Purpose:

- Become a serious internal lab tool, not only a portfolio demo.

Must have:

- Newcomer learning workflow.
- Experiment planning workflow.
- Real job submission workflow.
- Monitoring and result analysis workflow.
- Algorithm extension workflow for new lab methods.
- Reproducible experiment records.
- Access control and audit logs.

Product identity:

- Seismic Research Computing Multi-Agent Workbench.

FWI remains the first flagship use case. The platform should support frequency
extrapolation, post-stack algorithms, forward modeling, velocity-modeling tools,
and new lab algorithms through AlgorithmCards and backend adapters.

## How To Decide The Current Version

Use this order:

1. If Code Agent, AlgorithmCard, ExperimentSpec, JobSpec, and DryRunBackend are
   not complete, continue v0.2.
2. If v0.2 is complete but structured paper/algorithm/experiment/failure-case
   notes are missing, start v0.3.
3. If knowledge is structured but planner output is not yet reliable and
   reproducible, start v0.4.
4. If planner works but the UI still looks like chat, start v0.5.
5. If UI works but lab code configs/logs/loss curves are not integrated, start
   v0.6.
6. If lab code adapter works and the lab is ready for controlled execution,
   start v0.8.
7. If real execution is stable, harden toward v1.0.

## Handoff Rule For New Sessions

At the start of a new upgrade session, read these files:

- `docs/upgrade/README.md`
- `docs/upgrade/milestones.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/version-roadmap.md`
- `docs/upgrade/upgrade-log.md`
- the active plan in `docs/superpowers/plans/`

Then continue the first incomplete task for the current version. Validate the
change, update `upgrade-log.md`, update `career-notes.md` when the change adds
architecture or technical talking points, and commit.
