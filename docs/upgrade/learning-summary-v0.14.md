# v0.14 Approved Template Run Packet 学习总结

日期：2026-06-23

## 解决的问题

v0.10 已经有 single-server profile/template/review request 和 dry-run review packet。
v0.12 有 fake lifecycle，v0.13 有 workspace planner。但这些能力还没有被组合成一个
面向未来运行的统一 packet：用户还不能在一个文本里看到“哪个 approved template、哪些
结构化参数、哪个 workspace preview、哪个 lifecycle id、哪些资源上限会一起形成一次
future run”。

如果这一步直接交给 runner 来做，风险会很高。runner 很容易同时承担参数解释、路径组合、
命令拼接、凭据读取和服务器连接。v0.14 的目标是把 runner 之前的 review packet 先固定下来：

- 用户只能选择 approved template。
- 用户只能提交结构化参数。
- 未批准参数会被拒绝。
- 自由 command 会被拒绝，并且不会被渲染。
- packet 明确说明执行、凭据读取、服务器连接和 workspace 创建都关闭。

这样后续 v0.15 设计 sanity-check runner gate 时，可以基于一个已测试的 review packet，
而不是重新从自由文本里推断执行意图。

## 实现方式

新增模块：

- `research/include/agent_rpc/research/approved_template_run_packet.h`
- `research/src/approved_template_run_packet.cpp`
- `tests/test_approved_template_run_packet.cpp`

核心类型：

- `ApprovedTemplateRunPacketRequest`：组合 `SingleServerProfile`、
  `SingleServerJobTemplate`、`SingleServerReviewRequest`、`WorkspacePlan`、
  `lifecycle_id`、必填参数列表和被拒绝的 `free_form_command` 尝试。
- `ApprovedTemplateRunPacket`：输出原始 request、`accepted_parameters`、
  `validation_errors` 和一组非执行 flags。

核心函数：

- `make_approved_template_run_packet`：汇总 profile/template/review request/workspace
  validation，检查必填参数和自由 command，并只把 allowlist 参数放入
  `accepted_parameters`。
- `render_approved_template_run_packet`：渲染 review packet，展示 fixed entrypoint、
  structured parameters、workspace preview、artifact preview、resource limits、
  lifecycle id 和安全 flags。

数据流：

```text
SingleServerProfile
SingleServerJobTemplate
SingleServerReviewRequest
WorkspacePlan
  -> ApprovedTemplateRunPacketRequest
  -> make_approved_template_run_packet
  -> ApprovedTemplateRunPacket
  -> render_approved_template_run_packet
```

这个数据流没有执行节点。它不把用户文本变成 shell，不读取凭据引用，也不访问真实服务器。

## 设计取舍

为什么不生成 shell command？

v0.14 的目标是 review packet，不是 runner。即使 template 有固定 entrypoint，当前阶段也只
渲染 `entrypoint_label`，不渲染可执行命令。这样可以避免把 review 文本误当作可复制执行的
脚本，也避免提前引入 shell escaping、参数拼接和环境变量处理。

为什么保留 `free_form_command` 字段但永远拒绝？

测试需要证明“如果上层或未来 UI 误传了自由 command，模块会拒绝它”。因此 request 里有一个
显式的拒绝入口，但 packet 不接受它：`free_form_command_accepted` 永远是 `false`，validation
会产生错误，renderer 不打印命令内容。

为什么渲染 `accepted_parameters` 而不是原始 parameters？

原始 parameters 可能包含 `extra_flags=--unsafe` 这类未批准输入。renderer 如果原样打印，就会
把危险内容带进 review packet，容易被用户复制或误读。v0.14 只渲染 allowlist 参数；未批准
参数只以 validation error 的参数名出现，不打印参数值。

为什么不读取 credential reference？

`SingleServerProfile` 里保存的是引用名，不是真实 secret。v0.14 不调用 secret manager、不读
环境变量、不打开任何凭据文件。renderer 也不打印 credential reference，避免 review 文本里
出现凭据路径或 secret 名称。

