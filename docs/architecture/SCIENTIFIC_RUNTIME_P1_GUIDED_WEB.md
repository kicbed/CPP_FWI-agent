# Scientific Runtime P1 Guided Web 闭环

<!-- scientific-runtime-p1-guided-web: v1 -->

- 决策：`D-003` / Accepted
- 切片：`P1-005`
- 实现状态：**Verified**
- 验证日期：2026-07-15
- 前置依赖：P0 contracts、P1.1a Task Store、P1.1b Registry、P1.2a fixed Adapter、
  P1.1c atomic submit 均已 Verified

本切片把已经验证的持久后端接入本机 Web，完成固定 Marmousi/Deepwave 的一条最小纵向
链路：Catalog → 确定性表单 → TaskDraft/Plan → 人工批准 → SQLite task → Adapter status →
两个标准 ArtifactManifest。它没有实现 P2 的取消、lease、重试、自动 reconciliation、SSE
或任务列表，也没有实现 P3 DAG 或 P4 Agent Planner。

## 1. 产品流程

1. Web 从同源 session endpoint 取得服务器绑定的 scope、P1 capability 和进程生命周期内的
   CSRF token；服务重启后必须重新取得；
2. 从 immutable Catalog 读取唯一受支持的 `marmousi_94_288@1.0.0` 与当前
   `deepwave.acoustic_fwi@1.1.0`，页面只展示无路径 metadata；旧算法 `1.0.0` 快照保留但不供
   新 Guided 任务选择；
3. 浏览器只提交七个字段：`goal`、dataset ID/version、`preset`、`device`、`iterations`、
   `seed`；服务器组装完整 TaskDraft、资源上限、单节点 Plan 和 node idempotency key；
4. 页面展示真实 `task_id`、draft revision、计划和 canonical `plan_hash`。修改使用 revision
   CAS 并生成新计划；放弃仅允许 pre-runtime task，持久记录为用户 discard，不调用 Adapter
   cancel；
5. “批准运行”把 ApprovalDecision 精确绑定当前 `plan_hash`，随后复用 P1.1c 原子 submit 与
   one-shot dispatch；批准前不会创建 FWI job；
6. 页面只用 GET 轮询。TaskService 从已持久化 receipt 查询 Adapter，把 Running/progress/
   Succeeded/Failed 转成 SQLite 中连续、不可变的 RunEvent；
7. Succeeded 后固定收集 `inverted_velocity_model_2d` NPY 和 `loss_curve` CSV。每次下载都再次
   复核 manifest、task/plan/fingerprint/lineage、字节数和 SHA-256，再通过 task-scoped endpoint
   返回；浏览器不能从 `location` 拼接服务器路径。

执行型 FWI 快捷按钮和聊天文本统一进入这张确认卡。理论问题仍走原聊天路径；旧 MCP/FWI
Result renderer 保留作兼容回归，但不再允许日常 Web 操作绕过批准卡直接执行。

D-006/P1-006 把显式反演迭代上限从 100 提升到 10000，同时保留 smoke/demo 默认 2/5 次、
人工批准和严格整数校验。变更使用新的 Algorithm/Adapter `1.1.0` 身份，不原地改写已注册的
`1.0.0` manifest。大于 100 次时页面提示长任务风险；P1 仍不提供运行中取消或完成时间保证。

普通 Web 聊天的 HTTP/A2A 请求固定携带字符串
`metadata.allow_legacy_fwi_submit="false"`；可选 gRPC bridge 固定接收布尔 `false`，再以
protobuf/A2A 字符串 metadata 传给 Orchestrator。同步与流式 handler 使用同一策略，
`ToolCallingEngine` 在生成实际确定性 tool plan 后、执行 MCP 前拒绝
`fwi_submit_demo`，但不误拦兼容的 status/result 读取。字段缺省时继续兼容旧 CLI、MCP 和
A2A 客户端。该字段是防止前端 classifier 漂移的 Web 产品来源策略，不是不可伪造的认证；
P1 的部署边界仍是本机 loopback。

## 2. 持久与幂等边界

SQLite schema v4 新增两类不可变记录：

- `workbench_mutations`：记录 draft revision、plan、approval 和 abandon 的 scope、operation、
  mutation key、request hash、task 与 outcome hash。精确重放识别已提交的原 mutation、不重复产生
  副作用，并返回该 task 当前受 scope 约束的 aggregate；同 key 不同请求拒绝；
- `task_abandonments`：只允许 `Draft|NeedsInput|AwaitingApproval → Cancelled`，要求没有 dispatch
  intent 和 RunEvent。SQL trigger 与服务层同时禁止把它冒充运行中取消。

create 与 submit 已分别沿用既有 typed idempotency 表。Guided facade 为一个用户 mutation 派生
分域 key，并使用稳定 task/draft/plan/approval identity，使响应丢失后的同 key 重放不重复创建
任务或 Worker。浏览器在 mutation 网络结果未知时保留原 key、禁用自动重发，先用 GET
查询；若查到 approval 已持久化但 submit 尚未入队，页面只提供由用户显式点击、且复用原
`Idempotency-Key` 的安全重放。这不是 P2 task retry。页面刷新后的任务发现/列表属于
P2，P1 只保证持有 `task_id` 时可在服务重启后查询。

## 3. 同源 HTTP API

固定前缀：`/api/scientific-runtime/v1`。JSON 成功 envelope 为
`{"ok":true,"data":...}`；错误只返回稳定 code 和脱敏 message。

