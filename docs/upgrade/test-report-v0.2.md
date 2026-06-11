# v0.2 Test Report

Date: 2026-06-11

Scope:

- Code Agent MVP.
- AlgorithmCard model, AlgorithmRegistry, and seed cards.
- Local read-only algorithm listing helper.
- ExperimentSpec, JobSpec, and DryRunBackend.
- Experiment Planner Agent skeleton.
- v0.2 demo and upgrade documentation.

Safety boundaries verified by implementation and docs:

- No real CUDA/MPI execution is enabled.
- No SSH, Slurm, PBS, or remote-server backend is connected.
- No arbitrary shell command execution is wired from user input.
- Code Agent is read-only and only proposes patches.
- DryRunBackend renders previews and includes `dry_run: true`; it does not
  submit jobs.

## Commands

Final verification commands:

```bash
cmake --build build -j2
ctest --test-dir build --output-on-failure
bash -n examples/ai_orchestrator/start_system.sh
bash -n examples/ai_orchestrator/stop_system.sh
bash -n deploy/scripts/start.sh
git diff --check
```

Results will be recorded in `docs/upgrade/upgrade-log.md` for the final v0.2
documentation commit.

Final result:

- PASS. `cmake --build build -j2` exited 0.
- PASS. `ctest --test-dir build --output-on-failure` passed 20/20 tests.
- PASS. `bash -n examples/ai_orchestrator/start_system.sh` exited 0.
- PASS. `bash -n examples/ai_orchestrator/stop_system.sh` exited 0.
- PASS. `bash -n deploy/scripts/start.sh` exited 0.
- PASS. `git diff --check` produced no output before the final result log was
  written; it is rerun before commit.

## Coverage Summary

- `CodeAgentRegistrationTest`: verifies Code Agent tag, skills, tool-calling
  capability, and AgentCard serialization contract.
- `CodeAgentToolsTest`: verifies read-only list/read/search helpers reject
  unsafe paths and do not execute shell commands.
- `AlgorithmCardTest`: verifies JSON parsing, required fields, and rejection of
  non-`dry_run` backends.
- `AlgorithmRegistryTest`: verifies seed-card loading, filtering, invalid-card
  rejection, and the read-only listing helper shape.
- `ExperimentSpecTest` and `DryRunBackendTest`: verify spec validation and
  dry-run rendering.
- `ExperimentPlannerRegistrationTest` and `ExperimentPlannerExecutableTargetTest`:
  verify planner registration metadata and build target availability.

## 知识点总结（学习与面试复盘）

这一版 v0.2 的核心不是“又接了一个聊天机器人”，而是把项目往科研计算
Agent 平台推进了一步：有可路由的 Code Agent，有算法元数据模型，有实验
规格和作业规格，有只渲染不执行的 dry-run 后端，也有一个可以继续增强的
Experiment Planner Agent skeleton。下面按面试和学习复盘的方式拆开。

### 1. 整体架构：从聊天 Demo 到科研计算 Agent Workbench

要解决的问题：

- 原项目已经有 gRPC、A2A、MCP、RAG、Redis memory、Web/CLI，但项目叙事更像
  RPC/多 Agent demo。
- v0.2 要把它收敛成一个 FWI-first 的研究计算助手平台，让面试官能快速看出
  “这是一个有产品边界和工程边界的平台”，不是散装功能。

本版形成的架构层次：

- Client 层：CLI client、Web UI、gRPC client、HTTP bridge。
- Orchestrator 层：识别 intent，按固定路由或 Agent-RAG 选择 Agent。
- Agent 层：Math、FWI Theory、FWI Teaching、General Research、Code Agent、
  Experiment Planner Agent。
- Tool 层：MCP 工具发现与调用，后续可扩展实验工具。
- Knowledge 层：本地 Markdown/JSON 知识、AlgorithmCard 元数据。
- Research Planning 层：ExperimentSpec、JobSpec、DryRunBackend。

面试可以这样说：

> 我把一个多 Agent demo 重构叙事成科研计算 workbench。它不是只把用户问题
> 丢给 LLM，而是把用户入口、路由、Agent 能力描述、工具、知识元数据、实验
> 规格和执行后端边界分层。这样后续加真实集群执行时，不会直接把危险能力
> 塞进聊天接口，而是从 dry-run backend 逐步演进。

关键边界：

- v0.2 不接真实 CUDA/MPI。
- 不接 SSH、Slurm、PBS 或远程服务器。
- 不从用户输入执行任意 shell 命令。
- Code Agent 只读，只能解释代码和给 patch 建议，不能自动改代码。

### 2. AgentCard 和路由契约：Agent 不是靠名字猜，而是靠元数据被发现

