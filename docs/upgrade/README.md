# Lab Research Agent Upgrade Guide

This directory is the operating manual for upgrading this project into a
FWI-first research computing agent workbench.

Use it when starting a new upgrade session. The goal is to make each session
small, testable, and committed to git.

## Current Target

Upgrade the project from a rough multi-agent/FWI demo into:

> Lab Research Agent Platform for Seismic Computing

`v0.2 Lab Agent MVP` is complete as of 2026-06-11:

- Real Code Agent MVP.
- AlgorithmCard registry.
- ExperimentSpec and JobSpec data models.
- Dry-run experiment planning.
- Better README and roadmap.
- No real CUDA/MPI or cluster execution yet.

`v0.3 Research Knowledge Base` is complete as of 2026-06-12:

- JSON-backed paper, algorithm, experiment, and failure-case notes.
- Deterministic local loading and validation.
- Retrieval by note type, method, failure mode, parameter advice, and dataset.
- v0.3 test report and Chinese learning summary.

`v0.4 Experiment Planner` is complete as of 2026-06-12:

- Deterministic PlannerContext retrieval from user requests.
- Structured PlannerAnswer with algorithm recommendation, assumptions,
  parameter table, risk analysis, and next-step plan.
- ExperimentSpec JSON, dry-run JobSpec text, and reproducible experiment
  records grounded in AlgorithmCards and the v0.3 knowledge base.
- v0.4 test report and Chinese learning summary.

`v0.5 Lab Workbench UI` is complete as of 2026-06-12:

- Browser surface renamed to Lab Agent Workbench.
- Left-side sessions, AlgorithmCards, and experiment-history entry points.
- Center conversation surface preserved for HTTP and gRPC modes.
- Right-side inspector for route trace, tool calls, selected AlgorithmCard,
  parameter table, ExperimentSpec, JobSpec, dry-run artifacts, and service
  status.
- Static parsing helpers for ExperimentSpec JSON and dry-run JobSpec text.
- v0.5 smoke notes, screenshot path, test report, and Chinese learning summary.

`v0.6 Lab Code Adapter` is complete as of 2026-06-22:

- Config template reader and safe config generation.
- Log parser and loss curve extraction for supplied local text fixtures.
- Common failure recognizers for cycle skipping, stagnant loss, NaN/Inf, and
  resource-limit symptoms.
- Planner-facing diagnostic summaries that keep all execution dry-run only.
- v0.6 test report and Chinese learning summary.

`v0.7 JobBackend Reservation` is complete as of 2026-06-22:

- `JobBackend` interface with `validate`, `render`, `explain`, and backend type
  identity.
- Backend type enum values for `dry_run`, `local`, `ssh`, `slurm`, and `pbs`.
- Runtime guard that allows only `dry_run` and rejects all reserved or unknown
  backend values with clear messages.
- `AlgorithmCard` backend validation now uses the shared backend guard.
- v0.7 test report and Chinese learning summary.

`v0.8 Server Backend Safety Foundation` is complete as of 2026-06-22:

- Written safety design for auth, approved templates, workspace isolation, job
  lifecycle state, artifact collection, and audit logging.
- Server-job request and record models for future controlled execution.
- Approved job template validation.
- Workspace path traversal rejection.
- In-memory lifecycle record helpers.
- v0.8 test report and Chinese learning summary.
- Runtime remains dry-run only; non-`dry_run` backend values are still
  rejected.

Detailed learning note:

- `docs/upgrade/study-pack-v0.8.md`
- `docs/upgrade/learning-summary-v0.8.md`
- `docs/upgrade/v0.8-completion-audit.md`

`Milestone 11 preflight` is complete as of 2026-06-22:

- Metadata-only backend approval decision validation.
- Metadata-only job audit event model for future submission, rejection,
  lifecycle, artifact, and operator-note records.
- Metadata-only in-memory job audit log validation and append helpers for
  future audit persistence boundaries.
