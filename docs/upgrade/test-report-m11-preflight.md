# M11 预检测试报告

日期：2026-06-22

## 范围

本报告记录 M11 metadata-only 预检阶段的测试结果。这里的“预检”指：先把未来真实后端
需要的审批、授权、workspace、模板和审计信息建成可测试模型，但不接真实服务器。

覆盖内容：

- 后端批准决策 metadata。
- 授权提交人 metadata。
- 仅 metadata 的作业审计事件和内存审计日志。
- 统一的后端预检就绪报告。
- 运行时守卫验证：真实后端值仍然保持禁用。

## 验证命令

TDD 红灯检查：

```bash
cmake --build build -j2 && ctest --test-dir build -R ServerJobTest --output-on-failure
```

实现前预期并观察到的结果：

- FAIL。编译期失败，因为 `BackendPreflightPackage`、
  `BackendPreflightReport` 和 `evaluate_backend_preflight` 还不存在。

目标绿灯检查：

```bash
cmake --build build -j2 && ctest --test-dir build -R ServerJobTest --output-on-failure
```

实现后观察到的结果：

- PASS。`ServerJobTest` 通过。

本阶段最终验证：

```bash
cmake --build build -j2
ctest --test-dir build --output-on-failure
git diff --check
```

观察到的结果：

- PASS。完整构建退出码为 0。
- PASS。全量 `ctest` 通过 26/26 个测试。
- PASS。`git diff --check` 没有输出。

## 这证明了什么

M11 预检层现在可以回答一个关键问题：未来真实后端需要的 metadata 包是否完整。
它会检查实验室批准 metadata、授权提交人、dry-run 提交边界、approved templates、
workspace 目录命名、审计事件 metadata，以及同一个 job 的审计日志分组。

这里故意把 `metadata_ready` 和 `runtime_enabled` 分开。一个完整的预检包可以做到
metadata ready，但运行时仍然拒绝 `local`、`ssh`、`slurm` 和 `pbs`。这是接真实
实验室后端之前应该处于的状态。

## 安全边界

本阶段没有新增真实 CUDA/MPI 执行。

本阶段没有新增 SSH、Slurm、PBS、远程服务器、本地 wrapper、凭据读取、调度器提交、
任意 shell 执行或 Code Agent 自动应用 patch。

`DryRunBackend` 仍然是唯一启用的运行时后端。预检报告只处理 metadata，不会提交作业。
