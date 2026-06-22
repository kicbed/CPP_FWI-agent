# v0.6 Lab Code Adapter Test Report

Date: 2026-06-22

## Scope

v0.6 adds a deterministic Lab Code Adapter layer for inspecting lab-style FWI
configuration templates, producing dry-run config previews, parsing supplied log
text, extracting loss curves, recognizing common failure signals, and producing
Planner-facing diagnostic summaries.

It does not submit jobs, run CUDA/MPI binaries, connect to SSH, Slurm, PBS, or
remote servers, execute shell commands from user input, or let Code Agent apply
patches automatically.

## Files Changed

- `research/include/agent_rpc/research/lab_code_adapter.h`
- `research/src/lab_code_adapter.cpp`
- `research/CMakeLists.txt`
- `resources/lab_code_adapter/config_templates/fwi_marmousi_multiscale.json`
- `resources/lab_code_adapter/logs/fwi_loss_stagnation.log`
- `resources/lab_code_adapter/logs/fwi_nan_instability.log`
- `tests/test_lab_code_adapter.cpp`
- `tests/CMakeLists.txt`
- `docs/upgrade/README.md`
- `docs/upgrade/milestones.md`
- `docs/upgrade/version-roadmap.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/upgrade-log.md`
- `docs/superpowers/plans/2026-06-22-lab-code-adapter-v0.6.md`

## Validation

Commands run:

```bash
cmake --build build -j2
ctest --test-dir build -R LabCodeAdapter --output-on-failure
ctest --test-dir build --output-on-failure
git diff --check
```

Result:

- PASS. `cmake --build build -j2` exited 0.
- PASS. `LabCodeAdapterTest` passed 1/1.
- PASS. Full `ctest` passed 25/25 tests.
- PASS. `git diff --check` produced no output.

## Detailed Chinese Knowledge Summary

### 1. 解决的问题

v0.5 已经把浏览器界面升级成 Lab Agent Workbench，用户能看到
AlgorithmCard、ExperimentSpec、JobSpec、参数表、路由轨迹和 dry-run 状态。
但 v0.5 的不足是：系统能“规划一个实验”，却还不能理解实验真正落地时常见的
输入材料，例如实验配置模板、FWI 运行日志、loss 曲线以及失败报错。对实验室
新人来说，最常见的问题往往不是“我该跑哪个算法”，而是“这个配置合理吗”、
“loss 为什么不降”、“是不是 cycle skipping”、“日志里的 NaN 或 OOM 说明什么”。

v0.6 的目标就是补上这个中间层：它不是执行后端，而是一个本地、确定性、可测试
的 Lab Code Adapter。它让平台开始理解实验代码周边的配置和日志形状，为后续
Workbench 展示、Planner 诊断和最终受控执行后端打基础。这样做的核心价值是
先把“读懂实验材料”和“真的提交作业”分开，避免一上来就把 CUDA/MPI、SSH、
Slurm/PBS 等高风险能力混进产品。

### 2. 实现方式

v0.6 的数据流是：配置模板 JSON 和日志文本进入 `lab_code_adapter`，先变成
结构化 C++ 数据，再由识别器产生有限的诊断 finding，最后由 summary builder
输出 Planner 可以消费的摘要。

API 形状分为几层：

- `ConfigTemplate` 和 `ConfigPlaceholder` 表示实验配置模板和占位符。
- `load_config_template(path)` 从固定资源或测试 fixture 加载 JSON，并返回
  `ConfigTemplateLoadResult`，错误不会抛到调用方外面，而是放进 `errors`。
- `render_config_preview(template, values)` 从结构化参数 map 生成配置预览，
  输出带 `dry_run: true`，不做 shell 展开、环境变量展开或文件写入。
- `parse_lab_log(log_text)` 只接受文本内容，解析 `ITER/FREQ/LOSS`、`WARN`、
  `ERROR`、`STATUS`，提取 loss curve、warning、diagnostic line 和最终状态。
- `recognize_failure_modes(parsed_log, config_values)` 输出结构化
  `FailureFinding`，包括 `code`、`severity`、`evidence`、`suggested_next_check`。