- Unified metadata-only backend preflight readiness report that separates
  metadata readiness from runtime backend enablement.
- Requires lab approval, approval reference, workspace root, credential
  reference, authorization policy, authorized submitter list, audit retention,
  and operator contact before any real backend can be considered selected.
- Runtime remains dry-run only; `local`, `ssh`, `slurm`, and `pbs` are still
  rejected by the shared backend guard.

M11 预检详细文档：

- `docs/upgrade/test-report-m11-preflight.md`
- `docs/upgrade/m11-preflight-completion-audit.md`
- `docs/upgrade/learning-summary-m11-preflight.md`

`v0.9 后端就绪评审` 已在 2026-06-22 完成：

- 面向 operator 渲染 `BackendPreflightReport`。
- 为 operator 评审生成 dry-run 提交包预览。
- 生成仅 metadata 的审计日志预览，不做持久化。
- 生成 workspace 和 artifact 路径预览，不创建目录。
- 就绪输出展示 metadata 就绪状态、运行时启用状态、验证错误、运行时阻塞原因和安全边界。
- 运行时仍然只允许 dry-run；`local`、`ssh`、`slurm` 和 `pbs` 仍会被共享后端守卫拒绝。

v0.9 详细文档：

- `docs/upgrade/test-report-v0.9.md`
- `docs/upgrade/learning-summary-v0.9.md`

`M11 实验室后端决策包` 已在 2026-06-23 创建：

- `docs/upgrade/m11-lab-backend-decision-package.md`
- `docs/upgrade/m11-lab-process-guide.md`
- 这些文档只是评审材料，不选择或启用真实后端。
- M11-T1 仍未完成；必须等实验室提供具体后端选择、凭据策略、
  workspace root、授权策略、审计保留、配额/operator 规则和 operator
  联系人后才能继续真实后端实现。

如果当前实验室只是一个服务器账号、自己或小组内部先跑实验，请优先阅读
`docs/upgrade/single-server-backend-v0.10.md` 和
`docs/upgrade/v1.0-internal-preview-roadmap.md`。它们把下一阶段收敛为
单服务器账号、固定 workspace、固定 approved template、dry-run review packet、
fake lifecycle 和内部预览收口，不要求一开始做复杂多用户平台。

`v0.10 单服务器账号接入准备` 已在 2026-06-23 完成第一批实现：

- `docs/upgrade/single-server-backend-v0.10.md`
- `docs/upgrade/test-report-v0.10.md`
- `docs/upgrade/learning-summary-v0.10.md`
- 新增 `SingleServerProfile`、`SingleServerJobTemplate` 和
  `SingleServerReviewRequest` metadata。
- 新增 profile/template/request 校验和 dry-run review packet renderer。
- 不执行真实命令，不读取真实凭据，不连接服务器，不创建 workspace，也不改变
  运行时后端守卫。

`v0.11 实验室内部安全操作策略` 已在 2026-06-23 完成第一批实现：

- `docs/upgrade/safe-operations-v0.11.md`
- `docs/upgrade/test-report-v0.11.md`
- `docs/upgrade/learning-summary-v0.11-safe-operations.md`
- 新增 `LabAccountRole`、`SafeOperationType`、`SafeOperationRequest`、
  `SafeOperationPolicy`、`DeleteReviewRequest` 和 `DeleteReviewPacket` metadata。
- 新增 safe operation allowlist 校验和删除 dry-run review packet renderer。
- 测试证明 `readonly` 不能请求删除 preview，`lab_user` 可以请求 workspace 下的
  delete dry-run preview，`lab_root` 仍不能绕过 dry-run、路径保护、symlink 风险和
  删除确认。
- 不做真实删除、不移动 trash、不读取凭据、不连接服务器、不创建 workspace、不执行 shell。

`v0.12 Fake Lifecycle` 已在 2026-06-23 完成第一批实现：

