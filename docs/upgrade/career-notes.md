# Career Notes

This file records architecture and technical talking points for resumes,
interviews, project reports, and lab demos. Keep it factual. Do not claim
features that are only planned.

Update this file when an upgrade changes architecture, adds a major component,
adds tests, improves deployment, or changes the product story.

## One-Line Project Pitch

FWI-first research computing multi-agent workbench built with C++, gRPC, A2A,
MCP, RAG, Redis memory, and a Web/CLI interface.

Current status:

- Multi-agent communication framework and FWI research assistant prototype.
- Supports gRPC/A2A communication, agent registry, MCP tool integration,
  Agent-RAG routing, Tool-RAG, Redis-backed conversation memory, local knowledge
  retrieval, and Web UI.
- Includes a Code Agent MVP executable for read-only code Q&A, error diagnosis,
  project inspection, and patch proposal prompts; automatic patch application
  is not enabled.
- Includes an initial `AlgorithmCard` C++ research model for JSON-backed
  algorithm metadata, registry loading, seed cards, and dry-run backend
  validation.
- Includes `ExperimentSpec`, `JobSpec`, and `DryRunBackend` models for safe
  experiment planning without submitting jobs.
- Includes a v0.3 `ResearchKnowledgeNote` and `ResearchKnowledgeBase` for
  JSON-backed paper, algorithm, experiment, and failure-case notes with
  deterministic local retrieval by method, failure mode, parameter advice, and
  dataset.
- Includes an initial v0.4 `PlannerContext` layer that deterministically
  combines AlgorithmCards, ResearchKnowledge notes, failure-mode evidence, and
  parameter advice before the Experiment Planner calls an LLM.
- Includes a v0.4 `PlannerAnswer` layer that turns PlannerContext into
  structured algorithm recommendations, assumptions, parameter tables, risk
  analysis, ExperimentSpec JSON, dry-run JobSpec text, and reproducible
  experiment records.
- Includes a v0.5 Lab Agent Workbench UI that exposes sessions, AlgorithmCards,
  experiment history, route trace, tool calls, parameter tables, ExperimentSpec,
  JobSpec, dry-run state, and service status panels in the browser.
- Includes a v0.6 Lab Code Adapter for local config-template loading, dry-run
  config previews, supplied log parsing, loss curve extraction, common failure
  recognition, and Planner-facing diagnostic summaries.
- Includes an initial `JobBackend` interface reservation so future execution
  backends can share the same validate/render/explain contract while the only
  implemented backend remains `DryRunBackend`.
- Includes v0.2 demo and test-report documentation for FWI Q&A, Code Agent
  routing, and dry-run Experiment Planner smoke testing.
- Real CUDA/MPI or cluster execution is not enabled yet.

## Architecture Talking Points

Current architecture:

- Client layer: CLI client, Web UI, gRPC client, HTTP bridge.
- Service layer: gRPC server and AIQueryService.
- Protocol adapter layer: A2A adapter converts RPC requests into A2A JSON-RPC
  messages.
- Orchestration layer: AI Orchestrator routes requests to specialized agents.
- Agent layer: Math, FWI Theory, FWI Teaching, General Research, Code Agent,
  and Experiment Planner Agent.
- Tool layer: MCP integrated server and plugins such as calculator and FWI
  metadata tools.
- Retrieval layer: Agent-RAG for dynamic agent selection, Tool-RAG for tool
  selection, local FWI knowledge retrieval, and structured v0.3 research
  knowledge retrieval by note type, method, failure mode, parameter advice, and
  dataset.
- Planner context layer: v0.4 deterministic request inference and context
  construction select AlgorithmCards and local research notes for the Planner
  prompt before LLM generation.
- Memory layer: Redis-backed session history, agent memory, and task state.

Current v0.2 state:

- Lab Agent MVP scope is complete: Code Agent, AlgorithmCard registry,
  ExperimentSpec, JobSpec, DryRunBackend, Experiment Planner skeleton, demo
  script, and test report.

Current v0.3 state:

- Research Knowledge Base is complete: typed local JSON notes under
  `resources/research_knowledge`, deterministic C++ loading, validation, and
  tests for method, failure-mode, parameter-advice, and dataset retrieval.

Current v0.4 state:

- Experiment Planner is complete for the v0.4 scope: `PlannerContext` infers
  FWI planning signals from a request, retrieves matching AlgorithmCards and
  knowledge notes, and `PlannerAnswer` creates a structured dry-run plan and
  reproducible experiment record.

Current v0.5 state:

- Lab Workbench UI is complete for the v0.5 scope: the browser surface keeps
  chat interaction but adds research-workbench panels for AlgorithmCards,
  experiment history, route/tool inspection, parameter planning,
  ExperimentSpec/JobSpec rendering, dry-run boundaries, and local service
  status.

Current v0.6 state:

- Lab Code Adapter is complete for the v0.6 scope: deterministic local parsing
  covers config templates, dry-run config previews, supplied log text, loss
  curves, common failure recognizers, and Planner-facing summaries. No real
  execution backend was added.

Current JobBackend reservation state:

- A `JobBackend` interface now defines the future backend contract through
  `validate`, `render`, and `explain`; `DryRunBackend` implements that contract
  and remains the only concrete backend. Non-dry-run backend enum values and
  runtime rejection are still planned before any server execution milestone.

## Technical Highlights

- C++17/C++20 multi-module project with CMake.
- gRPC and Protocol Buffers for service APIs.
- A2A-style HTTP JSON-RPC for agent-to-agent messaging.
- MCP client/server integration with tool discovery, tool schema, sync/async
  calls, and RAG-based tool retrieval.
- Redis-backed task and conversation memory.
- Local and API-based embedding support.
- JSON-backed local research knowledge notes for paper, algorithm, experiment,
  and failure-case guidance.
- Deterministic planner grounding that turns a user request into selected
  AlgorithmCards, local knowledge notes, parameter advice, and explicit
  dry-run safety boundaries before LLM generation.
- Structured dry-run experiment planning that produces parameter tables, risk
  analysis, ExperimentSpec JSON, dry-run JobSpec previews, and versioned
  experiment records without executing jobs.
- Static browser workbench that renders planner artifacts into inspectable
  panels and keeps execution state visible as `dry_run: true`.
- Deterministic lab-code adapter that converts config templates and supplied
  FWI log text into structured dry-run diagnostics, loss curves, failure
  findings, and Planner-facing summaries.
- Reserved a C++ `JobBackend` abstraction for future execution backends while
  preserving dry-run-only behavior through the existing `DryRunBackend`.
- Property and integration tests with GoogleTest and RapidCheck.
- Web UI with HTTP and gRPC bridge modes.

## Resume Bullets

Use only bullets that match the completed implementation.

- Built a C++ multi-agent communication framework using gRPC, Protocol Buffers,
  A2A-style JSON-RPC, and Redis-backed task memory.
- Implemented agent registry and dynamic routing with AgentCard metadata,
  skills, tags, and embedding-based Agent-RAG retrieval.
- Integrated MCP tool calling with schema-based tool discovery, retry handling,
  and RAG-based tool selection.
- Developed an FWI research assistant prototype with local knowledge retrieval,
  specialized FWI agents, and metadata tools for velocity models and datasets.
- Added automated test coverage across RPC serialization, A2A adapters,
  registry behavior, routing, MCP integration, and RAG properties.
- Added a read-only Code Agent MVP executable for code Q&A, error diagnosis,
  repository list/read/search context, and patch proposal prompts, with local
  startup script integration.
- Added the first research-domain C++ model, `AlgorithmCard`, with JSON
  serialization and validation that rejects non-dry-run backends in v0.2.
- Added an `AlgorithmRegistry` that loads algorithm metadata from JSON seed
  cards and supports deterministic lookup/filtering without Orchestrator
  changes.
- Added a local algorithm listing helper that exposes registry contents as a
  read-only JSON summary for future agent or MCP tool use.
- Added `ExperimentSpec`, `JobSpec`, and `DryRunBackend` abstractions with tests
  for dry-run rendering and validation.
- Added an Experiment Planner Agent skeleton that registers as a planning
  specialist and grounds prompts in local AlgorithmCards while preserving
  dry-run-only execution boundaries.
- Added a structured research knowledge base with typed JSON notes and tested
  retrieval by method, failure mode, parameter advice, and dataset for FWI
  planning.
- Added a deterministic PlannerContext layer that grounds Experiment Planner
  prompts in AlgorithmCards, local research knowledge, failure-case notes, and
  parameter advice while preserving dry-run-only execution boundaries.