要解决的问题：

- Orchestrator 里已有 `code` intent 分支，但以前没有真正的 Code Agent，
  code 问题会 fallback 到 general handler。
- 多 Agent 系统如果只靠 prompt 或硬编码名字，后续会很难维护。Agent 应该
  用稳定的 metadata 描述自己的能力。

v0.2 的做法：

- Code Agent 注册时带上 `code`、`engineering`、`debugging` tags。
- skills 包括 `code_navigation`、`error_diagnosis`、`patch_proposal`。
- capabilities 明确声明 tool-calling 等能力。
- `AgentRegistration::build_agent_card()` 把这些信息序列化成 AgentCard。
- Orchestrator 的 `call_code_agent(...)` 通过
  `call_agent_by_tag("code", ...)` 在 registry 里选 Agent。

关键文件和测试：

- `examples/ai_orchestrator/orchestrator_main.cpp`
- `examples/ai_orchestrator/code_agent_main.cpp`
- `tests/test_code_agent_registration.cpp`

学习点：

- AgentCard 类似“服务发现 + 能力声明”的契约。
- tags 负责粗粒度路由，skills 负责告诉系统和人这个 Agent 能做什么。
- 测试 registration metadata 很重要，因为路由失败往往不是编译错误，而是
  metadata 写错导致运行时找不到合适 Agent。

面试可以这样说：

> 我没有只在 Orchestrator 里硬编码一个 URL，而是让 Code Agent 以 AgentCard
> 的方式注册：包含 tag、skill、capability。然后用测试锁住这个契约，确保
> `code` intent 能找到带 `code` tag 的 Agent。这体现的是多 Agent 系统里
> “能力发现”和“路由契约”的设计。

### 3. Code Agent 安全设计：能读仓库，但不能执行用户命令

要解决的问题：

- Code Agent 如果直接接 shell，会非常危险：用户输入可能变成任意命令执行。
- 但如果完全不给上下文，Code Agent 又无法回答“代码在哪里”“逻辑怎么走”。

v0.2 的做法：

- 提供三个窄能力的只读工具：
  - list files：列出仓库内安全路径。
  - read file：读取仓库内文件。
  - search text：在仓库内做文本搜索，返回 path/line/text。
- 工具实现拒绝绝对路径和逃逸出 project root 的路径。
- Code Agent prompt 明确要求：
  - 不声称自己改了文件。
  - 不执行命令。
  - patch 只能作为建议输出。
  - 给出风险和验证方式。

关键文件和测试：

- `examples/ai_orchestrator/code_agent_tools.hpp`
- `examples/ai_orchestrator/code_agent_tools.cpp`
- `examples/ai_orchestrator/code_agent_main.cpp`
- `tests/test_code_agent_tools.cpp`

学习点：

- 安全 Agent 的核心不是“相信模型会守规矩”，而是把工具能力收窄。
- 只读工具比通用 shell 安全得多，也更容易测试。
- 路径安全要防 path traversal，比如 `../`、绝对路径、符号链接等越界风险。

面试可以这样说：

> Code Agent 的设计重点是最小权限。它需要代码上下文，但我没有给它 shell。
> 我只给了 list/read/search 三个只读工具，并在工具层做 project root 边界
> 校验。这样模型可以解释代码和提出 patch，但没有执行命令和自动改文件的
> 能力。

### 4. AlgorithmCard：把科研算法从 prompt 变成数据模型

要解决的问题：

- 如果 FWI、频率外推、叠后反演等算法全写在 prompt 里，后续新增算法就要改
  Orchestrator 或 Agent prompt，扩展性差。
- 科研平台需要让算法有结构化 metadata：参数、输入、输出、失败模式、执行
  后端边界。

v0.2 的做法：

- 新增 `AlgorithmCard` C++ 模型，支持 JSON parse/serialize/validate。
- 新增 `AlgorithmRegistry`，从 `resources/algorithms/*.json` 加载算法卡片。
- seed cards 包括：
  - `fwi-cuda-mpi`
  - `frequency-extrapolation`
  - `poststack-inversion`
- 所有卡片的 execution backend 都必须是 `dry_run`。
- 新增 local listing helper，把 registry 内容以稳定 JSON summary 暴露给
  Agent 或未来 MCP tool。

关键文件和测试：

- `research/include/agent_rpc/research/algorithm_card.h`
- `research/src/algorithm_card.cpp`
- `research/include/agent_rpc/research/algorithm_registry.h`
- `research/src/algorithm_registry.cpp`
- `research/include/agent_rpc/research/algorithm_listing_tool.h`
- `resources/algorithms/*.json`
- `tests/test_algorithm_card.cpp`
- `tests/test_algorithm_registry.cpp`

