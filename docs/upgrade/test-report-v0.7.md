# v0.7 JobBackend Reservation Test Report

Date: 2026-06-22

## Scope

v0.7 reserves the execution-backend boundary without enabling real execution.
It adds a `JobBackend` interface with backend type identity, explicit backend
type enum values, shared backend parsing and validation helpers, and runtime
rejection for every non-`dry_run` backend value.

It does not submit jobs, run CUDA/MPI binaries, connect to SSH, Slurm, PBS, or
remote servers, execute shell commands from user input, or let Code Agent apply
patches automatically.

## Files Changed

- `research/include/agent_rpc/research/job_backend.h`
- `research/src/job_backend.cpp`
- `research/src/dry_run_backend.cpp`
- `research/src/algorithm_card.cpp`
- `research/CMakeLists.txt`
- `tests/test_experiment_spec.cpp`
- `tests/test_algorithm_card.cpp`
- `tests/test_algorithm_registry.cpp`
- `docs/upgrade/README.md`
- `docs/upgrade/milestones.md`
- `docs/upgrade/version-roadmap.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/test-report-v0.7.md`
- `docs/upgrade/upgrade-log.md`

## Validation

Commands run:

```bash
cmake --build build -j2
ctest --test-dir build -R "(ExperimentSpecTest|AlgorithmCardTest)" --output-on-failure
ctest --test-dir build --output-on-failure
git diff --check
```

Result:

- PASS. RED `cmake --build build -j2` failed first because `JobBackendType`,
  `parse_job_backend_type`, and `validate_backend_enabled` did not exist yet.
- PASS. `cmake --build build -j2` exited 0 after implementation.
- PASS. Targeted `AlgorithmCardTest` and `ExperimentSpecTest` passed 2/2.
- PASS. Full `ctest` initially exposed one stale `AlgorithmRegistryTest`
  assertion that still expected the old v0.2 backend error string; after
  migrating the assertion to the shared guard message, `AlgorithmRegistryTest`
  passed.
- PASS. Full `ctest` passed 25/25 tests.
- PASS. `git diff --check` produced no output.

## How Slurm/PBS Can Be Added Later

Slurm/PBS should be added as controlled backend implementations only after the
security and operations model is explicit. The future implementation path should
look like this:

1. Add a backend factory that reads a trusted deployment configuration, not
   arbitrary user text, and returns a `JobBackend` implementation.
2. Keep `JobSpec` structured. Do not accept raw shell scripts from chat input.
   Generate submission scripts from allowlisted fields such as command template,
   working directory, resource counts, time limit, environment allowlist, and
   artifact paths.
3. Add authentication and authorization before any submit path exists. A user
   should not be able to choose `slurm` or `pbs` only by changing JSON text.
4. Isolate each job in a workspace with controlled input/output paths. Do not
   allow `..`, absolute path escapes, or writes outside the job workspace.
5. Add audit logging for who requested the job, what `ExperimentSpec` and
   `JobSpec` were approved, which backend submitted it, and which artifacts were
   collected.
6. Add lifecycle methods only when needed, such as submit/status/cancel/logs,
   and test each method with a fake backend before touching a real scheduler.
7. Keep dry-run rendering available even after real backends exist so users can
   inspect the exact planned job before approval.

## Detailed Chinese Knowledge Summary

### 1. 解决的问题

v0.6 以后，平台已经能读配置模板、解析日志、提取 loss 曲线并识别常见失败模式。
但是执行边界仍然不够明确：系统里有 `DryRunBackend`，也有 `JobSpec`，但还没有一个
统一的 backend 类型层来表达“未来会有 local、SSH、Slurm、PBS，但现在只能 dry-run”。

这个缺口很关键。没有明确的 backend 类型和拒绝逻辑时，后续 JSON 元数据、Planner
输出或者 UI 选择很容易把 `slurm`、`pbs` 这样的字符串当成“已经支持”。对研究计算平台
来说，这不是普通功能缺失，而是安全边界不清：真实 CUDA/MPI 或集群作业一旦被错误提交，
可能占用实验室资源、覆盖输出目录、暴露远程凭据，甚至让聊天输入间接变成 shell 执行。

v0.7 要解决的不是“怎么跑作业”，而是“在还不能跑作业时，代码层如何诚实地表达未来形状
并明确拒绝”。它把 backend 名称、解析、拒绝和文档放在一起，让后续 v0.8 可以沿着清晰
接口扩展，而不是从一堆散落字符串里猜系统到底支持什么。

### 2. 实现方式

v0.7 的数据流很短，但边界很重要：外部 JSON 或代码传入 backend 字符串，先经过
`parse_job_backend_type` 转成 `JobBackendType`，再由 `validate_backend_enabled` 判断是否
允许。当前只有 `dry_run` 返回空错误；`local`、`ssh`、`slurm`、`pbs` 都被识别为保留值，
但返回明确拒绝信息；未知值比如 `kubernetes` 会返回支持值列表，避免静默降级。

