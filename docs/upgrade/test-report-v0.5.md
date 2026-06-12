# v0.5 Lab Workbench UI Test Report

Date: 2026-06-12

Scope:

- Complete `v0.5 Lab Workbench UI`.
- Keep the browser UI usable as a research workbench instead of a chat-only
  surface.
- Preserve all dry-run and read-only safety boundaries.

Validation summary:

- `cmake --build build -j2` before implementation produced the expected RED
  failure through `WebBrandingTest`: missing `id="algorithmList"`.
- `cmake --build build -j2` after implementation passed.
- `ctest --test-dir build -R WebBrandingTest --output-on-failure` passed after
  implementation.
- `python3 web/serve.py 18080` plus `curl http://localhost:18080/` verified the
  static Web UI can be served locally and contains the v0.5 workbench panels.
- Smoke screenshot target:
  `docs/upgrade/screenshots/lab-workbench-v0.5-smoke.png`.
- Full `ctest --test-dir build --output-on-failure` passed after the final
  implementation.
- `git diff --check` produced no output.

## 1. 解决的问题

v0.4 已经让 Experiment Planner 能产出结构化内容：算法推荐、假设、参数表、风险分析、`ExperimentSpec`、dry-run `JobSpec` 和可复现实验记录。但是这些能力主要停留在后端模型、测试和文本回答里。对演示者或实验室新成员来说，浏览器页面仍然像一个普通聊天窗口：用户只能看到一段回答，很难快速判断请求被哪个 agent 处理、使用了哪些本地知识或工具、推荐了哪个 AlgorithmCard、生成的实验规格和作业预览是否仍然停留在 dry-run 边界。

v0.5 要解决的核心问题就是“可观察性”和“产品叙事”。科研计算 Agent 不能只把复杂计划塞进聊天气泡里，因为实验计划天然有结构：算法、数据集、参数、资源、输出、风险和执行状态都应该被单独检查。上一版不足之处不是缺少规划能力，而是缺少一个能把规划结果拆开看的工作台界面。v0.5 因此把页面从 generic chat 升级成 Lab Agent Workbench，让演示者第一眼能看到这是一个面向地震科研计算的多 Agent 工作台，而不是普通问答机器人。

这次仍然没有接真实 CUDA/MPI、集群、SSH、Slurm 或 PBS。原因很明确：UI 层先把 dry-run 计划、状态和边界展示清楚，才能降低后续接入真实执行时的误操作风险。如果还没有可视化地展示“这只是 dry-run 预览”，就先接真实后端，会让用户误以为某个 JobSpec 已经可以安全提交，这不符合项目的安全节奏。

## 2. 实现方式

v0.5 采用了最小可验证的静态前端方案。仓库当前 Web UI 是一个无前端构建链的 `web/index.html`，通过 `web/serve.py` 直接托管。因此本次没有引入 React、Vite、bundler 或新的 npm 依赖，而是在现有页面里增加 workbench 布局和原生 JavaScript 渲染函数。这个选择的好处是改动集中、启动路径不变、CI 仍然只需要 CMake/CTest 和静态文件检查；代价是 `index.html` 会继续偏大，后续如果 UI 复杂度继续上升，v0.6 或 v0.7 可以再考虑拆分 JS/CSS 模块。

数据流分成三层。第一层是静态种子数据：页面内定义 `ALGORITHM_CARDS`，对应已有 `resources/algorithms` 中的 CUDA-MPI FWI、frequency extrapolation、post-stack inversion 三类算法；同时定义 `DEFAULT_EXPERIMENT_SPEC`、`DEFAULT_JOB_SPEC`、`DEFAULT_ROUTE_TRACE` 和 `DEFAULT_TOOL_CALLS`。这些数据让页面在没有后端服务时也能展示完整工作台形态，适合本地 demo 和招聘展示。

第二层是对话结果解析：当 assistant 消息进入页面时，`updateInspectorFromAnswer(content)` 会根据文本内容选择相关 AlgorithmCard，并尝试通过 `extractExperimentSpec(text)` 从 fenced JSON block 中解析包含 `algorithm_id` 和 `dataset_id` 的实验规格，通过 `extractJobSpec(text)` 从包含 `dry_run: true` 的文本或 YAML block 中解析作业预览。解析结果只进入右侧 inspector 的渲染函数，绝不会调用后端提交接口。

第三层是渲染层。左侧 sidebar 新增 `algorithmList` 和 `experimentHistory`，用于展示算法入口和历史 dry-run 草稿。中间仍然保留原来的 HTTP/gRPC 对话区，避免破坏已有交互。右侧 `workbenchInspector` 负责展示 `Route Trace`、`Tool Calls`、`AlgorithmCard`、`Parameter Table`、`ExperimentSpec`、`JobSpec` 和 `Service Status`。这些区域用 `renderAlgorithmList`、`selectAlgorithm`、`renderRouteTrace`、`renderToolCalls`、`renderParameterTable`、`renderExperimentSpec`、`renderJobSpec` 等函数更新。

