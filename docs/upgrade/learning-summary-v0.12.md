# v0.12 Fake Lifecycle 学习总结

日期：2026-06-23

## 解决的问题

v0.10 已经能生成单服务器 dry-run review packet，v0.11 增加了内部角色和删除
dry-run review packet。但在这两个版本之后，用户仍然只能看到“请求被渲染出来了”，
看不到一个实验请求从 review 到 approved、queued、running、finished 的过程。

如果直接接服务器或调度器，会过早引入 SSH、Slurm/PBS、CUDA/MPI、凭据、workspace
创建、日志采集和失败恢复等复杂风险。v0.12 的目标是先把 lifecycle 的产品体验和状态
边界做出来：让用户能看到 requested、reviewed、approved、rejected、queued、running、
succeeded、failed、cancelled，但这些状态只存在于 C++ metadata 里。

这一步的价值是把“状态流”从“真实执行”中拆出来。后续即使接入真实 runner，也可以复用
同一套状态名、transition 规则和 preview 语义，而不是在接服务器时临时发明状态。

## 实现方式

新增模块：

- `research/include/agent_rpc/research/single_server_lifecycle.h`
- `research/src/single_server_lifecycle.cpp`
- `tests/test_single_server_lifecycle.cpp`

核心类型：

- `SingleServerLifecycleState`：枚举全部 fake lifecycle 状态。
- `SingleServerLifecycleEvent`：记录某次状态变化的状态、消息和时间戳。
- `SingleServerLifecycleRecord`：记录 job id、request id、user id、template id、
  当前状态、事件历史，以及 `server_connected`、`command_executed`、
  `workspace_created` 三个非执行标志。

核心函数：

- `parse_single_server_lifecycle_state`：把稳定字符串解析为 enum。
- `to_string`：把 enum 渲染回稳定字符串。
- `make_single_server_lifecycle_record`：创建默认 `requested` record。
- `append_single_server_lifecycle_event`：只在内存中追加 event 和更新状态。
- `render_single_server_lifecycle_preview`：生成给人看的 fake lifecycle preview。

允许的状态流：

```text
requested -> reviewed | rejected
reviewed -> approved | rejected
approved -> queued | cancelled
queued -> running | cancelled
running -> succeeded | failed | cancelled
```

`rejected`、`succeeded`、`failed` 和 `cancelled` 是终态，后面不能再转到其他状态。
这能避免已经失败或取消的请求被假装继续运行。

## 设计取舍

为什么不复用 v0.8 的 `JobLifecycleState`？

v0.8 的 `JobRecord` 是通用 server backend safety model，状态包含 `Draft`、
`Submitted` 等未来后端语义。v0.12 面向单服务器 internal preview，状态需要从用户
可理解的 review 流程开始：`requested`、`reviewed`、`approved`。单独建
`single_server_lifecycle` 模块能避免把通用后端模型提前绑死在当前内部预览流程上。

为什么 renderer 不是执行器？

本阶段 renderer 只输出文本 preview。`queued` 和 `running` 只是 fake 状态，不代表
真实队列或真实进程。renderer 会显式输出：

- `server_connected: false`
- `command_executed: false`
- `workspace_created: false`

这些字段让演示、学习和后续 UI 集成时不会把 fake lifecycle 误解成真实执行。

为什么要显示 `allowed_next_states`？

如果用户看到当前状态是 `reviewed`，下一步应该知道只能走 `approved` 或 `rejected`。
这比只显示当前状态更接近真实产品体验，也能帮助后续 UI 做按钮启用/禁用逻辑。v0.12
只显示状态，不执行按钮动作。

## 测试保护点

`SingleServerLifecycleTest` 保护以下行为：

- 所有状态字符串稳定可解析。
- 新建 record 必须默认 `requested`。
- 新建和 append 后仍然不连接服务器、不执行命令、不创建 workspace。
- 成功状态流可以走到 `succeeded`。
- approved 状态可以被取消到 `cancelled`。
- rejected 终态不能跳到 `running`。
- preview 包含当前状态、下一状态列表、事件历史和安全边界。

TDD 过程中先看到缺少头文件、缺少 API、缺少 `allowed_next_states` 三类失败，再实现对应
代码。这样可以证明测试确实约束了新增能力，而不是事后补一个永远会通过的测试。

## 安全边界

v0.12 仍然不做：

- 不连接真实服务器。
- 不读取密码、token、私钥或 secret manager。
- 不创建 workspace、run directory、log directory 或 artifact directory。
- 不执行 shell、`mpirun`、`srun`、CUDA/MPI 程序或 fixed runner。
- 不提交 SSH、Slurm、PBS 或 local wrapper job。
- 不采集真实日志或 artifact。
- 不改变 dry-run-only backend guard。

这意味着 v0.12 可以放心用于 UI 状态演示、operator review 训练和内部流程讲解，但不能
声称已经能运行实验。

## 面试准备

项目一句话：

我在一个 C++ 多智能体科研计算平台里，先用 metadata-only 的方式实现了单服务器 job
生命周期状态机，让实验请求可以经过 requested、reviewed、approved、queued、running
和终态展示，同时保证没有任何服务器连接、命令执行或目录创建。

技术深挖：

这一步的关键不是状态枚举本身，而是把“生命周期可视化”和“真实执行”解耦。状态机只接收
结构化 enum，不接受用户 shell；append helper 只改内存 record；renderer 明确输出非执行
flags。这样后续接真实 runner 时，UI 和 review 流程已经稳定，执行层只需要在更严格的 gate
后把真实状态映射到同一套 metadata。

常见追问：

问：为什么不直接接 Slurm 或 SSH？

答：因为真实执行需要凭据、workspace、超时、日志、artifact、取消、审计和权限控制。
在这些边界没有实现前接 Slurm/SSH，会把用户输入、服务器账号和文件系统风险混在一起。
fake lifecycle 先验证产品流程和状态语义，降低后续执行层风险。

问：fake lifecycle 会不会误导用户以为任务真的运行了？

答：preview 明确显示 `server_connected: false`、`command_executed: false` 和
`workspace_created: false`。文档也写清楚 queued/running 只是模拟状态，不代表真实队列
或真实进程。

问：为什么要测试 invalid transition？

答：生命周期状态机最容易出错的地方是终态继续流转，比如 rejected 后又 running。
测试终态拒绝可以避免 UI 或 future adapter 把已经拒绝、失败或取消的请求重新推进。

STAR 示例：

情境：实验室内部预览需要展示一次实验请求的状态变化，但真实服务器执行还没有安全 gate。

任务：实现一个能被 UI 和 operator review 使用的 lifecycle 状态机，同时绝不连接服务器或
执行命令。

行动：用 TDD 新增 `single_server_lifecycle` 模块，先写失败测试，再实现状态枚举、record、
transition validation、event append 和 preview renderer，并用测试覆盖成功、取消和非法终态
转换。

结果：新增的 `SingleServerLifecycleTest` 进入全量测试套件，全量测试通过 29/29。项目现在
能演示单服务器 internal preview 的状态流，但真实执行能力仍被保留到后续安全 gate。