- `docs/upgrade/test-report-v0.12.md`
- `docs/upgrade/learning-summary-v0.12.md`
- 新增 `SingleServerLifecycleState`、`SingleServerLifecycleEvent` 和
  `SingleServerLifecycleRecord` metadata。
- 支持 requested、reviewed、approved、rejected、queued、running、succeeded、
  failed 和 cancelled 状态解析、内存态状态转换和 lifecycle preview。
- preview 展示当前状态、允许的下一状态、event history，以及
  `server_connected: false`、`command_executed: false`、
  `workspace_created: false`。
- 不连接服务器、不执行命令、不创建目录、不读取凭据、不采集真实日志或 artifact。

`v0.13 Workspace Planner` 已在 2026-06-23 完成第一批实现：

- `docs/upgrade/test-report-v0.13.md`
- `docs/upgrade/learning-summary-v0.13.md`
- 新增 `WorkspacePlanRequest` 和 `WorkspacePlan` metadata。
- 支持 workspace path、run directory path、log path 和 artifact path preview。
- 新增路径安全校验，拒绝空 workspace root、`..` 路径穿越、绝对路径逃逸、
  危险 root、以及 `.git`、`.ssh`、`secrets`、`credentials`、`repo`、`code`、
  `env`、`venv`、`shared_data` 等保护标签。
- preview 明确展示 `directories_created: false`、`files_moved: false`、
  `server_connected: false`。
- 不创建目录、不删除目录、不移动文件、不连接服务器、不访问远程文件系统。

`v1.0 internal preview` 分步路线已在 2026-06-23 创建：

- `docs/upgrade/v1.0-internal-preview-roadmap.md`
- 路线把 v0.11 到 v1.0 拆成 safe operations、fake lifecycle、workspace
  planner、approved template run packet、sanity-check runner gate 和 v1.0
  closeout。
- 新窗口复制提示词和详细 agent 执行计划保存在本地忽略文件中，不再提交到 GitHub。

真实 CUDA/MPI、Slurm、PBS、SSH 或实验室服务器执行仍保留到后续后端里程碑；
只有产品和最小安全边界稳定后再启用。

## New Conversation Workflow

Every new upgrade conversation should follow this sequence.

1. Keep any copy-paste prompts in a local ignored file, for example
   `docs/upgrade/local-prompts.md` or
   `docs/upgrade/local-v1.0-internal-preview-prompts.md`.
2. Ask the agent to read these files first:
   - `docs/upgrade/README.md`
   - `docs/upgrade/milestones.md`
   - `docs/upgrade/career-notes.md`
   - `docs/upgrade/version-roadmap.md`
   - `docs/upgrade/upgrade-log.md`
   - the active local implementation plan under `docs/superpowers/plans/`, if
     a local ignored plan exists for this workspace
3. The agent checks `git status --short`.
4. The agent chooses the next unchecked task from the active plan.
5. The agent implements only that task or one tightly related batch.
6. The agent runs the required validation commands.
7. The agent updates `docs/upgrade/upgrade-log.md`.
8. If the change adds architecture, a technical capability, tests, deployment,
   or product story, the agent updates `docs/upgrade/career-notes.md`.
9. The agent commits the completed work to git.
10. The final response includes:
   - what changed
   - which tests ran
   - commit hash
   - next recommended task
   - a detailed Chinese knowledge summary for learning and interview prep when
     the task finishes a version, adds major architecture, or produces a test
     report

Knowledge summaries should be written in Chinese and detailed enough for later
study and interview preparation. Do not write only a few generic bullets. Use
sectioned prose with concrete engineering details. For version completions, test
reports, major architecture work, or meaningful technical capability changes,
include at least:

- The problem being solved and why the previous version was insufficient.
- The implementation approach, including data flow, API shape, important
  design tradeoffs, and why simpler or more complex alternatives were not used.
- Key files, tests, resources, and what each test protects.
- Safety or product boundaries, especially around CUDA/MPI, SSH, Slurm/PBS,
  remote execution, shell execution, and Code Agent write permissions.