API 形状上，UI 没有新增后端 endpoint。状态检查继续走本地 HTTP 探测：Orchestrator 使用 `:5000`，Registry 使用 `:8500/v1/agent/cards`，Embedding 使用 `:6000/health`，Code Agent 使用 `:5010/.well-known/agent-card.json`，Planner Agent 使用 `:5011/.well-known/agent-card.json`，gRPC bridge 使用 `:50052/health`。MCP 在当前系统中主要是本地 stdio/subprocess 配置，不是稳定 HTTP 健康服务，所以 UI 把它标为本地 stdio 状态，而不是伪造一个远程服务探测。

没有采用更复杂方案的原因是 v0.5 的目标是“可演示、可检查、可回归测试”的工作台，不是完整前端架构迁移。没有采用更简单方案的原因是只改标题或加几段文案不能满足 milestone：用户需要看到 route trace、tool calls、AlgorithmCards、Spec/JobSpec 和状态面板。这次方案正好卡在两者之间：不引入新系统，但把关键研究对象结构化展示出来。

## 3. 关键文件/测试/资源

`web/index.html` 是主要实现文件。它现在承担三个职责：保留原有聊天交互，提供左侧 AlgorithmCard/实验历史入口，提供右侧 inspector 的结构化渲染。关键 DOM id 包括 `algorithmList`、`experimentHistory`、`workbenchInspector`、`routeTrace`、`toolCalls`、`selectedAlgorithmCard`、`parameterTable`、`experimentSpecPanel` 和 `jobSpecPanel`。这些 id 被测试锁定，防止后续 UI 改动把 v0.5 的核心工作台区域删掉。

`tests/check_web_branding.cmake` 从单纯品牌检查扩展成 Web UI 合同测试。它检查页面仍然使用 `Lab Agent Workbench` 品牌，也检查 v0.5 必须存在的 panel、状态 id、算法 id、渲染函数和解析函数。这个测试保护的风险是“页面看起来还能打开，但工作台关键能力被无意删除”。因为当前项目没有前端测试框架，用 CMake 读静态文件是一种低成本但有效的守门方式。

`tests/CMakeLists.txt` 注册 `WebBrandingTest`，并把 `docs/upgrade/test-report-v0.5.md` 作为检查输入。这样 v0.5 完成不只要求代码有 UI，还要求有对应测试报告和中文复盘章节。这个设计保护的是升级流程本身：以后如果有人只改页面、不写复盘，测试会失败。

`docs/upgrade/milestones.md`、`docs/upgrade/version-roadmap.md`、`docs/upgrade/README.md` 和 `docs/upgrade/career-notes.md` 记录产品状态。它们保护项目叙事一致性：README 说当前目标，roadmap 说版本状态，milestones 说任务完成情况，career notes 说面试可讲的已实现能力。v0.5 完成后，这些文件都必须从“计划中”更新为“已完成”。

`docs/upgrade/upgrade-log.md` 是本轮可验证事实记录，记录 RED 失败、GREEN 通过、Web smoke、截图路径、完整 CTest 和 diff check。`docs/upgrade/screenshots/lab-workbench-v0.5-smoke.png` 是 UI smoke 的视觉证据，方便后续回看页面首屏是否真的像工作台。

## 4. 安全或产品边界

本次 UI 完成没有改变执行能力。页面里可以看到 `dry_run: true`，可以渲染 `JobSpec`，也可以展示 command、working directory、MPI process count、GPU count 和 artifact 路径，但这些内容只是文本预览。没有新增任何 API 调用去执行 command，没有从用户输入拼接 shell 命令，也没有提交作业到本机或远端。

CUDA/MPI 边界：AlgorithmCard 中仍然可以出现 CUDA/MPI，因为它描述的是未来实验代码形态和算法背景，不代表当前系统会执行 CUDA/MPI 程序。UI 中的 `CUDA-MPI FWI` 卡片明确落在 `dry_run` backend 下。`gpu_count` 和 `mpi_processes` 是资源字段展示，不是调度动作。

SSH、Slurm、PBS、远程执行边界：v0.5 没有引入 SSH 配置、Slurm/PBS 脚本提交、远程服务器地址、认证令牌或 job queue 操作。UI 的服务状态只探测 localhost 已有组件；即使某个服务在线，页面也只展示状态，不触发远程操作。

shell 执行边界：前端不会执行 shell。`JobSpec.command` 只作为字符串展示在 inspector 中。`extractJobSpec` 只解析包含 `dry_run: true` 的文本块，并把字段渲染成表格；它不会把 command 传给 `fetch`、`eval`、`Function` 或任何执行入口。

Code Agent 写权限边界：v0.5 没有改变 Code Agent。Code Agent 仍然默认只读，可以解释代码、搜索项目、提出 patch 建议，但不会自动应用 patch。UI 只把 Code Agent 作为服务状态项展示，不提供“一键应用 patch”或“一键运行命令”按钮。

产品边界：这次完成的是演示型工作台，不是完整实验管理系统。它能让用户看清规划结构和 dry-run 边界，但还没有真实 artifact 浏览、图像可视化、日志上传、权限控制或审计链路。那些属于后续 Lab Code Adapter 和 Server Backend 阶段。