- Added a PlannerAnswer layer that converts grounded planner context into
  structured dry-run experiment plans, including ExperimentSpec, JobSpec, risk
  analysis, and reproducible records.
- Renamed the Web UI brand to Lab Agent Workbench and added a CTest guard for
  the static UI and server branding text.
- Upgraded the Web UI into a Lab Agent Workbench that renders route traces,
  tool calls, AlgorithmCards, parameter tables, ExperimentSpec, JobSpec,
  dry-run state, experiment history, and service status panels.
- Added a Lab Code Adapter for reading lab-style config templates, rendering
  dry-run config previews, parsing supplied logs, extracting loss curves, and
  recognizing common FWI failure patterns without job submission.
- Reserved the future `JobBackend` interface and made `DryRunBackend`
  polymorphic through that interface without adding any real execution backend.

Planned after v0.6:

- Finish hardening the future backend boundary with explicit backend type enum
  values and runtime rejection for all non-`dry_run` choices until server
  execution has an explicit safety design.

Move planned bullets into completed bullets only after implementation and tests
are committed.

## Interview Explanation: Why This Is Not Just A Chatbot

This project separates communication, orchestration, tools, knowledge, and
experiment planning:

- Chat is only one interface.
- Agents are registered with skills and tags.
- The Orchestrator can route by fixed intent or Agent-RAG.
- Tools are discovered through MCP and selected through Tool-RAG.
- Research algorithms will be represented as AlgorithmCards instead of hardcoded
  prompts.
- Real execution is intentionally behind a backend interface so CUDA/MPI and
  cluster jobs can be added safely later.

## Upgrade Notes

Add one short entry whenever a meaningful technical change lands.

### 2026-06-11: Upgrade Planning

- Added upgrade workflow, milestone board, v0.2 implementation plan, and version
  roadmap.
- Current next engineering target is Code Agent MVP.

### 2026-06-11: README Product Positioning

- Reframed the README first screen as a Lab Research Agent Platform rather than
  only an RPC framework.
- Documented the product layers and current safety boundaries for recruiter,
  lab user, and demo audiences.

### 2026-06-11: Code Agent Registration Contract

- Added a GoogleTest contract for Code Agent registration metadata, including
  the `code` tag, code-oriented skills, tool-calling capability, and AgentCard
  serialization expectations.

### 2026-06-11: Code Agent Executable

- Added the `ai_code_agent` executable, Code Agent startup integration, and a
  CTest executable-target contract.
- The Code Agent is prompt-only and read-only in this step; repository
  list/read/search tools remain the next Code Agent milestone.

### 2026-06-11: Code Agent Read-Only Inspection Tools

- Added C++ read-only project inspection helpers for file listing, safe file
  reading, and text search inside the project root.
- Wired Code Agent prompts to include deterministic project context while still
  preventing shell execution and automatic patch application.

### 2026-06-11: Code Agent Smoke Test Docs

- Added a documented smoke-test path for verifying that code intent routes to
  the read-only Code Agent and identifies the Orchestrator routing logic.

### 2026-06-11: Quick Demo Command Map

- Added recruiter- and demo-friendly README commands for HTTP, gRPC bridge, Web
  UI, and local embedding entry points while preserving localhost-only safety
  boundaries.

### 2026-06-11: AlgorithmCard Model

- Added the `agent_rpc_research` library and an `AlgorithmCard` model for
  JSON-backed lab algorithm metadata, including validation that keeps execution
  constrained to `dry_run` in v0.2.

### 2026-06-11: AlgorithmRegistry And Seed Cards

- Added file-based AlgorithmCard loading from `resources/algorithms/*.json`,
  seed cards for FWI, frequency extrapolation, and post-stack inversion, plus
  tests for loading, filtering, and invalid backend rejection.

### 2026-06-11: Algorithm Listing Tool Entry

- Added a deterministic local listing helper for AlgorithmRegistry summaries,
  preserving a read-only metadata boundary before any MCP exposure.

### 2026-06-11: ExperimentSpec, JobSpec, And DryRunBackend

- Added structured experiment and job models plus a dry-run backend that renders
  command previews with `dry_run: true` without executing anything.

### 2026-06-11: Experiment Planner Agent Skeleton

- Added an Experiment Planner Agent executable and startup integration with
  planning/research-computing registration tags and AlgorithmCard prompt
  context.

