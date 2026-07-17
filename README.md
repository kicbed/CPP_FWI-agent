# Deepwave 2D FWI 多 Agent 科研助手

这是一个基于 C++17、MCP、Web UI 和 Deepwave 的**实验性二维常密度声学 FWI MVP**。它可以在本机 CPU 或单张 NVIDIA GPU 上实际执行合成数据正演与简单速度反演，并通过自然语言提交、查询和展示任务。

环境和固定 Marmousi 模型准备完成后，一键启动：

```bash
./start.sh
# 浏览器打开 http://127.0.0.1:8080
```

`start.sh` 会自动执行 CMake 增量配置与编译，启用 MCP，并启动 Agent 与 Web；需要 clean-first 重新编译时使用 `./start.sh --rebuild`，确认二进制已是最新时可用 `./start.sh --no-build`。

一键关闭：

```bash
./stop.sh
```

`stop.sh` 可以重复执行；已经停止的服务不会导致脚本失败。它不删除 Redis/浏览器历史，
也不等于取消已提交的 FWI 任务。

使用 Codex 继续开发时，在本仓库或其子目录中正常打开新会话后直接提问即可。根目录
`AGENTS.md` 会自动要求 Codex 读取已采纳决策并检查当前 Git、测试、服务和任务状态；
不需要记忆或手动运行项目专用 Codex 脚本。跨会话记录规则和安全边界见后文的 Codex
工作流文档。

需要使用 Web 的 gRPC 模式时，也仍从同一个根入口启动：

```bash
./start.sh --grpc
```

该选项会额外启动仅监听 `127.0.0.1:50051/50052` 的 gRPC Server 和 Web bridge。
浏览器仍通过 HTTP 访问 50052，但 bridge 会把请求真正转发到 50051 的
`AIQueryService`；未加 `--grpc` 时，页面会禁用 gRPC 模式并说明启动方法。

根目录的 `./start.sh` 与 `./stop.sh` 是唯一推荐的日常启停入口。`examples/ai_orchestrator/start_system.sh` 属于内部实现，`deploy/scripts/` 下的旧入口只保留给兼容或高级调试场景，不建议直接调用。

首次安装、C++ 本地编译、Python 虚拟环境、CUDA 和 Docker 隔离部署请先阅读 [部署与使用说明](docs/DEPLOYMENT.md)。仓库不包含编译产物、Python 虚拟环境、模型文件、运行结果或 `.env`。

- 更换/注册模型及当前全部反演参数：[模型与数值配置指南](docs/MODEL_GUIDE.md)
- 自己从浏览器逐步验收：[Web 前端端到端测试](docs/FRONTEND_TEST.md)
- 会话隔离、上下文窗口、历史保留与隐私：[对话上下文与历史管理](docs/CONVERSATION_MANAGEMENT.md)
- 跨 Codex 会话延续的已采纳方向与待实现项：[项目持续开发与已采纳决策](docs/PROJECT_CONTINUITY.md)
- 已批准的下一代科研任务 Runtime 完整阶段：[科研任务 Agent Runtime 实施计划](docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md)
- 已验证的 P0 最小 FWI 合同、执行门与安全边界：[Scientific Runtime P0 合同规范](docs/architecture/SCIENTIFIC_RUNTIME_P0_CONTRACTS.md)
- 已验证的 P1 Guided 确认、运行与结果闭环：[P1 Guided Web 验收边界](docs/architecture/SCIENTIFIC_RUNTIME_P1_GUIDED_WEB.md)
- 各阶段实际 checkpoint 和下一安全动作：[科研任务 Runtime 进度账本](docs/PROJECT_PROGRESS.md)
- 分支、提交、SSH 推送与待确认的 AI 提示词提案：[Git 与 AI 提示词管理规则](docs/GIT_AND_PROMPT_POLICY.md)
- 新 Codex 会话如何自动接续项目：[Codex 自动接续与跨会话工作流](docs/CODEX_WORKFLOW.md)

项目治理最高约束：历史 D-001～D-012 保持原状。任何编号 D 的新增、删除、重编号、重排或正文修改，
必须先由 Codex 展示单 D 精确 diff/SHA-256，再由用户单独原样复制其唯一 `D-AUTH` 授权句；
“同意”“继续”“固定”“记录”“修正”等均不能替代该授权。

## 下一代 Runtime：P0 + P1 已验证，P2 进行中

