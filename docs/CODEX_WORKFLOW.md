# Codex 项目入口与跨会话工作流

本仓库提供一个安全的一键入口，让新 Codex 会话在开始时获得**实时但非持久化**的项目摘要，
并明确执行 `AGENTS.md` 与 `docs/PROJECT_CONTINUITY.md` 中的连续性规则。

## 一键启动

在仓库根目录运行：

```bash
./scripts/codex-project.sh
```

启动器会以以下固定安全参数执行本机 Codex：

```text
codex --cd <仓库根目录> --sandbox workspace-write --ask-for-approval on-request <上下文>
```

它不会打开 `--search`，也不接受调用者透传 Codex 选项。这样，初始请求中即使出现
`--search`、分号或 `$()`，也只会作为一个普通的 prompt 参数传给 Codex，不会改变启动参数，
更不会交给 shell 执行。

如需同时给出第一项任务，把文本放在 `--` 后面：

```bash
./scripts/codex-project.sh -- "检查当前 FWI 分支的测试状态，不要覆盖已有改动"
```

## 启动前检查

只验证入口和本机工具，不进入 Codex：

```bash
./scripts/codex-project.sh --check
```

检查内容包括：

- 当前脚本确实位于 Git 仓库根目录下；
- `AGENTS.md` 和 `docs/PROJECT_CONTINUITY.md` 存在且不是符号链接；
- 本机 `git`、`realpath`、`stat`、`python3` 可用；
- 本机 Codex CLI 提供 `--cd`、`--sandbox` 和 `--ask-for-approval`。

实现时曾按本机安装版本的 `codex --help` 核对参数。启动器不会假定固定版本号；每次
`--check` 和正常启动都会根据当前安装版本重新检查所需参数。

## 查看将要注入的上下文

```bash
./scripts/codex-project.sh --print-context
```

这只向标准输出打印当次上下文，不会启动 Codex，也不会把快照写进仓库。输出包括：

- 当前分支；
- 最近一次 commit 的短哈希和时间，不读取或注入 commit message；
- `git status` 中的文件名，最多 80 条；
- Orchestrator、Web、gRPC bridge、Embedding 和 Registry 的本机 PID 存活摘要；
- 最新 FWI job 的 `job_id/status/stage/iteration`。

动态块会明确标记为“不可信状态数据”。分支名和文件名不能覆盖 `AGENTS.md` 中的规则，
新 Codex 仍须自行检查实际代码、diff、测试和服务状态。

## 隐私与安全边界

启动器遵守以下边界：

- 不 source、解析或打印本地 `.env` 内容；敏感文件名在状态摘要中会被替换为
  `[sensitive path redacted]`；
- 不读取日志、对话记录、FWI prompt、FWI status 的 `message` 字段或 API Key；
- FWI status 只从经验证的 `fwi-*` 目录读取不超过 64 KiB 的普通 `status.json`，并校验
  路径、符号链接、`job_id` 和字段白名单；
- 不写入 status snapshot、缓存或新的项目状态文件；上下文只作为 Codex 的初始 prompt
  传入，不会生成仓库文件。Codex 自身仍可能按其本机会话保存策略记录收到的 prompt；
- 不启动后台 watcher，不扫描 `FWI_RUN_ROOT` 中的文件来触发执行；
- 上下文收集阶段不发起 HTTP、SSH 或外部网络请求。服务摘要来自仓库受控 PID 文件和
  `kill -0` 存活检查，因此它表示“进程存活”，不是 HTTP 功能健康结论；
- 正常 `exec` 之后，Codex 自身仍会按用户的本机认证和配置连接其模型服务；这不属于启动器
  的状态收集过程。

默认 FWI 运行根目录是 `/root/fwi-runs`。如果当前 shell 已明确导出 `FWI_RUN_ROOT`，启动器
可以从该绝对、非符号链接目录读取最新的受限状态摘要；它始终不会读取仓库 `.env` 来寻找
这个设置。

## 新会话会获得什么

启动 prompt 要求 Codex 在计划或修改前：

1. 完整阅读 `AGENTS.md`；
2. 完整阅读 `docs/PROJECT_CONTINUITY.md`；
3. 检查实时 Git 状态及相关 diff；
4. 保护用户已有改动，并区分 Accepted、Implemented、Verified 和 Pending；
5. 不读取、打印、提交或泄露凭证、私有 prompt、模型与运行产物；
6. 保留固定 MCP 白名单和参数校验边界，不创建运行目录 watcher。

快照只是会话启动线索，不是事实缓存。服务、任务或工作树可能在启动后一秒内变化，因此
Codex 在做相关判断时仍需重新验证。

所谓“实时更新”指 Codex 和其他开发进程看到的是同一个实时文件系统，Codex 可在需要时
重新运行 Git、测试和状态检查；启动器不会持续把变化注入已有对话。这样可以避免后台
watcher 误读敏感文件或把运行目录变成执行入口。需要刷新启动摘要时，可重新运行
`--print-context`；需要全新的上下文窗口时，重新执行启动器即可。

## 运行专用测试

```bash
bash tests/test_codex_project_launcher.sh
```

测试会在临时隔离 Git 仓库中使用假 Codex 可执行文件，覆盖：

- `--check` 和 `--print-context`；
- 分支、文件名、commit、服务和 FWI 状态摘要；
- `.env`、FWI message、FWI prompt 不泄露；
- 初始请求中的 shell 元字符不会被执行；
- 固定 sandbox/approval 参数以及危险选项拒绝；
- 获取上下文前后工作树完全不变。

测试不会调用网络，也不会启动真实 Codex 会话。