API 形状如下：

- `JobBackendType` 枚举列出 `DryRun`、`Local`、`Ssh`、`Slurm`、`Pbs`、`Unknown`。
- `to_string(JobBackendType)` 把枚举转回稳定字符串，方便日志、UI 和 dry-run 文本展示。
- `parse_job_backend_type(std::string)` 把 JSON 或配置里的字符串转成枚举。
- `supported_job_backend_names()` 给错误信息、UI 下拉或未来文档生成提供单一来源。
- `validate_backend_enabled(...)` 是真正的安全门。它接受 `dry_run`，拒绝所有保留或未知值。
- `JobBackend::type()` 让每个 backend 实现暴露自己的类型，`DryRunBackend::type()` 返回
  `JobBackendType::DryRun`。

关键取舍是没有现在就实现 `LocalBackend`、`SshBackend`、`SlurmBackend` 或 `PbsBackend`。
如果只为了枚举好看而加空实现，调用方可能误以为这些 backend 能用。也没有立刻做复杂
factory，因为当前只有一个具体 backend，factory 会引入还没有消费场景的抽象。更合适的做法
是先把“类型和值域”和“拒绝策略”沉到共享 helper，再让 `AlgorithmCard` 复用它。这样 JSON
算法卡里写 `slurm` 会被明确拒绝，而不是靠某个调用点临时判断。

### 3. 关键文件、测试和资源

`research/include/agent_rpc/research/job_backend.h` 是新的后端边界。它既定义
`JobBackendType` 和解析/校验函数声明，也定义 `JobBackend` 抽象接口。这个文件保护的是
架构接口：后续真实后端必须走同一套类型和行为契约。

`research/src/job_backend.cpp` 是共享 backend 类型实现。它集中维护支持的 backend 名称、
字符串解析、字符串输出和 dry-run guard。把这些逻辑集中起来，可以避免 `AlgorithmCard`、
Planner、UI 或未来 backend factory 各写一套字符串判断。

`research/src/dry_run_backend.cpp` 只新增 `DryRunBackend::type()`。它没有改变 render、
validate 或 explain 的既有行为，说明本次是接口硬化，不是执行语义变化。

`research/src/algorithm_card.cpp` 从直接判断 `backend != "dry_run"` 改成调用
`validate_backend_enabled(backend)`。这个测试保护的是资源元数据边界：即使算法卡 JSON
声明了 `slurm` 或未知 backend，加载校验也会失败。

`tests/test_experiment_spec.cpp` 增加三类保护：接口调用能看到 `DryRunBackend` 的类型；
五个保留 backend 名称能稳定解析；所有非 `dry_run` 值在 runtime guard 中被拒绝。它保护
的是未来执行入口不能绕过 guard。

`tests/test_algorithm_card.cpp` 增加算法卡层面的拒绝测试。它保护的是 JSON 数据不会因为写了
`slurm`、`pbs` 或未知 backend 就悄悄进入可执行状态。

### 4. 安全或产品边界

CUDA/MPI 边界：v0.7 没有调用 CUDA、MPI、`mpirun`、求解器二进制或任何实验程序。`JobSpec`
里的 command 仍然只是 dry-run 文本，不能被 backend 执行。

SSH 边界：虽然 `JobBackendType::Ssh` 已经存在，但它是保留值。传入 `ssh` 会返回
“reserved for future server execution; only dry_run is enabled”。没有主机名、密钥、用户、
端口、远程目录或远程命令逻辑。

Slurm/PBS 边界：`slurm` 和 `pbs` 现在只是 enum 值和错误信息，不是调度器集成。没有
`sbatch`、`qsub`、队列选择、分区选择、作业取消、日志拉取或 artifact 同步。

远程执行和 shell 执行边界：v0.7 没有 `system`、`popen`、shell script 生成和执行，也没有
把用户输入当 shell 命令。未来如果接入 Slurm/PBS，也必须从结构化 `JobSpec` 生成受控脚本，
不能让用户直接提交任意 shell。

Code Agent 写权限边界：本次没有改变 Code Agent。它仍然是只读分析和 patch proposal，
不会自动应用 patch，不会写配置，也不会替用户启用 backend。

### 5. 调试或 TDD 证据

本批次先写测试再实现。新增测试先要求 `backend.type()`、`JobBackendType`、
`parse_job_backend_type`、`to_string` 和 `validate_backend_enabled` 存在。第一次运行
`cmake --build build -j2` 按预期失败，错误集中在这些符号不存在，说明 RED 是正确的：
测试没有因为拼写或 CMake 配置失败，而是因为目标能力缺失。

