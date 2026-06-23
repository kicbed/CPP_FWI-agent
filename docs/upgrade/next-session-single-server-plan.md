# 下一窗口交接：单服务器账号初步接入计划

日期：2026-06-23

用途：下一次新开窗口时，把这份文档作为主要任务说明，让 Codex 继续推进项目。

## 1. 当前判断：框架已经可以开始系统学习

是的，当前项目的基本框架已经搭起来了，适合开始系统学习和复盘。

已经具备的主线能力：

- 多智能体通信框架：gRPC、A2A 风格消息、agent registry、orchestrator。
- 研究计算领域模型：`AlgorithmCard`、`ExperimentSpec`、`JobSpec`、`DryRunBackend`。
- 知识库和规划层：结构化 research knowledge、PlannerContext、PlannerAnswer。
- Lab Workbench UI：能展示路由、工具调用、参数表、ExperimentSpec、JobSpec 和 dry-run 状态。
- Lab Code Adapter：能读配置模板、解析日志、抽取 loss curve、识别常见失败信号。
- 后端安全基础：`JobBackend` 接口、后端类型守卫、server job metadata、preflight report。
- 非执行评审层：后端就绪报告、dry-run 提交包、审计预览、workspace/artifact 计划。

当前还没有的能力：

- 没有真实 CUDA/MPI 执行。
- 没有 SSH、Slurm、PBS 或远程服务器提交。
- 没有真实凭据读取。
- 没有真正创建服务器 workspace。
- 没有生产审计存储。
- Code Agent 仍然只读，不会自动应用 patch。

所以现在最适合学习的是：先把“系统如何从问题变成 dry-run 实验计划”学透，再学“为什么
真实后端要慢慢接”。

## 2. 你的实验室场景简化版

当前实验室不是复杂多用户平台，而是初步阶段：

- 主要是自己或小组内部使用。
- 通常用一个服务器账号跑实验。
- 不需要一开始就做完整企业级权限系统。
- 不需要一开始就做复杂 quota、operator、生产审计系统。
- 目标是先跑通“可控模板 -> 单服务器账号 -> workspace -> 日志/artifact 回收”的最小闭环。

但是仍然保留三个底线：

1. 不把服务器密码、token、私钥、账号秘密写进仓库。
2. 不把用户自由文本直接拼成 shell 命令。
3. 不让 Code Agent 自动修改代码或自动执行未确认 patch。

这不是为了把项目搞复杂，而是为了避免最容易出事故的地方。

## 3. 单服务器账号最小后端目标

下一阶段可以把目标命名为：

> M11-S1 Single Server Controlled Runner Preparation

中文理解：单服务器账号受控运行准备阶段。

第一版不要直接做大而全的集群系统，只做这些：

- 一个固定服务器账号，由你自己或实验室确认。
- 一个固定 workspace root，例如 `/data/lab_agent_runs`，真实值不要写进公开仓库。
- 一个固定 approved job template，例如 FWI dry-run 或最小 sanity-check 脚本。
- 一组结构化参数，例如 dataset、config、niter、frequency band、gpu_count。
- 一个 dry-run submission packet，先展示将要做什么。
- 一个 fake/simulated backend 测试路径，先不连真实服务器也能测状态流。
- 后续再接真实服务器时，只让模板参数进入命令，不允许自由 command。

## 4. 下一窗口建议做的第一个小任务

下一窗口不要马上 SSH 到服务器，也不要跑真实作业。建议先做一个小任务：

> 新增单服务器账号接入设计文档和实现计划，明确第一版只做 metadata/profile/template，
> 不执行真实命令。

建议文件：

- `docs/upgrade/single-server-backend-v0.10.md`
- `docs/superpowers/plans/2026-06-23-single-server-backend-v0.10.md`

计划里第一批代码任务可以是：

- 新增 `SingleServerProfile` metadata 模型，记录 profile id、workspace root 引用、
  credential reference、allowed template ids。
- 新增 `SingleServerJobTemplate` metadata 模型，记录固定入口、允许参数和 artifact 约定。
- 新增测试：拒绝空 credential reference、拒绝 workspace path traversal、拒绝未知 template。
- 新增 renderer：只渲染 review packet，不执行命令。

这一步完成后，才考虑下一小步：fake backend lifecycle。

## 5. 新窗口可以直接复制的提示词

新开窗口后，可以直接复制下面这段：