学习点：

- 这是“配置/数据驱动架构”：新增算法优先加 JSON，而不是改 C++ 路由逻辑。
- `failure_modes` 很重要，因为科研助手不只推荐算法，还要解释风险，比如
  cycle skipping、unstable gradient、noise amplification。
- `backend == "dry_run"` 是安全边界，防止 v0.2 意外进入真实执行。

面试可以这样说：

> 我把算法知识抽象成 AlgorithmCard，里面有 domain、tags、parameters、
> inputs、outputs、failure_modes 和 execution backend。这样系统新增一个
> 算法只需要加一张 JSON 卡片，Registry 会加载并验证它。这个设计把算法
> 扩展从代码改动变成数据扩展，也为后续检索和规划做准备。

### 5. ExperimentSpec、JobSpec、DryRunBackend：把“计划”和“执行”分开

要解决的问题：

- 用户会问“帮我跑一个 Marmousi 多尺度 FWI 实验”，但 v0.2 不能真正提交作业。
- 如果 Agent 直接生成命令并执行，风险很高，也不利于审计和复现。

v0.2 的做法：

- `ExperimentSpec` 描述科研实验意图：
  - algorithm_id
  - dataset_id
  - parameters
  - resources
  - expected_outputs
- `JobSpec` 描述未来可能执行的作业形状：
  - command
  - working_dir
  - env
  - mpi_processes
  - gpu_count
  - time_limit_minutes
  - artifact_paths
- `DryRunBackend` 只做三件事：
  - validate：检查 JobSpec 是否完整。
  - render：渲染 dry-run 文本。
  - explain：解释这个 dry-run 的含义。
- render 结果必须包含 `dry_run: true`。

关键文件和测试：

- `research/include/agent_rpc/research/experiment_spec.h`
- `research/include/agent_rpc/research/job_spec.h`
- `research/include/agent_rpc/research/job_backend.h`
- `research/src/experiment_spec.cpp`
- `research/src/job_spec.cpp`
- `research/src/dry_run_backend.cpp`
- `tests/test_experiment_spec.cpp`

学习点：

- `ExperimentSpec` 是“我要做什么实验”，`JobSpec` 是“如果未来要运行，作业长
  什么样”。
- `DryRunBackend` 是安全替身：让用户看到计划和命令预览，但不会调用系统执行。
- 这种分层方便以后加 Slurm/PBS/SSH backend：只要实现同一类 JobBackend 接口，
  同时保留审计和权限控制。

面试可以这样说：

> 我把实验规划和作业执行拆开。ExperimentSpec 表达科研意图，JobSpec 表达
> 作业形状，DryRunBackend 只渲染预览并强制 `dry_run: true`。这让系统可以
> 先做安全的实验规划和复现记录，等权限、隔离、日志、审计都准备好后，再接
> 真实集群 backend。

### 6. Experiment Planner Agent skeleton：先接入 Agent，再逐步增强可靠性

要解决的问题：

- v0.2 需要有实验规划 Agent，但不能过早承诺“它已经能稳定生成完整实验方案”。
- Planner 需要能看到 AlgorithmCard metadata，并且在执行相关回答里坚持 dry-run。

v0.2 的做法：

- 新增 `ai_experiment_planner_agent` 可执行文件。
- 注册 tags：
  - `experiment`
  - `planning`
  - `research-computing`
  - `fwi`
- skills 包括：
  - experiment planning
  - parameter advice
  - dry-run job
- prompt 注入 local AlgorithmCard listing。
- startup scripts 在本地 `5011` 端口启动 planner。
- demo 中明确 planner smoke test 直连 `http://localhost:5011`，因为当前固定
  Orchestrator intent 还只有 `math/code/general/fwi`。

关键文件和测试：

- `examples/ai_orchestrator/experiment_planner_agent_main.cpp`
- `examples/ai_orchestrator/start_system.sh`
- `deploy/scripts/start.sh`
- `tests/test_experiment_planner_registration.cpp`

学习点：

- skeleton 的价值是把 Agent 生命周期、注册、启动、prompt 上下文、安全边界
  先接起来。
- 它还不是最终 planner：结构化知识检索、确定性 spec generation、风险分析
  模板、可复现实验记录都应该放到 v0.3/v0.4。
- 文档必须如实描述能力边界，不能把 skeleton 包装成完全可靠的实验规划系统。

面试可以这样说：