实现阶段只做最小改动：在头文件声明 enum 和 helper；新增 `job_backend.cpp` 实现解析和拒绝；
在 `DryRunBackend` 里返回 `JobBackendType::DryRun`；在 `AlgorithmCard` 校验里复用共享 guard。
没有加入真实 backend，没有加入 factory，也没有改变 dry-run 渲染语义。

GREEN 阶段先运行 `cmake --build build -j2`，确认库和测试能编译链接；再运行
`ctest --test-dir build -R "(ExperimentSpecTest|AlgorithmCardTest)" --output-on-failure`，确认后端
测试和算法卡测试都通过；最后运行完整 `ctest`，证明这次接口变化没有破坏 Planner、
Lab Code Adapter、A2A、MCP、Web branding 或其他既有测试。

完整 `ctest` 第一次还暴露了一个旧测试迁移问题：`AlgorithmRegistryTest` 仍然在找旧的
`only dry_run backend is enabled in v0.2` 文案。根因不是 registry 行为错误，而是
`AlgorithmCard` 已经切换到共享 backend guard，错误信息变成了新的“reserved backend +
only dry_run”格式。修复方式是迁移该测试断言，让它继续保护“registry 能报告 unsafe
backend”这个行为，而不是绑定旧文案。

### 6. 面试怎么讲

短 pitch：

我在研究计算智能体平台里做了一个 v0.7 后端边界预留层。它把未来可能支持的 local、SSH、
Slurm、PBS 都建模成明确枚举，但当前 runtime 只允许 dry-run，并用测试保证算法卡和后端调用
都不能绕过这个限制。

技术深挖版：

平台的 Planner 会产生 `ExperimentSpec` 和 `JobSpec`，但真实集群执行是高风险能力。因此我把
执行层分成三段：`JobSpec` 只描述结构化作业；`JobBackend` 定义 validate/render/explain/type
契约；`validate_backend_enabled` 决定当前是否允许某种 backend。这样后续接 Slurm/PBS 时，
可以新增具体 backend 实现，但在没有认证、隔离、审计和审批之前，任何 JSON 里的 `slurm`
或 `pbs` 都会被共享 guard 拒绝。

简历讲法：

“Designed a dry-run-first JobBackend boundary in C++ with explicit backend
type modeling and runtime rejection for reserved local/SSH/Slurm/PBS execution
paths, preventing accidental cluster submission before auth, isolation, and
audit controls are implemented.”

### 7. 常见追问和 STAR 复盘

问：为什么要把 `local`、`ssh`、`slurm`、`pbs` 放进 enum，但又拒绝它们？

答：这是为了区分“产品路线里承认这些 backend 类型”和“当前版本已经支持执行”。enum 让未来
扩展有稳定 API，runtime guard 保证当前不会误执行。没有 enum 时，字符串会散落在不同模块；
没有 guard 时，enum 又可能被误认为功能已经打开。

问：为什么不直接把非 `dry_run` 从 enum 里删掉？

答：如果只保留 `dry_run`，后续做 v0.8 时仍然要重新设计类型边界，而且 roadmap 和代码之间
没有连接。现在保留值域但拒绝执行，可以让测试和文档共同说明系统路线。

问：为什么不现在做 SlurmBackend？

答：SlurmBackend 不只是调用 `sbatch`。它需要认证、授权、工作目录隔离、资源限额、脚本模板、
审计日志、状态查询、取消、日志和 artifact 收集。如果这些没有设计好，接入 Slurm 反而会把
聊天系统变成危险的远程命令入口。

问：这个改动怎么防止用户输入变成 shell 执行？

答：当前没有任何执行方法。`validate_backend_enabled` 在 backend 选择阶段就拒绝了所有真实
执行类型；`DryRunBackend` 只渲染文本；`AlgorithmCard` 加载时也会拒绝非 dry-run backend。
未来即使加真实 backend，也应该从结构化 `JobSpec` 和 allowlisted template 生成脚本，不能
直接执行聊天文本。

STAR 复盘：

Situation：v0.6 后平台可以理解配置和日志，但下一步要走向真实执行，必须先把执行后端边界
定义清楚。

Task：在不接入任何真实 CUDA/MPI、SSH、Slurm/PBS 的前提下，完成未来后端类型预留和当前
版本的安全拒绝。

Action：我用 TDD 先写失败测试，要求 backend enum、解析函数、类型输出和 runtime guard
存在；随后实现共享 `JobBackendType` 和 `validate_backend_enabled`，让 `DryRunBackend`
暴露类型，并让 `AlgorithmCard` 复用同一套 guard；最后补 roadmap、milestone、career notes
和测试报告。

Result：v0.7 完成后，系统有了清晰的 backend 扩展边界，但完整测试仍证明没有真实执行能力
被打开。下一步可以进入 v0.8 的安全设计，而不是直接把 Slurm/PBS 接到聊天入口上。
