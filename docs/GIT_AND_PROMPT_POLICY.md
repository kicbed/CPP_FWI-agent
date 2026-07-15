# Git 与 AI 提示词管理规则

<!-- git-prompt-policy: v1 -->

- Git 决策：`D-004` / **Accepted**
- Git 实现状态：**Verified — first SSH checkpoint pushed**
- 提示词提案：`D-005` / **Proposed — awaiting user confirmation**
- 记录日期：2026-07-15

`D-004` 只覆盖用户明确要求的 Git 分支与 checkpoint 管理。用户对 AI prompt
是否上传表达了倾向但未确认具体分类，因此第 4–5 节作为 `D-005` 提案，
不冒充已采纳决策。在用户确认前，本分支采用安全默认：不上传临时 prompt/原始
聊天，也不迁移或删除已有产品运行时 prompt。

## 1. 分支策略

- `feature/fwi-deepwave-2d-acoustic` 是已验证的 FWI MVP 基线，不在其上继续堆积下一代架构。
- `feature/scientific-agent-runtime` 是 D-003 的集成分支，基于 `ffeb5bc` 创建。
- 高风险或可独立并行的阶段可以从集成分支创建短期切片分支；不同写入 Agent 不得同时修改
  同一文件。合回前必须由主 Agent 检查完整 diff 和测试。
- 不自动修改或推送 `main`，不 force-push，不重写已经发布的功能分支历史，不使用
  `git reset --hard` 或 `git checkout --` 清理用户改动。
- 远端操作使用现有 SSH remote，不依赖 `gh`。

首个 checkpoint `b5ac633` 已推送到
`origin/feature/scientific-agent-runtime`；本地 branch/upstream 在推送后指向同一提交。

截至 2026-07-15 的现场快照显示，本地 `main` 相对 `origin/main` 为 ahead 57 / behind 2。
这是会过期的证据，每次操作前必须重查；D-003 开发不得因此自动切换、reset、rebase
或推送 `main`。

## 2. 提交 checkpoint

一个可提交切片至少满足：

1. 范围与当前阶段一致；
2. 已检查 `git status` 和完整相关 diff；
3. 相关测试通过，失败项如实记录；
4. `docs/PROJECT_PROGRESS.md` 已更新；
5. 没有 `.env`、密钥、模型、运行结果、日志、数据库、缓存、构建产物或虚拟环境；
6. 暂存使用明确文件清单，不使用未经复核的 `git add .`；
7. `git diff --cached --check`、暂存文件清单和敏感模式扫描通过。

推荐按可审阅的纵向切片提交，例如：

```text
docs: record scientific agent runtime architecture
feat(task-contracts): add versioned task schemas
feat(task-runtime): add durable task state transitions
feat(fwi-adapter): run FWI through the task kernel
feat(workbench): add task draft approval flow
```

不要为追求整齐而把未验证的大量改动压成一个提交，也不要提交纯生成物作为“进度备份”。

## 3. 推送规则

- 在用户已要求持续 Git 管理的 D-003 开发中，完成并验证的阶段 checkpoint 可以提交并通过
  SSH 推送当前功能分支。
- 推送前再次确认 branch、upstream、提交范围和远端 URL；推送后核对本地/远端 commit。
- 中途未完成且不安全的工作不为“备份”强行推送；需要跨会话续做时保留工作树并在进度账本
  标记真实状态，后续会话先保护这些改动。
- 合并到 `main`、删除远端分支、打发布 tag 或重写历史仍需用户明确指示。

## 4. AI 提示词分类（D-005 提案）

### 不提交：开发临时提示词

以下内容不属于产品源码：

- Codex/Claude 的一次性交接 prompt；
- 从聊天窗口复制的完整原始对话；
- 临时“下一窗口继续”文本；
- 含个人路径、内部状态、日志片段或未审核数据的探索性 prompt；
- 为某次调试临时写的模型指令。

统一放到仓库根目录 `.local-prompts/` 或使用 `*.local-prompt.md` 文件名。它们由
`.gitignore` 排除。跨会话所需事实应提炼进持续决策、架构计划和进度账本，而不是依赖原始
prompt。

### 建议提交：决定产品行为的运行时提示词

如果提示词会被 Orchestrator、Planner、Critic、Analyzer 或其他产品 Agent 在运行时加载，
它就是源码的一部分，必须：

- 放在明确的源码/资源目录并有稳定 ID 和版本；
- 不含 API Key、`.env`、凭证、个人信息或机器专属秘密；
- 明确角色、输入信任边界、允许工具、输出 Schema 和失败行为；
- 接受 code review、prompt injection/越权测试和变更记录；
- 与使用它的代码和测试在同一切片提交。

运行时提示词不能等到“全部开发完成后再删除”。删除文件不会清除 Git 历史，而且会破坏行为
复现。真正不应公开的临时 prompt 从一开始就不提交；真正决定产品行为的 prompt 应作为受审
源码保留。

## 5. 现有历史文件（D-005 获批后执行）

仓库已有一些旧的 prompt/next-session/local 命名文档。它们可能已经存在于 Git 历史中，
本次不做破坏性批量删除。如用户批准 D-005，P0 再按文件名和必要内容进行
一次独立审计：

- 有持续价值的内容提炼进正式计划/规范；
- 纯临时交接文件停止跟踪；
- 若发现真实秘密，先轮换对应凭据，再按用户批准的历史清理方案处理；
- 仅从最新提交删除文件不能视为已经从远端历史清除。

## 6. 禁止进入 Git 的内容

- `.env` 及环境专属变体；
- API Key、私钥、访问令牌和凭证文件；
- `/root/fwi-data`、`/root/fwi-runs`、模型和运行 artifacts；
- `build/`、CMake 生成物、`.so/.o/.a`、虚拟环境、Python/Node 缓存；
- Redis/SQLite 运行数据库、PID、日志和临时 checkpoint；
- `.local-prompts/` 和 `*.local-prompt.md`。

示例配置、公开 Schema、脱敏测试 fixture 和必要的运行时提示词可以提交，但必须通过同样的
diff 与敏感信息检查。
