# Scientific Runtime P1 Guided Web 闭环

<!-- scientific-runtime-p1-guided-web: v1 -->

- 决策：`D-003` / Accepted
- 切片：`P1-005`
- 实现状态：**Verified**
- 验证日期：2026-07-15
- 前置依赖：P0 contracts、P1.1a Task Store、P1.1b Registry、P1.2a fixed Adapter、
  P1.1c atomic submit 均已 Verified
- 后续维护：`D-007 / P1-007 / P2-001` **Verified；完整 P2 仍 Pending**

本切片把已经验证的持久后端接入本机 Web，完成固定 Marmousi/Deepwave 的一条最小纵向
链路：Catalog → 确定性表单 → TaskDraft/Plan → 人工批准 → SQLite task → Adapter status →
两个标准 ArtifactManifest。P1-005 checkpoint 当时没有任务列表；用户后续确认的
D-007/P2-001 只补充了 scope-bound 持久任务发现和重开。完整 P2 的取消、lease、重试、
自动 reconciliation 和 SSE 仍未实现，也没有 P3 DAG 或 P4 Agent Planner。

## 1. 产品流程

1. Web 从同源 session endpoint 取得服务器绑定的 scope、P1 capability 和进程生命周期内的
   CSRF token；服务重启后必须重新取得；
2. 从 immutable Catalog 读取唯一受支持的 `marmousi_94_288@1.0.0` 与当前
   `deepwave.acoustic_fwi@1.3.0`，页面只展示无路径 metadata；旧算法 `1.0.0`/`1.1.0`
   四参数快照和 `1.2.0` 六参数快照均保持不可变，只供已有任务严格读取，不供新
   Guided 任务选择；当前 `1.3.0` manifest 只广告
   `acoustic_fwi_2d` 与 `fwi_smoke|fwi_demo`，不广告 legacy Worker/MCP `forward`；
3. 浏览器只提交九个字段：`goal`、dataset ID/version、`preset`、`device`、`iterations`、
   `seed`、`optimizer`、`learning_rate`；学习率是严格十进制字符串，服务器将其缩放
   1000 倍为整数 `learning_rate_milli`，再组装完整 TaskDraft、资源上限、单节点 Plan
   和 node idempotency key；
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

D-007/P1-007 在不改数值核心和人工批准边界的前提下增加 Adam/SGD 与学习率选择，
因公共 plan hash 域禁止 JSON float，所以使用 contract minor `1.1.0`。`1.2.0`
Algorithm/Adapter 是不可变的六参数历史快照；当前新提交以 Algorithm/Adapter
`1.3.0` 固定最终一致的 manifest：FWI-only，iterations `1..10000`，seed
`0..2147483647`，并按 optimizer 条件约束学习率。Adam 允许 `0.1..100`，SGD
允许 `100000..1000000000`，最多三位小数。
页面把 Adam LR=10 标为固定 Marmousi 已验证基线，Adam LR=2 标为微型 CPU 证据的
保守检查，SGD LR=10000000 只标为实验性校准起点。`gradient_clip_quantile=0.98`
是当前版本固定且可见的值，不是本轮可编辑参数。

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
任务或 Worker。浏览器在 approve 未取得合法成功 projection 时（包括结构化 4xx、5xx 或
网络中断）保留原 key、禁用自动重发，先用 GET 查询；若查到 approval 已持久化但 submit
尚未入队，当前页面仍持有原 key 时才提供
由用户显式点击的安全重放。这不是 P2 task retry。

P1-005 checkpoint 只保证持有 `task_id` 时可在服务重启后查询。D-007/P2-001 后，
页面用 scope-bound SQLite 分页 API 恢复左栏任务索引，并允许用户按 `task_id` 重开。
列表 GET 不触发 Adapter refresh；重开单任务才走原有 GET/status 路径。页面恢复后如果
已批准任务缺少原 `Idempotency-Key`，只读 fail closed，不为重发生成新 key。

## 3. 同源 HTTP API

固定前缀：`/api/scientific-runtime/v1`。JSON 成功 envelope 为
`{"ok":true,"data":...}`；错误只返回稳定 code 和脱敏 message。

