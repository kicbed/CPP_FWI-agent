# v0.15 Internal Sanity-Check Runner Gate 学习总结

> 历史版本说明：本文只描述 2026-06 的 v0.15 通用 runner gate。当前仓库另有固定
> 白名单 Deepwave CPU/单 GPU FWI 执行路径；通用 JobBackend 仍保持本文所述的非执行
> 边界。当前能力以 [根 README](../../README.md) 为准。

日期：2026-06-23

## 解决的问题

v0.14 已经能把 approved template、结构化参数、workspace plan 和 lifecycle id 合成
non-executing run packet。但如果下一步直接启用 runner，会有几个风险：

- 用户可能把自由 command 注入到 runner。
- runner 可能绕过 approved template，直接拼 shell。
- 未来为了调试方便，可能临时读取凭据或连接 SSH。
- stdout/stderr、timeout、artifact path 和 audit event 如果没有提前设计，真实执行后
  很难补齐可观测性和追责边界。

v0.15 的目标是先定义最小 runner gate：只有固定 allowlisted runner id，只有固定
developer-maintained entrypoint label，只有 review metadata，没有真实执行。

## 实现方式

新增模块：

- `research/include/agent_rpc/research/internal_sanity_runner.h`
- `research/src/internal_sanity_runner.cpp`
- `tests/test_internal_sanity_runner.cpp`

核心类型：

- `SanityRunnerDefinition`：描述 allowlisted runner id、展示名、固定 entrypoint label、
  timeout、stdout/stderr capture plan 和 expected artifacts。
- `SanityRunnerRequest`：描述 request id、user id、runner id、workspace plan id、
  workspace root、planned artifact paths，以及必须拒绝的危险请求字段。
- `SanityRunnerReviewPacket`：输出 request、匹配到的 definition、validation errors、
  artifact path plan、audit event type 和所有非执行 flags。

核心函数：

- `make_sanity_runner_review_packet`：匹配 allowlisted runner id，校验 timeout、
  capture plan、artifact path、自由 command、删除请求、凭据读取和后端连接请求。
- `render_sanity_runner_review_packet`：渲染 review packet，但不渲染用户自由 command。

数据流：

```text
SanityRunnerDefinition allowlist
SanityRunnerRequest
  -> make_sanity_runner_review_packet
  -> SanityRunnerReviewPacket
  -> render_sanity_runner_review_packet
```

这个数据流没有执行节点。它不启动进程，不访问 filesystem，不读取 secret，不连接服务器。

## 设计取舍

为什么用 runner id 而不是 command？

runner id 可以被 allowlist 精确控制。command 是开放字符串，即使第一版只用于 sanity check，
也会很快变成临时调试入口。v0.15 把用户输入限制为 `runner_id` 和结构化 metadata，未来真实
执行只能由开发者维护的映射表决定。

为什么保留危险请求字段？

`free_form_command`、`deletion_requested`、`credential_read_requested`、`ssh_requested`、
`slurm_requested`、`pbs_requested` 和 `remote_server_access_requested` 都是显式拒绝入口。
这样测试能证明：即使上层 UI 或未来集成误传了这些字段，gate 也会拒绝，而不是忽略后误执行。

为什么 artifact path 只做字符串校验？

当前阶段不能访问真实服务器或创建 workspace，所以只能做 preview-level validation。字符串校验
能先挡住明显的 workspace root 逃逸和 `..` traversal。未来真实执行前，还需要在实际 filesystem
上做 canonical path 校验和权限检查。

为什么 stdout/stderr 只是 planned？

因为当前没有进程执行，也就没有真实 stdout/stderr。v0.15 只要求 runner definition 必须声明
capture plan，确保后续启用真实 runner 时不会忘记日志边界。

## 测试保护点

`InternalSanityRunnerTest` 覆盖：

- 正常 allowlisted runner review：确认 timeout、stdout/stderr capture、artifact path、
  audit event 和非执行 flags 都进入 packet。
- unknown runner id：确认未在 allowlist 中的 runner 被拒绝。
- 自由 command 和危险操作：确认自由 command、删除、凭据读取、SSH、Slurm、PBS、remote
  server access 都被拒绝，renderer 不打印 `rm -rf` 之类的用户命令。
- artifact path safety：确认 artifact path 必须在 workspace root 下，且不能包含 traversal。
- definition gate metadata：确认 timeout 必须为正数，stdout/stderr capture plan 必须存在。

TDD 证据是先看到缺少 `internal_sanity_runner.h` 的编译失败，再实现 header/source/CMake，
最后看到聚焦测试通过。

## 安全边界

v0.15 仍然不做：

- v0.15 通用 runner gate 本身不执行 CUDA/MPI；这不描述后来加入的固定 Deepwave FWI 路径。
- 不执行 shell，不调用 `mpirun`、`srun` 或任何 local wrapper。
- 不连接 SSH、Slurm、PBS 或远程服务器。
- 不读取密码、token、私钥、secret manager 或 `.env` 凭据。
- 不创建 workspace、目录或文件。
- 不删除或移动任何文件。
- 不采集真实日志或 artifact。
- 不改变运行时 backend guard。

因此 v0.15 是 execution gate 的 metadata foundation，不是 execution feature。

## 面试准备

项目一句话：

我在一个 C++ 科研计算 multi-agent 平台里实现了 internal sanity-check runner gate，
用固定 allowlisted runner id、timeout、stdout/stderr capture plan、artifact path plan
和 audit event metadata 约束未来最小执行入口，同时继续拒绝自由 shell、删除、凭据读取和
SSH/Slurm/PBS。

技术深挖：

这个模块把“选择哪个固定 sanity check”和“执行命令”分开。用户只能传 `runner_id`；
系统用 allowlist 找到 `SanityRunnerDefinition`，然后生成 review packet。即使 request 里
出现自由 command 或后端连接请求，也只会进入 validation errors，renderer 不打印命令文本。

常见追问：

问：固定 runner id 为什么比 shell 安全？

答：runner id 的含义由开发者维护的 allowlist 决定，用户不能改变 entrypoint。shell command
则把解释权交给用户输入，需要复杂 escaping 和权限控制，第一版很容易退化成任意执行入口。

问：这个功能是否已经能执行 sanity check？

答：不能。当前只有 review metadata，`execution_enabled: false` 和
`command_executed: false` 是核心行为。真实执行需要后续单独实现固定 runner 映射、进程隔离、
真实路径校验、日志采集、timeout enforcement 和审计持久化。

问：为什么测试里要放 `rm -rf`？

答：不是为了执行它，而是为了证明 renderer 不会把危险用户 command 带入 review packet。
这能防止用户复制 packet 内容误执行，也能防止未来 UI 把拒绝字段展示成操作建议。

STAR 示例：

情境：项目已经有 approved-template run packet，但还没有定义任何 runner gate。

任务：实现 v0.15，让系统能审查固定 sanity-check runner 的 metadata，同时不启用真实执行。

行动：用 TDD 先写 `InternalSanityRunnerTest`，覆盖 allowlisted runner、未知 runner、
自由 command、删除、凭据读取、SSH/Slurm/PBS、artifact path 逃逸和 timeout/capture metadata；
确认缺少 header 的 RED 构建失败后，再实现 header/source/CMake 和 renderer。

结果：新增 `InternalSanityRunnerTest` 进入全量 CTest。系统现在能生成 non-executing
sanity runner review packet，为后续 v1.0 internal preview closeout 提供了固定 runner gate
说明，同时没有新增任何真实执行能力。