`D-003 Accepted` 的 P1 最小持久垂直切片已完成：固定 Marmousi/Deepwave 任务从
Guided 表单进入 TaskDraft/Plan 确认卡，用户可修改、批准或放弃 pre-runtime 草稿；
批准后任务通过 SQLite TaskService、固定 Adapter 和受监督派发实际运行，页面展示
真实状态、事件与受控 NPY/CSV 结果。

当前 P2 已完成任务恢复、Supervisor/Worker fence、运行中取消、timeout、正向 reconciliation
与最多一次有限重试的有界 Verified 切片；完整 P2 仍 Pending，剩余负向/不确定 reconciliation
矩阵及 SSE + P2 阶段出口。固定全项目顺序为 P0→P1→P2→P3→P4→P5→P6，P3～P6 仍
Pending；只有通过 P6 的评测、观测和安全加固出口才算完整项目完成，P5 不是终点。

## 当前能做什么

- 使用 Deepwave `scalar` 求解二维常密度声学波动方程。
- 支持 CPU 和单张 NVIDIA GPU，使用 `float32`、Ricker 子波和单参数纵波速度 `Vp`。
- 从固定 Marmousi NPY 与 sidecar JSON 读取并验证 shape、轴顺序、网格、速度范围和哈希。
- 用真实速度模型生成合成观测数据，从慢度平滑初始模型执行 L2 波形残差 FWI。
- 提供 `forward`、2 次迭代 `fwi_smoke` 和 5 次迭代 `fwi_demo` preset。
- 生成模型、炮集、残差、损失曲线、状态、指标和 manifest 等结构化产物。
- 通过白名单 MCP 工具异步提交任务，并在 Web UI 中查看状态、指标和图片。
- FWI 理论问题会先检索仓库内受控的本地知识文档；该检索不依赖 Embedding 在线。
- Agent-RAG 可选用本地 Qwen Embedding 做 AgentCard 语义路由，离线时退回关键词匹配。
- Web 对行内和块级 LaTeX 公式按需渲染，渲染组件不可用时保留可读 TeX。

这不是生产级反演系统。观测数据与反演传播均由 Deepwave 生成，属于合成端到端/逆犯罪验证，主要用于验证系统调用、梯度、优化和结果展示流程，不能据此宣称对实际数据的普遍反演效果。

## 快速体验

启动后访问 <http://127.0.0.1:8080>，点击 **Smoke CUDA**（无 CUDA 时选 **Smoke CPU**），
填写或保留 `smoke`、`1`–`2` 次迭代和合法 seed，然后依次执行：

1. **生成 Draft / Plan 确认卡**：预期看到真实 `task_id`、revision 1、`plan_hash` 和
   `AwaitingApproval`，且此时没有 Worker job；
2. **修改**：改变 seed 再生成，预期 revision 加 1、`plan_hash` 改变；
3. **批准运行**：预期看到 `Queued`/`Running` 后进入 `Succeeded`（快任务可跳过中间帧）；
4. 成功后预期恰好八张标准结果卡：反演速度模型 NPY、损失曲线 CSV，以及真值模型、
   初始模型、反演模型、模型误差、炮集和损失曲线六张 PNG。

也可在聊天输入“帮我做个 Marmousi FWI，迭代50次”；执行型 FWI 文本应先打开同一张
Guided 确认卡，不会绕过人工批准直接提交。

普通 Web 聊天的 HTTP 与可选 gRPC transport 还固定关闭 legacy FWI submit；Orchestrator
会在 actual tool plan 之后、执行 MCP 之前拒绝旧提交工具。旧 CLI/MCP 客户端不带该字段时
继续兼容。该机制用于防前端分类规则漂移，P1 的安全部署边界仍是本机 loopback。

理论问题不会触发计算，例如：

```text
什么是 FWI？只解释概念，不要运行任务。
```

此类理论问题会使用 `resources/fwi_knowledge` 等本地资料作为有界参考，并要求在资料
支持的结论后标注对应本地文档标题。Embedding 状态与这套文档检索是两件事：Embedding 只用于
Agent-RAG 路由，即使它被禁用或暂时离线，本地 FWI 知识仍然可用。

如果只想确认能力或查看启动方法，可以问：

```text
你可以做 FWI 反演吗？
怎么启动一个 FWI 反演？
```

这两类问句只返回受支持范围和可复制命令，不会悄悄提交任务。带模型名和动作的明确
执行请求会打开 Guided 确认卡：

```text
使用 marmousi_94_288 在 CUDA 上运行两次迭代的二维声学 FWI smoke test。
```

`smoke` 建议 1–2 次，`demo` 默认 5 次；也可以在自然语言中显式指定
1–10000 次，页面先把该整数放入可修改的确认卡，例如：