- Debugging or TDD evidence when relevant: what failed first, what changed, and
  what verification proved.
- Interview preparation material: a short project pitch, a technical deep dive,
  likely follow-up questions with answers, and a STAR-style explanation.

## Required Validation

Run the smallest useful test set for every change, then broaden when the change
touches shared behavior.

| Change type | Required validation |
| --- | --- |
| Docs only | `git diff --check` |
| CMake or core C++ | `cmake --build build -j2` and `ctest --test-dir build --output-on-failure` |
| Tests only | `cmake --build build -j2` and the changed test binary through `ctest` |
| Agent routing | `ctest --test-dir build --output-on-failure` plus a manual curl or client smoke test if services can run |
| MCP plugin | Build `mcp_server_integrated`, run plugin or MCP integration test, then full `ctest` |
| Web UI | Serve `web/index.html`, check browser or curl health, and run `git diff --check` |
| Deploy script | Shell syntax check with `bash -n <script>` plus dry-run/manual command review |

Do not claim a task is complete unless the relevant validation command has been
run and its result is recorded in `docs/upgrade/upgrade-log.md`.

## Git Rules

Use small commits. One milestone can have many commits.

Suggested branch names:

- `upgrade/v0.2-code-agent`
- `upgrade/v0.2-algorithm-card`
- `upgrade/v0.2-experiment-planner`
- `upgrade/v0.2-workbench-ui`

Commit message style:

```text
docs: add lab agent upgrade roadmap
feat: add algorithm card registry
test: cover dry-run job backend
fix: route code intent to code agent
refactor: extract shared agent runtime
```

Before every commit:

```bash
git status --short
git diff --check
```

For code changes, also run the required build/test commands from the validation
matrix.

## Safety Boundaries

These rules stay in effect until the real server backend milestone.

- Do not execute real CUDA/MPI jobs.
- Do not run arbitrary shell commands from user input.
- Do not write or delete files outside the repository from an agent tool.
- Do not add SSH, Slurm, PBS, or remote execution until a reviewed v0.8 safety
  implementation explicitly enables a backend.
- Use `DryRunBackend` first. It may render commands and scripts, but it must not
  submit or execute them.
- Code Agent MVP should be read-only by default. Patch generation is allowed as
  text or explicit diff output. Automatic patch application is a later opt-in
  feature.

## Active Plans

Detailed agent execution plans and copy-paste prompts are local workspace
artifacts. They live under `docs/superpowers/plans/*.md` or
`docs/upgrade/local-*.md`, and are ignored by git so they do not get uploaded
to GitHub.

Tracked planning source of truth:

- `docs/upgrade/v1.0-internal-preview-roadmap.md`
- `docs/upgrade/safe-operations-v0.11.md`
- `docs/upgrade/single-server-backend-v0.10.md`

Current version state:

- v0.9 Backend Readiness Review is complete.
- v0.10 Single Server Runner Preparation has completed the metadata/profile/
  template and dry-run review packet implementation batch.
- v0.11 Safe Operations has completed the metadata, validation, and delete
  dry-run review packet implementation batch.
- v0.12 Fake Lifecycle has completed the metadata-only lifecycle state machine,
  in-memory transition validation, and non-executing preview renderer.
- v1.0 internal preview should start only after the roadmap's v0.11-v0.15
  safety gates are implemented and tested.

Next session should continue v0.13 Workspace Planner. Do not create
directories, delete files, move files, or connect real execution by default.

When a milestone becomes too large, create a new plan in:

```text
docs/superpowers/plans/YYYY-MM-DD-<feature-name>.md
```

Each local plan should produce working, testable software on its own. These
local plans are intentionally ignored by git.

## Local Prompts

Do not commit personal copy-paste upgrade prompts. If a prompt needs to be saved
locally, store it in `docs/upgrade/local-prompts.md`, which is ignored by git.