- `build_planner_diagnostic_summary(template, parsed_log, findings)` 生成受控摘要，
  包含 observed symptoms、likely causes、parameter tuning suggestions 和安全边界。

关键取舍是没有直接把日志交给 LLM，也没有让 Planner 读取无限长原始日志。原因是
原始日志可能很长、很脏，也可能包含命令片段或路径信息。把它先压缩成结构化
finding，可以降低 prompt 注入风险，也让测试能稳定断言。另一个取舍是暂时不引入
复杂规则引擎或机器学习异常检测，因为 v0.6 只需要覆盖实验室最常见的失败模式：
loss 停滞、NaN/Inf、cycle-skipping 风险和资源限制。规则足够透明，也方便面试时
解释每个判断来自哪条 evidence。

### 3. 关键文件、测试和资源

`research/include/agent_rpc/research/lab_code_adapter.h` 是 v0.6 的公开接口。
它定义了模板、渲染结果、日志解析结果、失败 finding 和 Planner 摘要。这个文件的
价值是把“适配实验代码形状”的能力沉到 research library，而不是散落在 UI 或 agent
prompt 里。

`research/src/lab_code_adapter.cpp` 是实现。它负责 JSON 读取、执行字段拒绝、
fixture 路径解析、配置预览渲染、日志正则解析、失败规则识别和摘要构造。路径解析
会从当前目录向上寻找资源，保证测试从仓库根目录或 CTest 的 build 目录运行都能找到
fixture。

`resources/lab_code_adapter/config_templates/fwi_marmousi_multiscale.json` 是配置模板
fixture，覆盖 dataset、start frequency、max frequency、grid spacing 和 iteration
count。它保护的是模板 schema 和占位符顺序的确定性。

`resources/lab_code_adapter/logs/fwi_loss_stagnation.log` 模拟 loss 下降很慢且日志
出现 plateau warning 的场景。测试用它保护 loss curve 解析和 loss stagnation 识别。

`resources/lab_code_adapter/logs/fwi_nan_instability.log` 模拟高频起步后出现 NaN/Inf
的场景。测试用它保护数值不稳定识别。

`tests/test_lab_code_adapter.cpp` 是核心测试。每个测试保护一个风险：
模板加载测试保护 JSON 字段、占位符顺序、必填字段和描述完整性；执行字段拒绝测试
保护 `submit_command` 和 `ssh_host` 这类危险字段不能进入模板；绝对路径拒绝测试
保护 adapter 不变成任意文件读取入口；配置预览测试保护 `dry_run: true` 和参数填充；
日志解析测试保护 iteration/frequency/loss/warning/status 提取；失败识别测试保护 loss
stagnation、NaN/Inf、cycle-skipping risk 和 resource limit；Planner summary 测试保护摘要
不会丢掉安全边界。

### 4. 安全或产品边界

v0.6 明确不接真实 CUDA/MPI。日志里可以出现 CUDA/MPI 字样，但 adapter 只把它当文本
解析，不会调用 `mpirun`、`nvcc`、`cuda` 程序或任何求解器。

v0.6 不接 SSH、Slurm、PBS 或远程服务器。配置模板中出现 `ssh_host`、
`submit_command`、`slurm_partition`、`pbs_queue`、`remote_host`、`execution_command`
会被拒绝。这一点很重要：模板不是作业提交描述，它只是 dry-run 配置预览描述。

v0.6 不执行来自用户输入的 shell 命令。`render_config_preview` 只是把结构化值写成
预览文本，不做 `$HOME` 展开、反引号执行、管道执行或环境变量替换。日志解析也只接收
文本内容；配置模板读取拒绝绝对路径和 `..` traversal，不把 adapter 变成任意文件读取
工具。

Code Agent 写权限仍然关闭。v0.6 可以为 Planner 或 Workbench 提供诊断摘要，但不会
让 Code Agent 自动修改配置或应用 patch。后续如果要支持 patch，必须作为显式确认的
独立能力设计。

### 5. 调试或 TDD 证据