## 测试保护点

`ApprovedTemplateRunPacketTest` 覆盖：

- 正常 packet 渲染：确认 request/user/profile/template/entrypoint/lifecycle/workspace/
  artifacts/resource limits 都能出现在 review packet 中。
- 非执行 flags：确认 execution、command、credentials、server、workspace、directory、
  file movement 和 free-form command 全部保持 false/disabled。
- 凭据不渲染：确认 `secret-ref:single-server-runner` 不出现在输出里。
- 未批准参数拒绝：`extra_flags=--unsafe` 产生 validation error，不进入
  `accepted_parameters`，也不进入渲染文本。
- 自由 command 拒绝：`mpirun -np 8 ./fwi --unsafe` 产生 validation error，命令文本不渲染。
- 必填参数缺失：缺少 `niter` 会被拒绝。
- profile/template mismatch：template 指向其他 profile 会被拒绝。
- workspace plan error：危险 workspace root 的错误会传递到 run packet。

TDD 证据是先看到缺少 header 的编译失败，再实现 header/source/CMake，最后看到聚焦测试通过。

## 安全边界

v0.14 仍然不做：

- 不执行真实 CUDA/MPI。
- 不执行 shell，不调用 `mpirun`、`srun` 或任何 local wrapper。
- 不连接 SSH、Slurm、PBS 或远程服务器。
- 不读取密码、token、私钥、secret manager 或 `.env` 凭据。
- 不创建 workspace、目录或文件。
- 不删除或移动任何文件。
- 不采集真实日志或 artifact。
- 不改变运行时 backend guard。

因此 v0.14 可以用于 UI/operator review，但不能声称系统已经能运行实验。

## 面试准备

项目一句话：

我在一个 C++ 科研计算 multi-agent 平台里实现了 approved-template run packet，
把单服务器 profile、approved template、结构化参数、workspace preview 和 lifecycle id
组合成可审查文本，同时明确拒绝自由命令、未批准参数、凭据读取和服务器连接。

技术深挖：

这个模块的关键是把“用户意图”和“可执行命令”隔离开。用户请求先进入
`SingleServerReviewRequest`，template 提供 allowlist 和 fixed entrypoint label，
workspace planner 提供 preview path，`ApprovedTemplateRunPacket` 只输出批准后的结构化参数
和非执行 flags。自由 command 即使被传入，也只产生 validation error，不会进入渲染文本。

常见追问：

问：为什么不直接从 template 渲染命令？

答：因为 v0.14 是执行前的 review packet。真实执行需要 runner gate、timeout、stdout/stderr
capture、artifact path、审计和操作员控制。提前渲染 shell command 会让 review 层承担执行层
职责，也容易制造命令注入风险。

问：如果用户提供了 `extra_flags=--unsafe` 怎么办？

答：参数名不在 template allowlist 中时，packet 记录 validation error，并且 renderer 只打印
`accepted_parameters`，所以 `--unsafe` 不会出现在 review packet 中。

问：这个功能是否已经能连接服务器？

答：不能。`server_connected: false`、`credentials_loaded: false` 和
`command_executed: false` 是核心行为。服务器连接要等后续 runner gate 和真实后端批准。

STAR 示例：

情境：项目已经有 profile/template、fake lifecycle 和 workspace planner，但还缺一个统一的
future-run review packet。

任务：实现 v0.14，让用户可以审查 approved template 将如何组合结构化参数和 workspace preview，
同时不执行任何命令。

行动：用 TDD 先写 `ApprovedTemplateRunPacketTest`，覆盖正常渲染、未批准参数、自由 command、
缺失必填参数、profile/template mismatch 和 workspace validation error；确认缺少 header 的
RED 构建失败后，再实现 header/source/CMake 和 renderer。

结果：新增 `ApprovedTemplateRunPacketTest` 进入全量 CTest。系统现在能生成 non-executing
run packet，明确拒绝自由 command 和未批准参数，为 v0.15 fixed sanity-check runner gate
提供了安全输入边界。