```text
使用 marmousi_94_288 在 CUDA 上运行 500 次迭代的 FWI，并向我展示结果。
```

用户点击“批准运行”后才异步提交。页面显示离开 Worker 内部身份的 `task_id`、真实
状态和标准 artifact；它不会在计算完成前伪造结果。超过安全上限、负数或小数会被拒绝。
超过 100 次的任务可能长时间占用 CPU/GPU，应先确认资源和参数。当前受托管且能证明 exact
attempt 的 Worker 运行后，任务卡会提供有界取消；不在该能力边界内的任务会 fail closed，
不会通过持久 PID 或任意后端强制终止。

## 模型和运行目录

当前 MCP 演示只允许固定模型 `marmousi_94_288`，不接受用户提供的任意文件路径：

```text
/root/fwi-data/models/marmousi_94_288.mat
/root/fwi-data/models/marmousi_94_288.npy
/root/fwi-data/models/marmousi_94_288.json
```

模型约定为 `[z, x] = [94, 288]`、`dx = dz = 10 m`、`float32`、`Vp = 1500–5500 m/s`。Worker 运行时从 sidecar 读取并验证这些信息，不修改原始模型。

任务输出默认写入：

```text
/root/fwi-runs/<job_id>/
```

可通过绝对路径环境变量 `FWI_RUN_ROOT` 修改输出根目录。每个成功任务包含：

```text
config.original.json   config.resolved.json   environment.json
status.json            progress.jsonl         run.log
loss.csv               metrics.json           manifest.json
models/*.npy           data/*.npy              figures/*.png
```

Web 只通过 `/fwi-artifacts/<job_id>/...` 暴露该运行根目录内受控的 `.json`、`.csv` 和 `.png` 文件。

## 独立 Worker

不启动 Agent 系统也可以直接测试数值后端：

```bash
source /root/.venvs/cpp-fwi-agent/bin/activate

python -m fwi_worker validate \
  --config tests/fwi_worker/fixtures/marmousi_forward_cpu.json

python -m fwi_worker forward \
  --config tests/fwi_worker/fixtures/homogeneous_cpu.json
```

确认 `torch.cuda.is_available()` 为 true 后再运行 CUDA 示例：

```bash
python -m fwi_worker invert \
  --config tests/fwi_worker/fixtures/marmousi_fwi_smoke_cuda.json
```

查看任务状态：

```bash
JOB_ID=fwi-YYYYMMDDTHHMMSSZ-xxxxxxxxxxxx
python -m fwi_worker status --run-dir "/root/fwi-runs/$JOB_ID"
```

## 架构

```text
浏览器 Web UI (:8080)
        ├──HTTP 默认───────────────> C++ Orchestrator (:5000)
        └──可选 Web bridge (:50052) ──gRPC──> AIQueryService (:50051)
                                                   │ A2A
                                                   ▼
                                      C++ Orchestrator (:5000)
                                                   │
                                      Agent / RAG / 理论问答
        │
        ▼
MCP fwi-runner ── 固定参数校验与异步进程启动
        │
        ▼
Python fwi_worker ── Deepwave 正演 / 梯度 / FWI / 绘图
        │
        ▼
FWI_RUN_ROOT ── 状态、指标、数组和 PNG 结果
```

数值算法只存在于独立 Python Worker 中；C++ MCP 插件只负责白名单参数校验、安全启动、状态查询和结果读取。通用 JobBackend 的 dry-run 限制没有被解除。

首次启用本地 Agent-RAG Embedding 时先显式准备模型缓存：

```bash
deploy/scripts/setup_embedding.sh
```

随后设置 `ROUTING_MODE=agent-rag`、`EMBEDDING_PROVIDER=local` 和
`ENABLE_LOCAL_EMBEDDING=auto`，根 `start.sh`/`stop.sh` 会统一托管服务。日常启动不会
自动下载模型，默认使用 CPU，避免与 Deepwave FWI 争用 GPU。详细配置见部署文档。

## 快速测试

Python Worker 与 Web 安全测试：

```bash
source /root/.venvs/cpp-fwi-agent/bin/activate
python -m unittest discover -s tests/fwi_worker -p 'test_*.py' -v
python -m unittest web.tests.test_artifact_route -v
node web/tests/ui_message_rendering_test.js
```

C++ 项目与 MCP 插件：