> 我先实现了 Experiment Planner Agent skeleton，让它能注册、启动、读取
> AlgorithmCard 上下文，并保持 dry-run-only。这个阶段我没有过度承诺 LLM
> 输出稳定性，而是把 deterministic 的模型、registry、dry-run backend 和测试
> 先打牢，后续再做 structured knowledge retrieval 和可复现实验记录。

### 7. 测试策略：先锁 deterministic 部分，再做 Agent smoke

要解决的问题：

- LLM 输出不稳定，不能把全部质量都压在端到端聊天测试上。
- C++ 项目升级容易因为 CMake target、include path、链接关系破坏构建。

v0.2 的测试重点：

- Registration contract tests：锁住 Code Agent 和 Planner Agent 的 metadata。
- Tool tests：锁住 Code Agent 只读工具的路径安全和搜索行为。
- Model tests：锁住 AlgorithmCard、ExperimentSpec、JobSpec 的 validation。
- Registry tests：锁住 JSON seed loading、过滤、非法 backend 拒绝。
- Backend tests：锁住 DryRunBackend 输出必须包含 `dry_run: true`。
- Executable target tests：确认 CMake 产物存在。
- Full CTest：确认新增模块没有破坏已有 RPC/A2A/MCP/RAG 测试。

验证命令习惯：

- 代码改动后跑 `cmake --build build -j2`。
- 跑相关 focused CTest。
- 最后跑 `ctest --test-dir build --output-on-failure`。
- 改启动脚本后跑 `bash -n <script>`。
- 提交前跑 `git diff --check`。

学习点：

- 对 Agent 系统来说，最好优先测试“非 LLM 的确定性边界”：注册元数据、工具
  安全、数据模型、后端行为。
- LLM smoke test 适合验证演示路径，但不适合作为唯一质量保证。
- upgrade log 记录每次验证命令，可以让项目演进过程在面试时有证据。

面试可以这样说：

> 我没有只做一个端到端聊天 demo，而是把 deterministic 的部分拆出来测：
> Agent registration、只读工具、AlgorithmCard validation、Registry loading、
> DryRunBackend rendering。LLM 相关部分用 smoke test 和文档说明边界。这种
> 测试策略能让多 Agent 项目在持续扩展时不容易退化。

### 8. 安全边界：为什么 v0.2 明确不接真实 CUDA/MPI 和集群

要解决的问题：

- 科研计算平台最终可能要接 CUDA/MPI、Slurm/PBS、SSH 或实验室服务器。
- 但如果在 Agent 还没有权限、隔离、审计、日志收集之前就接真实执行，会有
  很大的安全和运维风险。

v0.2 的选择：

- 只做 dry-run。
- 只渲染命令/作业预览，不提交作业。
- 不连接远程服务器。
- 不让用户输入变成 shell 命令。
- 文档和 prompt 都反复声明这些边界。

学习点：

- 安全边界不是“功能没做完”的借口，而是工程分期的一部分。
- 先有 spec、dry-run、日志、测试、demo，再加真实 backend，风险更可控。
- 面试时这能体现你不是只追功能，而是在考虑真实实验室使用场景。

面试可以这样说：

> 我故意没有在 v0.2 接真实 CUDA/MPI 或 Slurm。因为真实执行需要认证、授权、
> 工作目录隔离、日志和 artifact 收集、取消任务、审计等能力。v0.2 先把
> JobSpec 和 DryRunBackend 做出来，相当于为后续真实 backend 留接口，同时
> 避免早期系统误执行危险命令。

### 9. 后续路线：为什么下一步是 v0.3 Research Knowledge Base

v0.2 已经解决：

- Agent 能力入口：Code Agent 和 Planner Agent skeleton。
- 算法元数据：AlgorithmCard/Registry。
- 实验和作业形状：ExperimentSpec/JobSpec。
- 安全执行替身：DryRunBackend。
- 演示与测试报告：demo script、test report、upgrade log。

下一步 v0.3 应该解决：

- 用 `PaperNote` 记录论文和方法主张。
- 用 `AlgorithmNote` 记录算法假设、适用场景、参数建议。
- 用 `ExperimentNote` 记录历史实验、数据集、结果和结论。
- 用 `FailureCase` 记录 cycle skipping、loss 不下降、梯度不稳定等症状和诊断。
- 让 Planner 的参数建议有可检索依据，而不是只靠通用 LLM 常识。

面试可以这样说：

> v0.2 打的是平台骨架和安全边界，v0.3 要补知识地基。因为科研 Agent 最重要的
> 不是会聊天，而是能解释“为什么推荐这个参数、这个算法适合什么条件、失败时
> 应该怎么诊断”。所以我下一步会把 paper、algorithm、experiment、failure case
> 都结构化，给 planner 提供可追溯的本地知识依据。