| Method | Path | 作用 |
|---|---|---|
| GET | `/session` | 唯一不要求 CSRF 的 endpoint；返回 scope/capability/token |
| GET | `/catalog` | 返回当前 scope 可执行的固定 Dataset/Algorithm |
| POST | `/tasks` | 七字段表单创建 draft + plan |
| PUT | `/tasks/{task_id}/draft` | `expected_revision` + 七字段 CAS 修订 |
| POST | `/tasks/{task_id}/approve` | 批准当前 `plan_hash` 并 submit |
| POST | `/tasks/{task_id}/abandon` | body `{}`；只放弃 pre-runtime 草稿 |
| GET | `/tasks/{task_id}` | 查询并执行一次只读 Adapter status refresh |
| GET | `/tasks/{task_id}/events` | `after_sequence`/`limit` 分页读不可变事件 |
| GET | `/tasks/{task_id}/artifacts` | Succeeded 后返回恰好两个 manifest |
| GET | `/tasks/{task_id}/artifacts/{artifact_id}` | 受控下载 NPY/CSV |

除 `/session` 外都要求 `X-Workbench-CSRF`。所有 mutation 还要求 exact loopback `Origin`、
`Idempotency-Key`、UTF-8 `application/json` 和精确 `Content-Length`；JSON 拒绝重复 key、NaN/
Infinity、未知字段和超过 64 KiB 的 body。Host 必须等于部署配置的浏览器 authority，不返回
CORS header。浏览器不能提供 project/principal、任意路径、shell、Adapter handle 或 Worker
job ID。

P1 没有用户认证，因此 Guided API 只在 `127.0.0.1`/`localhost` 绑定时启用。
`WEB_HOST=0.0.0.0` 只保留旧静态页和 legacy API 的容器兼容性，Guided 路由 fail closed 为 503；
即使 Compose 只向宿主机 loopback 发布端口，也不把 wildcard 绑定当作 P1 身份边界。
这不是远程多用户方案。

## 4. 启动与状态位置

推荐入口不变：

```bash
./start.sh --no-build   # 已有最新构建
# 或 ./start.sh         # 自动增量构建
```

浏览器打开 `http://127.0.0.1:8080`。默认 task DB 位于仓库外：

```text
~/.local/state/cpp-fwi-agent/scientific-runtime/tasks.sqlite3
```

可通过 `SCIENTIFIC_RUNTIME_DB_PATH` 选择另一个私有绝对文件；路径不能穿越 symlink、位于
仓库、`FWI_RUN_ROOT` 或系统敏感目录，现有直接父目录必须由当前用户所有且为
`0700`。当前 Compose 继续只提供 legacy Web，不部署 Guided Runtime DB。任务库、WAL、FWI
运行结果和日志均不进入 Git。

## 5. 实际前端验收

1. 点击页面中的 **Smoke CUDA**（无 CUDA 时用 **Smoke CPU**）；
2. 确认页面出现 session scope 和 immutable Catalog metadata；建议填写：preset `fwi_smoke`、
   device `cuda`、iterations `1` 或 `2`、任意合法 seed；
3. 点击“生成 Draft / Plan 确认卡”。预期获得真实 `task_id`、revision 1、单节点 plan 和
   `AwaitingApproval`；此时没有 Worker job；
4. 点击“修改”，改变 seed，再次生成。预期 revision 变为 2、`plan_hash` 改变；
5. 若测试“放弃草稿”，点击放弃。预期 task 为 `Cancelled`，页面明确说明不是运行中取消；
   随后重新打开 Smoke 创建另一 task；
6. 点击“批准运行”。预期 dispatch 为 `dispatched`，SQLite 状态依次为 Queued、Running、
   Succeeded（很快时可跳过页面上中间帧）；失败必须明确显示 Failed，不伪装成功；
7. 成功后应出现恰好两张标准结果卡：反演模型 NPY 与损失 CSV。两个下载均从同源受控 endpoint
   返回 attachment，不使用旧的 raw job 路径；
8. `./stop.sh` 后重新启动，持有原 `task_id` 时 API 仍能读到相同终态和已落库事件。P1 页面
   本身不提供任务列表或自动恢复当前卡片。

## 6. 验证证据与限制

自动化覆盖 fresh/v3→v4 migration、hash/schema/trigger tamper、并发幂等、CAS、批准绑定、
pre-runtime abandon、状态单调/轮询去重、精确 NPY+CSV、受控下载、Host/Origin/CSRF/JSON/
HTTP framing、UI XSS/路径和批准防绕过。UI 与真实 C++ planner 的 75,240 条组合差分中，
`backend submit && UI false` 为 0；实际 HTTP sync/stream 和 gRPC bridge 绕过语句也都在 MCP
执行前返回 Guided 提示，且未新增 legacy job。现场验收另使用固定 venv 完成一次一迭代
CUDA：Queued → Running → Succeeded，验证连续 RunEvent、两个 artifact 的 size/SHA-256、
逻辑 artifact location 和 Worker job ID 脱敏，并在 Web 重启后重新查询同一 task。临时
task/job identity 不写入本文。

仍属 Pending：运行中 cancel、timeout、lease/heartbeat、retry、自动 reconciliation、SSE、
任务列表/页面刷新恢复、DAG、Agent Planner、通用算法 SDK。Adapter provenance 仍是
development，不据此声明跨环境 bitwise 可复现或真实数据上的科学效果。