```bash
cmake -S . -B build
cmake --build build -j"$(nproc)"
ctest --test-dir build --output-on-failure

cmake -S mcp_server_integrated -B mcp_server_integrated/build
cmake --build mcp_server_integrated/build -j"$(nproc)"
ctest --test-dir mcp_server_integrated/build --output-on-failure
```

CUDA 运行前建议确认：

```bash
/root/.venvs/cpp-fwi-agent/bin/python -c \
  "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
```

## 安全边界

- 不要提交 `.env`，不要把 API Key 写入命令、README、配置样例或日志。`.env` 由 Bash
  `source` 加载，只能使用自己创建且权限为 600 的可信赋值文件；它不是 secret manager。
- 根启动器只让需要调用 LLM 的 Agent 继承当前 provider 的 Key；Redis、Registry、Web、
  gRPC、Embedding、MCP 和 FWI Worker 会显式剥离 provider Key。为自动重启 Orchestrator，
  watchdog 仍保留已最小化的当前 LLM 凭据，以及仅在使用 DashScope Embedding 时所需的
  对应凭据；同一操作系统账号仍应视为同一安全边界。
- 使用云端 LLM 时，当前问题和有界历史会发往该 provider 的固定官方 endpoint；
  不要在聊天中粘贴 Key 或未授权数据。`local` provider 只允许带显式端口和路径的严格
  loopback HTTP endpoint，并会绕过系统代理且拒绝重定向。
- AgentCard 向量缓存写入忽略的私有运行目录，使用不跟随符号链接的文件访问和原子替换；
  缓存只是性能优化，损坏或身份不匹配时会重新计算或退回关键词路由。
- MCP 只接受固定的 `model_id`、preset、`cpu|cuda` 和仅反演可用的 `iterations=1..10000`，
  不接受 shell、Python 路径、模型路径或任意额外参数；单个 runner 并发上限为 2 个作业。
- Worker 通过部署时受控的绝对 Python 路径、固定模块和参数数组启动；该路径不接受 MCP 用户输入，不使用 `std::system`，也不执行用户提供的任意命令。
- Artifact 路由会解析并约束真实路径，拒绝 `..`、绝对路径、符号链接逃逸、目录列表和非白名单后缀。
- 当前 Web 服务面向本机实验使用，不带身份认证；不要直接暴露到公网。
- 每个 Web 对话使用独立 `contextId`；历史采用有界完整回合窗口并按会话隔离 FWI 工具状态。内置
  Redis 默认在仓库外使用 AOF `everysec` 持久化；session 每回合刷新 TTL，tool state 只在新
  job_id 落盘时刷新。异常崩溃可能丢失最后约 1 秒，当前也没有备份/HA。浏览器
  localStorage 与 Redis 数据均为明文，敏感实验应使用加密磁盘或关闭持久化并清除本地历史。
- 专业 Agent 接收有界、保留原始角色的上下文 envelope，不再共享一份 legacy history；
  Web 服务设置 CSP 等安全响应头。但前端依赖仍来自固定第三方 CDN，高敏感离线环境应先
  将这些依赖本地化，且不要把当前无登录鉴权的服务暴露给其他用户。
- 当前不连接 SSH、Slurm、PBS 或远程集群，也不支持任意本地作业后端。

## 范围与限制

当前只覆盖二维常密度声学、单参数 `Vp`、主频 8 Hz 的宽带 Ricker 子波、当前单阶段反演、CPU/单 GPU 和小规模合成验证。尚不支持弹性波、密度或 `Vs` 反演、3D、MPI、多 GPU、SEG-Y 直接读取、远程调度、复杂目标函数，以及脱离当前受托管 exact-attempt 边界的任意后端强制终止。

当前参数策略是“用户明确给值就校验后执行，省略时使用记录在 resolved config 中的保守
默认值”。由 Agent 提议参数后等待人工审批，或由用户授权 Agent 全权选择参数的交互工作流
尚未实现，作为后续功能保留。

完整的宿主机环境配置、仓库外 Python venv、Docker 隔离、模型放置、启动参数、健康检查、日志、故障排查及卸载步骤见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。模型注册和参数解释见 [docs/MODEL_GUIDE.md](docs/MODEL_GUIDE.md)，浏览器验收步骤见 [docs/FRONTEND_TEST.md](docs/FRONTEND_TEST.md)，上下文与历史语义见 [docs/CONVERSATION_MANAGEMENT.md](docs/CONVERSATION_MANAGEMENT.md)。

## 许可证与使用提示

请分别遵守本项目及其依赖（包括 PyTorch、Deepwave 和模型数据）的许可证与使用条件。模型数据不会随仓库分发。