### 2026-06-11: v0.2 Demo And Test Report

- Added a v0.2 demo script that separates Orchestrator demos from the direct
  Experiment Planner Agent dry-run smoke test.
- Added a v0.2 test report and knowledge summary covering routing contracts,
  research metadata modeling, dry-run planning boundaries, and verification
  practice.

### 2026-06-12: Research Knowledge Base Skeleton

- Added JSON-backed `ResearchKnowledgeNote` and `ResearchKnowledgeBase` C++
  models for typed paper, algorithm, experiment, and failure-case notes.
- Added deterministic retrieval tests for note type, method, failure mode, and
  parameter advice without enabling any real execution backend.

### 2026-06-12: AWI And Gradient Knowledge Notes

- Added structured AWI and adjoint-state gradient notes for cycle-skipping
  diagnosis, misfit-function choice, and gradient-check advice.
- Extended deterministic knowledge tests so v0.3 content coverage is protected
  by method, failure-mode, and parameter-advice retrieval assertions.

### 2026-06-12: v0.3 Research Knowledge Completion

- Added dataset-based research knowledge retrieval and marked v0.3 complete.
- Added a v0.3 test report with Chinese learning and interview-prep summary.

### 2026-06-12: v0.4 PlannerContext Retrieval

- Added deterministic PlannerContext retrieval for the Experiment Planner,
  combining AlgorithmCards, structured research notes, failure-mode evidence,
  and parameter advice before LLM prompting.
- Preserved the dry-run-only boundary: the Planner context explicitly marks
  real execution disabled and forbids CUDA/MPI, SSH, Slurm/PBS, remote jobs, and
  shell execution.

### 2026-06-12: v0.4 Experiment Planner Completion

- Added PlannerAnswer generation for structured algorithm recommendations,
  assumptions, parameter tables, risk analysis, next steps, ExperimentSpec,
  dry-run JobSpec, and reproducible experiment records.
- Updated the Experiment Planner Agent prompt path so deterministic structured
  scaffolds are available before LLM generation.

### 2026-06-12: Lab Agent Workbench Branding

- Renamed the static Web UI title, sidebar, welcome state, footer, and local
  server banner to Lab Agent Workbench.
- Added a CTest branding guard so future UI work does not regress to generic
  orchestrator-chat wording.

### 2026-06-12: v0.5 Lab Workbench UI Completion

- Added a browser-side research workbench layout with AlgorithmCards,
  experiment history, route trace, tool calls, selected AlgorithmCard,
  parameter table, ExperimentSpec, JobSpec, and service status panels.
- Added static parsing helpers for ExperimentSpec JSON blocks and dry-run
  JobSpec text blocks, preserving a preview-only boundary.
- Added a v0.5 test report and Chinese learning summary for product story,
  implementation details, verification evidence, safety boundaries, and
  interview preparation.

### 2026-06-22: v0.6 Lab Code Adapter Plan

- Added a v0.6 implementation plan for config template reading, safe config
  previews, log parsing, loss curve parsing, and deterministic failure
  recognition.
- Kept the career story explicit that v0.6 is planned, not implemented: no real
  CUDA/MPI execution, SSH, Slurm/PBS, remote execution, shell execution, or
  automatic Code Agent patch application was added.

### 2026-06-22: v0.6 Lab Code Adapter Completion

- Added the deterministic `lab_code_adapter` research component for config
  templates, dry-run config previews, supplied log parsing, loss curve
  extraction, common failure findings, and Planner-facing diagnostic summaries.
- Added fixture-backed tests for execution-field rejection, loss parsing,
  stagnation, NaN/Inf, cycle-skipping risk, resource-limit recognition, and
  dry-run safety boundary summaries.
- Preserved the execution boundary: no real CUDA/MPI execution, SSH, Slurm/PBS,
  remote execution, arbitrary shell execution, or automatic Code Agent patch
  application was added.

### 2026-06-22: JobBackend Interface Reservation

- Added a C++ `JobBackend` abstraction with `validate`, `render`, and `explain`
  methods for future execution-backend substitution.
- Made `DryRunBackend` implement the interface and added a contract test that
  exercises dry-run rendering through `const JobBackend&`.
- Preserved the execution boundary: no real CUDA/MPI execution, SSH, Slurm/PBS,
  remote execution, arbitrary shell execution, or automatic Code Agent patch
  application was added.