| Method | Path | 作用 |
|---|---|---|
| GET | `/session` | 唯一不要求 CSRF 的 endpoint；返回 scope/capability/token |
| GET | `/catalog` | 返回当前 scope 可执行的固定 Dataset/Algorithm |
| GET | `/tasks?limit=20[&cursor=...]` | P2-001；按 scope 返回持久任务摘要，不刷新 Adapter |
| POST | `/tasks` | 九字段表单创建 draft + plan |
| PUT | `/tasks/{task_id}/draft` | `expected_revision` + 九字段 CAS 修订 |
| POST | `/tasks/{task_id}/approve` | 批准当前 `plan_hash` 并 submit |
| POST | `/tasks/{task_id}/abandon` | body `{}`；只放弃 pre-runtime 草稿 |
| GET | `/tasks/{task_id}` | 查询并执行一次只读 Adapter status refresh |
| GET | `/tasks/{task_id}/events` | `after_sequence`/`limit` 分页读不可变事件 |
| GET | `/tasks/{task_id}/artifacts` | Succeeded 后返回恰好两个 manifest |
| GET | `/tasks/{task_id}/artifacts/{artifact_id}` | 受控下载 NPY/CSV |

当前浏览器的 create/revise mutation 始终发送完整九个 form 字段，revise 另带
`expected_revision`。为保持既有 `/v1` loopback 客户端兼容，服务端也接受 form 字段集合
精确等于历史七字段的请求（revise 仍要求 `expected_revision`）。服务端先用不可变 1.0/1.1
composer 重建历史候选，并要求 scope/operation/key/request hash 全部命中 durable ledger；
命中时返回原任务当前 aggregate，不改写旧 Draft/Plan。没有历史记录时才确定性补入
`optimizer=adam`、`learning_rate=10`，按当前 `1.3.0` 生成六参数 Draft/Plan。只提供
optimizer 或 learning rate、其他部分/混合 form shape 都返回 422；该 wire compatibility
不会允许旧 `1.0.0`/`1.1.0`/`1.2.0` Algorithm 发起新 dispatch，同 key 不同 payload
仍冲突。

除 `/session` 外都要求 `X-Workbench-CSRF`。所有 mutation 还要求 exact loopback `Origin`、
`Idempotency-Key`、UTF-8 `application/json` 和精确 `Content-Length`；JSON 拒绝重复 key、NaN/
Infinity、未知字段和超过 64 KiB 的 body。Host 必须等于部署配置的浏览器 authority，不返回
CORS header。浏览器不能提供 project/principal、任意路径、shell、Adapter handle 或 Worker
job ID。任务列表 `limit` 只允许 `1..50`，opaque cursor 必须是服务器返回的 canonical
base64url token；跨 scope、不存在或非规范 cursor 统一拒绝，不泄露其他任务是否存在。

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

1. 点击页面中的 **Smoke CUDA**（无 CUDA 时用 **Smoke CPU**）。自然语言请求如果没有
   明确写 CUDA/GPU，表单安全默认为 CPU，页面必须提示且绝不自动切换已确认的设备；
2. 确认左栏“持久 FWI 任务”从 SQLite 加载，页面出现 session scope 和 immutable
   Catalog metadata；当前 Catalog 不应把 `forward` 显示为可选 preset；
3. 建议首次填写 preset `fwi_smoke`、device `cuda`、iterations `1` 或 `2`、任意合法
   seed、optimizer `adam`、learning rate `10`。确认卡应显示这是已验证基线，并显示
   固定 gradient clip quantile `0.98`；
4. 分别点击建议卡 Adam LR=2 和 SGD LR=10000000。前者应标为保守微型检查，后者
   应标为实验性校准起点而非收敛推荐；选择后应同步更新优化器和学习率输入；
5. 点击“生成 Draft / Plan 确认卡”。预期获得真实 `task_id`、revision 1、单节点 plan、
   `AwaitingApproval`和六参数 Draft/Plan；学习率 10 持久为 `learning_rate_milli=10000`，
   此时没有 Worker job；