```text
你现在在仓库 /root/projects/project/agent-communication-main-v2。

请先阅读：
- docs/upgrade/README.md
- docs/upgrade/milestones.md
- docs/upgrade/version-roadmap.md
- docs/upgrade/upgrade-log.md
- docs/upgrade/next-session-single-server-plan.md
- docs/upgrade/m11-lab-backend-decision-package.md
- docs/upgrade/m11-lab-process-guide.md

当前实验室场景先按简单单服务器账号处理：自己或小组用一个服务器账号跑实验。
不要一开始做复杂多用户平台、Slurm/PBS、SSH 真实连接或生产审计系统。

下一步请做一个小任务：
新增单服务器账号初步接入的设计文档和实现计划，范围只到 metadata/profile/template
和 dry-run review packet，不执行真实命令，不读取真实凭据，不连接服务器。

执行规则：
- 先运行 git status --short。
- 说明准备做的任务和验证命令。
- 每次只做一个小任务或一个紧密相关小批次。
- 代码改动至少跑 cmake --build build -j2 和 ctest --test-dir build --output-on-failure。
- 文档改动也跑 git diff --check。
- 更新 docs/upgrade/upgrade-log.md。
- 如新增架构/技术能力/学习价值，更新 docs/upgrade/career-notes.md。
- 提交到 git。
- 最后告诉我改了什么、测试结果、commit hash、下一步建议，并用中文写学习总结。

限制：
- 不接真实 CUDA/MPI。
- 不接真实 SSH、Slurm、PBS 或远程服务器。
- 不执行来自用户输入的任意 shell 命令。
- 不把服务器密码、token、私钥或账号秘密写入仓库。
- Code Agent 默认只读，允许生成 patch 建议，但不要自动应用未确认 patch。
```

## 6. 学习计划

建议按 4 周学习，不要一开始追所有细节。

### 第 1 周：理解项目主线

目标：能讲清楚“用户问题如何变成 dry-run 实验计划”。

重点读：

- `README.md`
- `docs/upgrade/README.md`
- `docs/upgrade/version-roadmap.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/test-report-v0.4.md`
- `docs/upgrade/learning-summary-v0.9.md`

要掌握：

- Orchestrator、Agent、Tool、Knowledge、Planner、Backend 的分层。
- `AlgorithmCard` 为什么把算法做成数据。
- `ExperimentSpec` 和 `JobSpec` 分别表达什么。
- `DryRunBackend` 为什么重要。

练习：

- 用 3 分钟讲清楚项目 pitch。
- 画一张数据流：用户请求 -> PlannerContext -> PlannerAnswer -> ExperimentSpec -> JobSpec。

### 第 2 周：理解测试和安全边界

目标：能看懂为什么现在还不能真实执行。

重点读：

- `docs/upgrade/test-report-m11-preflight.md`
- `docs/upgrade/m11-preflight-completion-audit.md`
- `docs/upgrade/m11-lab-backend-decision-package.md`
- `docs/upgrade/m11-lab-process-guide.md`

要掌握：

- `metadata_ready` 和 `runtime_enabled` 为什么要分开。
- 为什么不把用户文本变成 shell 命令。
- 为什么 credential reference 不能等于真实密码。
- workspace path traversal 是什么风险。

练习：

- 回答“为什么不直接接 Slurm/SSH？”
- 回答“如果只有一个服务器账号，为什么还要 approved template？”

### 第 3 周：理解 Lab Code Adapter 和实验诊断

目标：能讲清楚系统如何从日志和配置里给实验建议。

重点读：

- `docs/upgrade/test-report-v0.6.md`
- `research/include/agent_rpc/research/lab_code_adapter.h`
- `tests/test_lab_code_adapter.cpp`

要掌握：

- config template reader 做什么。
- loss curve parser 做什么。
- failure recognizer 如何识别 NaN、停滞、cycle skipping、资源问题。
- Planner-facing diagnostic summary 如何给规划器提供证据。

练习：

- 找一个测试，讲它保护了什么风险。
- 写一段中文解释：loss 不下降时系统如何判断可能原因。

### 第 4 周：准备单服务器账号最小闭环

目标：为下一阶段真实实验室使用做准备，但仍不贸然执行。

重点读：

- `docs/upgrade/next-session-single-server-plan.md`
- `docs/upgrade/m11-lab-process-guide.md`
- `research/include/agent_rpc/research/server_job.h`
- `tests/test_server_job.cpp`

要掌握：

- 单服务器账号场景下可以删掉哪些复杂度。
- 哪些底线不能删：凭据不入库、命令不自由拼接、workspace 不越界。
- 为什么第一步应该做 profile/template/review packet，而不是直接 SSH。

练习：

- 写出一个单服务器 profile 需要哪些字段。
- 写出一个 FWI job template 允许哪些参数、禁止哪些参数。

## 7. 面试和汇报讲法

短版：

这个项目的基础框架已经完成：它能把地震 FWI 研究问题转成结构化实验计划、dry-run
JobSpec、日志诊断和后端就绪评审。下一阶段不是直接接集群，而是先按实验室真实使用
方式做单服务器账号的受控接入准备。

技术版：

系统已经有算法元数据、实验规格、dry-run backend、知识库检索、PlannerAnswer、
Lab Code Adapter 和 server job preflight。现在缺的是从 dry-run 到真实服务器的最小
受控路径。对当前实验室来说，不需要一开始做复杂多租户权限系统，但仍要有固定 profile、
credential reference、workspace root、approved template 和 review packet，避免把用户
文本直接变成 shell 命令。

下一步工程策略：

先写 `SingleServerProfile` 和 `SingleServerJobTemplate` 这类 metadata，再做 dry-run
review packet 和 fake lifecycle 测试。等这些边界稳定后，才讨论真实服务器连接。