## 5. 调试或 TDD 证据

本轮遵循了测试先行。先扩展 `tests/check_web_branding.cmake`，要求页面必须包含 `algorithmList`、`experimentHistory`、`workbenchInspector`、`Route Trace`、`Tool Calls`、`AlgorithmCard`、`ExperimentSpec`、`JobSpec`、`Parameter Table`、`dry_run: true`、服务状态 id、三个算法 id，以及 `renderExperimentSpec`、`renderJobSpec`、`extractExperimentSpec`、`extractJobSpec`、`updateInspectorFromAnswer` 等关键函数。

第一次运行：

```bash
cmake --build build -j2
ctest --test-dir build -R WebBrandingTest --output-on-failure
```

预期失败发生在 `Missing required Lab Workbench UI element or helper: id="algorithmList"`。这个失败说明测试不是空转，它确实能抓到 v0.5 还没有实现的左侧算法面板。

实现阶段只做最小闭环：补齐静态工作台结构、种子 AlgorithmCards、默认 dry-run 实验规格、默认 JobSpec、右侧 inspector、解析函数和状态检查。然后补充 v0.5 报告与升级文档，使测试从“缺 UI”和“缺报告”转为通过。

最终验证证明三件事。第一，`WebBrandingTest` 通过，说明静态 UI 和报告结构都满足 v0.5 合同。第二，`python3 web/serve.py 18080` 加 `curl http://localhost:18080/` 通过，说明页面能被现有静态服务器托管，并能在本地服务路径下返回预期内容。第三，完整 CTest 通过，说明 UI 静态改动没有破坏现有 C++、A2A、MCP、RAG、Planner、ResearchKnowledge 等测试。

## 6. 面试怎么讲

项目短 pitch 可以这样说：我做的是一个 FWI-first 的科研计算多 Agent 工作台。后端有 C++、gRPC、A2A、MCP、RAG、Redis memory 和结构化实验规划；前端在 v0.5 从普通聊天页升级为 Lab Agent Workbench，可以把算法卡片、路由链路、工具调用、参数表、ExperimentSpec、JobSpec 和 dry-run 状态拆开展示，帮助实验室新成员理解一个计算实验是怎么被规划出来的。

技术深挖版可以这样展开：v0.4 已经把 planner 输出结构化，但用户仍然只能看到一段文本。v0.5 做的是 presentation layer 的结构化映射。左侧展示 AlgorithmCards 和实验历史，中间保留 HTTP/gRPC 对话，右侧 inspector 监听 assistant 内容并解析 fenced JSON/YAML，把 `ExperimentSpec` 和 `JobSpec` 显示成表格。这个方案没有新增后端，也没有执行任何 job，适合在安全边界未稳定前做产品验证。

常见追问一：为什么不用 React 或 Vue？回答：当前仓库没有前端构建链，Web UI 是单文件静态页面。v0.5 的目标是把研究对象可视化，而不是迁移前端架构。用原生 JS 可以最小化依赖，保持启动脚本不变，并用 CMake 静态合同测试保护关键 DOM 和函数。等 UI 复杂度继续上升，再引入模块化前端会更有依据。

常见追问二：UI 解析 LLM 文本靠谱吗？回答：这不是最终数据契约，只是 v0.5 的渐进方案。后端已经有 `PlannerAnswer`、`ExperimentSpec` 和 `JobSpec` 模型，未来可以直接让 API 返回结构化字段。当前解析 fenced JSON/YAML 的好处是不用改协议也能把已有 Planner 输出可视化，缺点是对文本格式有要求，所以测试锁定了最关键的解析函数和 dry-run 标记。

常见追问三：怎么保证不会误执行作业？回答：执行边界在多层同时限制。研究模型只允许 dry-run backend；DryRunBackend 只 render 不执行；UI 只展示 command 字符串，不提供运行按钮；没有 SSH/Slurm/PBS/remote endpoint；Code Agent 仍然只读。也就是说，即使用户看到 command，它也只是 JobSpec 的预览字段。

常见追问四：这个 UI 和普通 chatbot 最大区别是什么？回答：普通 chatbot 只关注文本回答，Lab Workbench 关注科研计算对象。用户能看到 agent routing、tool evidence、algorithm metadata、parameter table、experiment specification、job preview 和 service health。这让回答从“听起来合理”变成“可以被检查、复现和交接”。

STAR 复盘可以这样讲。Situation：项目已有多 Agent 和 FWI planner，但 Web UI 仍像聊天 demo。Task：把 v0.5 做成可演示的科研工作台，同时不能接真实 CUDA/MPI 或集群执行。Action：我先写静态 UI 合同测试让缺失的 panel 失败，再实现左侧算法/历史、右侧 inspector、Spec/JobSpec 渲染、状态检查和 dry-run 标记，并更新测试报告与升级文档。Result：页面能展示完整 dry-run 实验计划，完整 CTest 通过，项目叙事从“能聊天”推进到“能检查实验规划过程”，同时没有扩大执行风险。
