# v0.13 Workspace Planner 学习总结

日期：2026-06-23

## 解决的问题

v0.12 已经能用 fake lifecycle 展示 requested、reviewed、approved、queued、
running、succeeded 等状态，但 lifecycle record 还没有明确指向“未来一次运行会把
workspace、日志和 artifact 放在哪里”。如果这一步直接留到真实 runner 里做，就会把
路径规划、目录创建、远程文件系统、权限、清理和 artifact 采集混在一起。

v0.13 的目标是先把路径计划单独拆出来。系统可以告诉用户：

- workspace preview 在哪里。
- run directory preview 在哪里。
- log path preview 在哪里。
- artifact path preview 在哪里。
- 哪些路径输入因为穿越、绝对路径逃逸、空 root 或保护标签而被拒绝。

这一步仍然不创建任何目录。它只是把“路径是否安全”和“目录是否真的存在”分开，避免未来
runner 一边建目录一边发现路径已经错了。

## 实现方式

新增模块：

- `research/include/agent_rpc/research/workspace_planner.h`
- `research/src/workspace_planner.cpp`
- `tests/test_workspace_planner.cpp`

核心类型：

- `WorkspacePlanRequest`：输入 request id、workspace root、job directory name、
  run directory name、log file name 和 artifact subdirectories。
- `WorkspacePlan`：输出 planned workspace path、planned run directory path、
  planned log path、planned artifact paths、validation errors，以及
  `directories_created`、`files_moved`、`server_connected` 三个非执行标志。

核心函数：

- `validate_workspace_plan_request`：做字符串级路径校验。
- `make_workspace_plan`：把 request 渲染成 preview-only plan。
- `render_workspace_plan_preview`：生成 operator/user 可读的 workspace plan preview。

数据流：

```text
WorkspacePlanRequest
  -> validate_workspace_plan_request
  -> make_workspace_plan
  -> WorkspacePlan
  -> render_workspace_plan_preview
```

这个数据流不依赖真实文件系统。`make_workspace_plan` 使用字符串 join 生成预览路径，
不会调用 `create_directory`、`remove`、`rename`、`directory_iterator` 或远程访问。

## 路径安全设计

本阶段采用保守的字符串级规则：

- `workspace_root` 必须非空并且是绝对路径。
- `workspace_root` 不能包含 `..`。
- `workspace_root` 不能是 `/`、`/root`、`/etc`、`/usr`、`/tmp` 等保护位置。
- `workspace_root` segment 不能包含 `.git`、`.ssh`、`secrets`、`credentials`、
  `repo`、`code`、`env`、`venv`、`shared_data` 等保护标签。
- `job_directory_name`、`run_directory_name`、`log_file_name` 和
  `artifact_subdirectories` 必须是单个相对组件。
- 相对组件不能为空、不能是绝对路径、不能包含 `..`、不能包含 `/` 或 `\`。
- 相对组件也不能使用保护标签。

为什么不做真实 canonical path？

真实 canonicalization 通常需要访问文件系统，可能触发不存在路径、symlink、权限或远程路径
问题。v0.13 的边界是“计划和校验”，不是“确认真实目录”。因此这里采用不触碰文件系统的
保守校验。后续如果要启用真实 runner，需要在更靠近执行层的位置再做真实路径解析、symlink
检查和权限检查。

为什么 artifact subdirectory 不允许嵌套路径？

第一版只允许单个相对组件，例如 `logs`、`artifacts`、`snapshots`。这样可以保证 preview
路径一定由 workspace root、job directory 和受控组件拼出。允许任意嵌套路径会提高表达能力，
但也会引入更多 traversal、重复 separator、隐藏目录和保护标签组合，当前 internal preview
不需要这部分复杂度。

## 测试保护点

`WorkspacePlannerTest` 覆盖：

- 正常请求能生成 workspace/run/log/artifact preview。
- preview 和 metadata 明确显示 `directories_created: false`、
  `files_moved: false`、`server_connected: false`。
- `../other` 被识别为 path traversal。
- 空 job directory 被拒绝，防止误把 workspace root 当作 job workspace。
- `/etc` 这类绝对 job directory 被拒绝。
- 空 root 和 `/` 这类危险 root 被拒绝。
- log file 和 artifact subdirectory 的 traversal/absolute escape 被拒绝。
- `secrets`、`env`、`shared_data` 等保护标签被拒绝。

TDD 过程中先看到缺少 header 的编译失败，再看到保护标签测试失败，然后才实现对应生产代码。
这证明测试确实约束了 v0.13 的行为。

## 安全边界

v0.13 仍然不做：

- 不创建目录。
- 不删除目录。
- 不移动文件。
- 不扫描真实目录。
- 不跟随 symlink。
- 不连接服务器。
- 不访问 SSH、Slurm、PBS 或远程文件系统。
- 不读取密码、token、私钥或 secret manager。
- 不执行 shell、CUDA/MPI 程序、`mpirun`、`srun` 或 fixed runner。

因此 v0.13 可以安全用于 review packet 和 UI preview，但不能声称 workspace 已经准备好。

## 面试准备

项目一句话：

我在一个 C++ 科研计算 multi-agent 平台里实现了 workspace planner，让系统能在不创建目录、
不连接服务器的前提下预览 future job 的 workspace、run、log 和 artifact 路径，并用测试覆盖
路径穿越、绝对路径逃逸和保护标签。

技术深挖：

这个模块的关键是把路径计划从真实执行里拆出来。`WorkspacePlanRequest` 只接收结构化字段，
不接收用户 shell；validation 先拒绝空 root、危险 root、`..`、绝对组件和保护标签；
`WorkspacePlan` 只保存 preview path 和非执行 flags。后续 approved-template run packet
可以引用 plan，但 runner 仍然需要另一个 gate 才能真实创建目录或执行命令。

常见追问：

问：为什么不用 `std::filesystem::canonical`？

答：`canonical` 需要访问真实文件系统，路径不存在或远程路径时会失败，也可能把 symlink
解析问题提前带进 metadata 层。v0.13 的目标是 preview 和输入校验，不触碰文件系统；真实
runner 以后再做执行层 canonicalization 和 symlink 检查。

问：为什么只允许单个相对组件？

答：第一版 internal preview 不需要复杂嵌套路径。单组件规则让所有生成路径都可预测地位于
workspace root/job directory 下面，降低 path traversal 和保护目录误用风险。

问：这个模块是否已经能创建 workspace？

答：不能。它只输出 preview，`directories_created` 永远是 `false`。创建目录属于后续 runner
gate，不属于 v0.13。

STAR 示例：

情境：项目需要在真实服务器执行前展示 future run 的 workspace/log/artifact 路径，但还没有
启用任何 runner。

任务：实现一个只做 preview 和路径校验的 workspace planner，防止路径逃逸和危险 root。

行动：用 TDD 新增 `WorkspacePlannerTest`，先让缺失 header 失败，再实现 request/plan/
validation/renderer；随后追加保护标签失败测试，再补上 `secrets`、`env`、`shared_data`
等标签校验。

结果：新增 `WorkspacePlannerTest` 进入全量 CTest，全量测试达到 30/30 通过。系统现在能为
v0.14 run packet 提供安全的路径 preview，同时仍不创建目录、不连接服务器、不执行命令。