6. 点击“修改”，改变 seed、optimizer 或 learning rate 后再次生成。预期 revision 增加、
   `plan_hash` 改变，旧 approval 不能用于新 plan；
7. 输入 Adam LR `0.099`、SGD LR `99999`、科学计数法、NaN 或超过三位小数。预期在创建
   Draft 前明确拒绝，不产生 task、dispatch 或 Worker job；
8. 若测试“放弃草稿”，点击放弃。预期 task 为 `Cancelled`，页面明确说明不是运行中取消；
   随后重新打开 Smoke 创建另一 task；
9. 点击“批准运行”。预期 dispatch 为 `dispatched`，SQLite 状态依次为 Queued、Running、
   Succeeded（很快时可跳过页面上中间帧）；失败必须明确显示 Failed，不伪装成功；
10. 运行中先向上滚动阅读其他内容，等待至少两次迭代状态刷新。预期页面保留原阅读位置；
    再手动滚到底部，后续轮询才继续跟随底部；
11. 点击任务卡 `×`。预期只关闭视图且明确提示“不取消任务”；同一 task 仍出现在左栏，
    点击可重开并继续读取状态。刷新整个页面后左栏仍能从 SQLite 发现它；
12. 成功后应出现恰好两张标准结果卡：反演模型 NPY 与损失 CSV。两个下载均从同源受控
    endpoint 返回 attachment，不使用旧的 raw job 路径；
13. `./stop.sh` 后重新启动，左栏应重新发现原 task，重开后能读到相同终态、已落库事件和
    artifacts。这是发现/重开验收，不是 cancel、retry、reconciliation 或 SSE 证据。

## 6. 验证证据与限制

原 P1-005 自动化覆盖 fresh/v3→v4 migration、hash/schema/trigger tamper、并发幂等、CAS、批准绑定、
pre-runtime abandon、状态单调/轮询去重、精确 NPY+CSV、受控下载、Host/Origin/CSRF/JSON/
HTTP framing、UI XSS/路径和批准防绕过。UI 与真实 C++ planner 的 75,240 条组合差分中，
`backend submit && UI false` 为 0；实际 HTTP sync/stream 和 gRPC bridge 绕过语句也都在 MCP
执行前返回 Guided 提示，且未新增 legacy job。现场验收另使用固定 venv 完成一次一迭代
CUDA：Queued → Running → Succeeded，验证连续 RunEvent、两个 artifact 的 size/SHA-256、
逻辑 artifact location 和 Worker job ID 脱敏，并在 Web 重启后重新查询同一 task。临时
task/job identity 不写入本文。

D-007/P1-007/P2-001 当前自动化证据为 Scientific Runtime 控制面 157/157、固定
venv Worker 27/27、Web Python 29/29、Node UI 行为和 `git diff --check` 全部 PASS。这些
覆盖 contract minor、Algorithm/Adapter 版本兼容、优化器/学习率边界、scope/cursor 列表、
左栏重开和滚动行为；Node 另遍历 125 条任务的 7 个可见页面，证明 cursor 不会越过
被客户端丢弃的任务。真实 loopback HTTP 现场验收确认页面提供优化器卡、CPU 默认警告和
持久左栏，旧 500 次 CPU/Algorithm 1.1 任务可由列表重开为 Succeeded 并读取两个 artifact；
非法 optimizer、精度过高学习率和 SGD 越界值均返回 422 且 task 数不变。当前 1.3 的
CUDA/SGD/LR=10000000 两次迭代任务实际经历 Queued → Running → Succeeded，两个 artifact
的字节数和 SHA-256 均匹配；Worker 指标为 NaN/Inf=0、非零模型更新、GPU 峰值约
713.3 MiB。新页面 session/list 后仍能发现并重开该终态任务。因此本有界后续切片为
**Verified**；两步校准只证明有限执行与模型更新，不是长程收敛推荐。

仍属 Pending：运行中 cancel、timeout、lease/heartbeat、retry、自动 reconciliation、SSE、
不依赖用户点击的运行意图/事件流恢复、DAG、Agent Planner、通用算法 SDK。Adapter provenance 仍是
development，不据此声明跨环境 bitwise 可复现或真实数据上的科学效果。
