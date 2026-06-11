# New Session Prompts

Copy one prompt into a new conversation. These prompts are intentionally
explicit so a fresh agent can continue without guessing.

## Prompt 1: Continue The Next Upgrade Task

```text
你现在在仓库 /root/projects/project/agent-communication-main-v2。

请先阅读这些文件，不要马上改代码：
1. docs/upgrade/README.md
2. docs/upgrade/milestones.md
3. docs/upgrade/upgrade-log.md
4. docs/superpowers/plans/2026-06-11-lab-agent-v0.2.md

然后执行：
1. 运行 git status --short，确认工作区状态。
2. 找到 active plan 里下一个未完成的小任务。
3. 告诉我你准备做哪个任务，以及会跑哪些验证。
4. 实现这个任务，保持改动小而完整。
5. 按 docs/upgrade/README.md 的验证矩阵跑测试。
6. 更新 docs/upgrade/upgrade-log.md，记录任务、测试、结果。
7. git diff --check。
8. 提交到 git，commit message 用 docs/upgrade/README.md 的格式。
9. 最后告诉我改了什么、测试结果、commit hash、下一步建议。

重要限制：
- 不要接真实 CUDA/MPI、Slurm、PBS、SSH 或远程服务器。
- 不要执行来自用户输入的任意 shell 命令。
- Code Agent 默认只读，允许生成 patch 建议，但不要自动应用用户未确认的 patch。
```

## Prompt 2: Start Code Agent MVP

```text
你现在在仓库 /root/projects/project/agent-communication-main-v2。

我要开始 Milestone 2: Code Agent MVP。

请先阅读：
- docs/upgrade/README.md
- docs/upgrade/milestones.md
- docs/upgrade/upgrade-log.md
- docs/superpowers/plans/2026-06-11-lab-agent-v0.2.md

目标：
让 Orchestrator 的 code intent 路由到真正的 Code Agent，而不是 fallback 到 general。

请按 TDD 做一个小任务：
1. 先写或扩展测试，验证 Code Agent 注册信息包含 tag code 和代码分析 skill。
2. 确认测试失败。
3. 实现最小 Code Agent 注册/启动代码。
4. 让测试通过。
5. 如果改了 CMake，运行 cmake --build build -j2。
6. 运行 ctest --test-dir build --output-on-failure。
7. 更新 docs/upgrade/upgrade-log.md。
8. git diff --check。
9. git commit。

暂时不要做自动文件修改、远程执行或 CUDA/MPI 接入。
```

## Prompt 3: Start AlgorithmCard Registry

```text
你现在在仓库 /root/projects/project/agent-communication-main-v2。

我要开始 Milestone 3: AlgorithmCard Registry。

请先阅读：
- docs/upgrade/README.md
- docs/upgrade/milestones.md
- docs/upgrade/upgrade-log.md
- docs/superpowers/plans/2026-06-11-lab-agent-v0.2.md

目标：
新增 AlgorithmCard 和 AlgorithmRegistry，让 FWI、频率外推、叠后算法、新算法都可以用 JSON 卡片注册。

请先做最小可验证版本：
1. 写测试：加载一个合法 AlgorithmCard JSON，验证 id/name/domain/parameters/inputs/outputs/failure_modes。
2. 写测试：缺 id 或缺 name 的卡片必须被拒绝。
3. 实现最小 C++ 模型和 registry。
4. 添加 resources/algorithms/fwi_cuda_mpi.json 作为第一张卡。
5. 运行 cmake --build build -j2。
6. 运行 ctest --test-dir build --output-on-failure。
7. 更新 docs/upgrade/upgrade-log.md。
8. git diff --check。
9. git commit。

不要接真实作业执行。
```

## Prompt 4: Start ExperimentSpec And DryRunBackend

```text
你现在在仓库 /root/projects/project/agent-communication-main-v2。

我要开始 Milestone 4: ExperimentSpec, JobSpec, DryRunBackend。

请先阅读：
- docs/upgrade/README.md
- docs/upgrade/milestones.md
- docs/upgrade/upgrade-log.md
- docs/superpowers/plans/2026-06-11-lab-agent-v0.2.md

目标：
让系统能把实验计划表示成结构化 ExperimentSpec，再渲染成 dry-run JobSpec。只生成命令或脚本草案，不执行。

请按 TDD 做：
1. 测试 valid ExperimentSpec 能通过 validation。
2. 测试 missing algorithm_id 会失败。
3. 测试 gpu_count < 0 会失败。
4. 测试 DryRunBackend render 输出包含 dry_run: true，mpirun 或 command 字段，以及 artifact 路径。
5. 实现最小代码。
6. 运行 cmake --build build -j2。
7. 运行 ctest --test-dir build --output-on-failure。
8. 更新 docs/upgrade/upgrade-log.md。
9. git diff --check。
10. git commit。
```

## Prompt 5: Fix Broken Tests

```text
你现在在仓库 /root/projects/project/agent-communication-main-v2。

上一次升级后测试失败了。请不要添加新功能，专门修复失败。

请执行：
1. 阅读 docs/upgrade/README.md 和 docs/upgrade/upgrade-log.md。
2. 运行 git status --short。
3. 运行 ctest --test-dir build --output-on-failure，记录失败测试。
4. 用 systematic debugging 的方式定位根因，不要猜。
5. 只改和失败相关的代码或测试。
6. 重新运行失败测试。
7. 再运行 ctest --test-dir build --output-on-failure。
8. 更新 docs/upgrade/upgrade-log.md。
9. git diff --check。
10. git commit。

最后告诉我失败原因、修复点、测试结果、commit hash。
```

## Prompt 6: Documentation And Resume Story

```text
你现在在仓库 /root/projects/project/agent-communication-main-v2。

请优化项目文档和求职展示，但不要改业务代码。

请先阅读：
- README.md
- docs/upgrade/README.md
- docs/upgrade/milestones.md
- docs/upgrade/upgrade-log.md

目标：
让项目在 README 里清楚展示为 Lab Research Agent Platform，突出 C++、gRPC、A2A、MCP、RAG、Code Agent、Experiment Planner、AlgorithmCard、DryRunBackend。

要求：
1. 文档真实反映当前完成状态，不夸大。
2. 明确写出当前不执行真实 CUDA/MPI 作业。
3. 添加一个 demo script section，展示用户如何体验问答、代码分析、实验规划 dry-run。
4. 运行 git diff --check。
5. 更新 docs/upgrade/upgrade-log.md。
6. git commit。
```

