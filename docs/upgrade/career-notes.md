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
- Includes an initial v0.3 `ResearchKnowledgeNote` and `ResearchKnowledgeBase`
  for JSON-backed paper, algorithm, experiment, and failure-case notes with
  deterministic local retrieval.
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
  knowledge retrieval by note type, method, failure mode, and parameter advice.
- Memory layer: Redis-backed session history, agent memory, and task state.

Current v0.2 state:

- Lab Agent MVP scope is complete: Code Agent, AlgorithmCard registry,
  ExperimentSpec, JobSpec, DryRunBackend, Experiment Planner skeleton, demo
  script, and test report.

Current v0.3 state:

- Research Knowledge Base has started with typed local JSON notes under
  `resources/research_knowledge`, deterministic C++ loading, validation, and
  tests for method and failure-mode advice retrieval.

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
  retrieval by method, failure mode, and parameter advice for FWI planning.

Planned after v0.3 start:

- Add remaining structured notes for AWI, adjoint-state gradients, and broader
- Add dataset-based knowledge retrieval to complete the v0.3 roadmap contract.
- Wire structured knowledge retrieval into the Experiment Planner response
  path before hardening planner output quality.

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
