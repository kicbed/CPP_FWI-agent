# v0.2 Demo Script

> Historical v0.2 demo only. Its non-execution statements and direct internal
> startup commands describe the 2026-06 version, not the current Deepwave FWI
> implementation. For current one-command startup and a real CPU/single-GPU
> FWI browser test, use [README](../../README.md) and
> [FRONTEND_TEST.md](../FRONTEND_TEST.md).

This script demonstrates the v0.2 Lab Agent MVP on local `localhost` services.
It does not execute real CUDA/MPI jobs, connect SSH, Slurm, PBS, or remote
servers, or apply code patches automatically.

## Setup

Build the project and start the local system:

```bash
cmake --build build -j2
./examples/ai_orchestrator/start_system.sh
```

Open the Orchestrator client:

```bash
./build/examples/ai_orchestrator/ai_client http://localhost:5000
```

In the client, type `n` to start a new conversation before sending each demo
question.

## Demo 1: FWI Knowledge

Ask through the Orchestrator client:

```text
解释 cycle skipping，并说明低频缺失时为什么多尺度反演有帮助。
```

Expected:

- Mentions cycle skipping.
- Mentions the half-cycle criterion or phase mismatch risk.
- Explains why starting from lower frequencies helps multi-scale FWI avoid bad
  local minima.
- Does not claim that an experiment was run.

## Demo 2: Code Agent

Ask through the Orchestrator client:

```text
这个项目里 Orchestrator 的 code intent 路由在哪里？请指出文件和逻辑。
```

Expected:

- Routes to Code Agent when `ai_code_agent` is running.
- References `examples/ai_orchestrator/orchestrator_main.cpp`.
- Explains that `intent == "code"` calls `call_code_agent(...)`, which then
  finds an agent through `call_agent_by_tag("code", ...)`.
- Does not claim that files were changed or commands were executed.

## Demo 3: Experiment Planning Dry Run

The fixed Orchestrator intent router currently handles `math`, `code`,
`general`, and `fwi`. For v0.2, use the Experiment Planner Agent endpoint
directly for the dry-run planning smoke test:

```bash
./build/examples/ai_orchestrator/ai_client http://localhost:5011
```

Ask:

```text
我想在 Marmousi 上跑多尺度 FWI，低频缺失，先给一个 dry-run 实验计划。
```

Expected:

- Uses local AlgorithmCard context and recommends a relevant algorithm such as
  `fwi-cuda-mpi`.
- Gives practical frequency-band, iteration, regularization, and resource
  suggestions.
- Includes assumptions, risks such as cycle skipping, and next-step tuning
  guidance.
- Includes an `ExperimentSpec`-style block.
- Includes a `JobSpec` dry-run block when describing execution.
- Explicitly states `dry_run: true` and that no real CUDA/MPI job was executed.

## Shutdown

Stop the local demo services:

```bash
./examples/ai_orchestrator/stop_system.sh
```