本次按 TDD 小步推进。Task 1 先写 `LabCodeAdapterTest`，第一次构建失败在缺少
`agent_rpc/research/lab_code_adapter.h`，证明测试确实覆盖了新入口。实现模板 reader
后目标测试又暴露了 CTest 工作目录下找不到 fixture 的问题，于是把路径解析改成从当前
目录向上寻找资源，目标测试通过。后续安全复查发现 `load_config_template(path)` 还应拒绝
绝对路径，于是先加了 `RejectsAbsoluteConfigTemplatePaths` 失败测试，手动运行确认失败，
再实现绝对路径和 `..` traversal 拒绝，目标测试转绿。

Task 2 先写 `render_config_preview` 测试，构建失败在缺少函数，然后实现 dry-run 预览。
Task 3 先写 `parse_lab_log` 测试，构建失败在缺少函数，然后实现日志和 loss curve
解析。Task 4 先写 failure recognizer 测试，失败点是缺少 `FailureFinding` 和
`recognize_failure_modes`，随后实现结构化 finding。Task 5 先写 Planner summary
测试，失败点是缺少 `build_planner_diagnostic_summary`，随后实现有限摘要和安全边界。

最终验证是完整构建和完整测试：`cmake --build build -j2` 退出 0，完整 `ctest` 25/25
通过，说明新增 research library 源文件、测试目标和既有 RPC/A2A/MCP/Planner/UI 测试
没有冲突。

### 6. 面试怎么讲

短 pitch：

这个项目是一个面向地震 FWI 实验的多智能体研究计算 Workbench。v0.6 我实现了 Lab
Code Adapter，让系统能在不提交作业的前提下读取实验配置模板、解析 FWI 日志、提取
loss 曲线、识别常见失败模式，并把结果变成 Planner 可用的结构化诊断摘要。

技术深挖版：

我把能力放在 C++ `agent_rpc_research` 库里，而不是写在 prompt 里。输入侧是 JSON
配置模板和日志文本，内部转换成 `ConfigTemplate`、`LabLogParseResult`、
`FailureFinding`、`PlannerDiagnosticSummary`。失败识别是透明规则：loss plateau 或
下降不足识别为 stagnation；NaN/Inf 文本识别为数值不稳定；高起始频率识别为
cycle-skipping risk；OOM/resource/allocation 文本识别为资源限制。输出侧只给有限字段，
这样 Planner 不需要吃大段日志，也能解释建议来自哪条 evidence。

常见追问和回答：

问：为什么不用 LLM 直接读日志？
答：直接把原始日志交给 LLM 不稳定，也有 prompt 注入和上下文膨胀风险。先做确定性
parser 和 finding，可以测试、可追踪、可解释，LLM 只负责组织语言或下一步计划。

问：为什么不直接接 Slurm/PBS？
答：实验执行涉及认证、授权、工作目录隔离、审计和资源保护。v0.6 先解决“读懂配置和
日志”，执行后端留到 v0.8，避免把诊断能力和提交能力耦合。

问：如何证明不会执行危险命令？
答：API 只接收 JSON 和文本，生成的是 preview string；测试覆盖执行字段拒绝；实现里没有
`system`、`popen`、SSH、Slurm/PBS 调用，也没有从用户输入路径任意读文件。

问：规则识别会不会太简单？
答：v0.6 是 MVP，目标是覆盖高频、可解释的失败类型。规则引擎和统计异常检测可以后续加，
但当前规则已经能保护最常见的教学和面试 demo 场景，并且每条规则有测试。

STAR 复盘：

Situation：v0.5 后系统能规划和展示 dry-run 实验，但还不能理解真实 FWI 实验中最常见的
配置和日志问题。

Task：需要在不接真实集群、不运行 CUDA/MPI 的前提下，让系统能分析配置模板、loss 曲线和
失败症状。

Action：我用 TDD 增加 `lab_code_adapter`，先写失败测试，再实现模板 reader、dry-run
config preview、日志 parser、failure recognizer 和 Planner summary，并把所有危险执行
字段挡在模板校验阶段。

Result：新增 `LabCodeAdapterTest`，完整测试从 24 个增加到 25 个并全部通过。平台获得了
从“规划实验”走向“诊断实验材料”的关键能力，同时继续保持 dry-run-only 的产品边界。
