# v0.9 后端就绪评审测试报告

日期：2026-06-22

状态：v0.9 非执行就绪/评审范围已完成。

## 范围

v0.9 把 M11 预检 metadata 转换成 operator 可以评审的文本预览，但不连接任何真实执行后端。

已完成能力：

- 将 `BackendPreflightReport` 渲染成面向 operator 的就绪状态文本。
- 从 `BackendPreflightPackage` 渲染 dry-run 提交包。
- 渲染同一个 job 的审计日志预览，但不做持久化。
- 渲染 workspace 和 artifact 路径计划，但不创建目录。

明确不包含：

- 真实 CUDA/MPI 执行。
- SSH、Slurm、PBS、本地 wrapper 或远程服务器执行。
- 凭据读取或集群账号处理。
- 写入生产审计存储。
- 从用户文本执行任意 shell 命令。
- Code Agent 自动应用 patch。

## TDD 证据

RED 检查：

- 为就绪报告渲染器添加测试后，`cmake --build build -j2` 失败，因为
  `render_backend_preflight_report` 还不存在。
- 为剩余 v0.9 预览功能添加测试后，`cmake --build build -j2` 失败，因为
  `render_dry_run_submission_packet`、`render_job_audit_log_preview` 和
  `render_workspace_artifact_plan` 还不存在。

GREEN 检查：

- 增加非执行预览 helper 后，
  `ctest --test-dir build -R ServerJobTest --output-on-failure` 通过。

## 最终验证

命令：

```bash
cmake --build build -j2
ctest --test-dir build -R ServerJobTest --output-on-failure
ctest --test-dir build --output-on-failure
git diff --check
```

结果：

- PASS。`cmake --build build -j2` 退出码为 0。
- PASS。`ServerJobTest` 通过。
- PASS。全量 `ctest --test-dir build --output-on-failure` 通过 26/26 个测试。
- PASS。`git diff --check` 没有输出。

## 安全结果

v0.9 只是评审层。运行时后端守卫没有变化：

- `local`、`ssh`、`slurm` 和 `pbs` 仍然会被拒绝。
- 只有后续 M11 真实后端决策和实现，在实验室批准与安全控制完成后，才能显式改变该行为。
