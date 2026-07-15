# Scientific Runtime P1.1c 原子提交与一次性派发

<!-- scientific-runtime-p1-submit: v1 -->

- 对应决策：`D-003`
- 实现切片：`P1.1c`
- 实现状态：**Implemented / Verified — atomic admission and one-shot dispatch**
- checkpoint 阶段状态：P1 当时仍在进行；Guided Web、P2 可靠恢复和 P3 DAG 尚未实现

本切片把已验证的 SQLite Task Store、Registry 和固定 Deepwave Adapter 接成一个最小后端
闭环。浏览器或 LLM 只能提交 task identity、current approval identity 和独立的 mutation
idempotency key；算法、数据、参数、资源、节点 key、Adapter 请求和 fingerprint 都从服务端
持久状态与受信任 preflight 派生。

## 1. 文件与责任

| 文件 | 责任 |
|---|---|
| `scientific_runtime/migrations/0003_submit_dispatch.sql` | v3 升级、不可变 intent/claim/outcome、typed submit-idempotency link |
| `scientific_runtime/task_store.py` | 同事务 Gate context、预算消费、首事件/Queued、intent 和 replay |
| `scientific_runtime/task_service.py` | 完整 Gate、P1 capability guard、submit API 和 crash-safe one-shot 顺序 |
| `scientific_runtime/task_dispatcher.py` | 固定代码映射到 Deepwave Adapter；禁止动态 import、path、shell 和 config 注入 |
| `scientific_runtime/fwi_adapter.py` | `validate` 暴露 preflight fingerprint；handle 暴露实际 dispatch fingerprint |

SQLite 仍是 task/status/event 真源；Adapter 私有索引只做 Worker submit 幂等。`FWI_RUN_ROOT`
仍是受控输出状态，不是可执行 job inbox，本切片没有 watcher。

## 2. 原子 admission

`TaskService.submit_task` 的外部输入只有：

```text
task_id + authenticated project_id/principal_id + current approval_id
        + submit-operation idempotency key
```

submit operation key 与 PlanGraph node idempotency key 是两个不同域。前者按
`(project, principal, submit_task, key)` 去重，请求 hash 只绑定稳定的 task/scope/approval；
后者来自不可变 plan，只传给 Adapter。

Store 在一个 `BEGIN IMMEDIATE` 中按以下顺序执行：

1. 先读取 submit replay；精确 replay 不重新检查 expiry/budget/status，也不再次 preflight 或派发；
2. 重读 current task/draft/plan/approval 和 approval budget；
3. 用同一连接读取 server-owned Dataset/Algorithm Registry snapshot；
4. 执行完整 deterministic Gate；
5. 执行固定单节点 FWI capability guard，并核对 Registry manifest 与打包 manifest 完全一致；
6. guarded `tasks_used + 1`；
7. 写 immutable dispatch intent、submit idempotency/typed link 和 sequence 1 `task_queued`；
8. 更新 task 为 `Queued`，在事务内重新解码并交叉检查聚合后提交。

任一步失败都会一起回滚预算、intent、idempotency、event 和状态。Adapter preflight 在事务前
完成；Adapter `submit`/Worker `Popen` 只会在 SQLite commit 后发生。

P1.1c/D-006 checkpoint 的 capability 精确限制为：单节点、无依赖、`acoustic_fwi_2d`、
`deepwave.acoustic_fwi@1.1.0`、`marmousi_94_288@1.0.0`、`fwi_smoke|fwi_demo`，以及固定
`fwi.deepwave_adapter@1.1.0`。原始 P1.1c checkpoint 使用的 Algorithm/Adapter `1.0.0` 快照
保持不可变；D-006/P1-006 通过 minor version 扩展显式迭代上限。D-007 的 `1.2.0`
Algorithm/Adapter 是不可变的六参数历史快照，和 `1.0.0`/`1.1.0` 一起仅保留既有
收据的严格读取。D-008 后当前新 dispatch 精确使用 Algorithm/Adapter `1.4.0` 与六参数 FWI
plan；它保留 1.3 的参数策略，并把六张固定 PNG 加入声明输出。其 manifest 只允许
`acoustic_fwi_2d`、`fwi_smoke|fwi_demo`、iterations
`1..10000`、seed `0..2147483647` 及 Adam/SGD 各自的条件学习率边界，不广告 legacy
`forward`。多节点即使通过通用 Gate 也被拒绝，留给 P3。

## 3. Fingerprint 语义

公共 RunEvent Schema 要求 `task_queued` 也带完整 fingerprint，但 Worker 尚未启动。为避免
伪造 runtime provenance：

- Adapter `validate` 做无 job/artifact 副作用的 live preflight，返回 development fingerprint；
- queued event 不带 `node_id`，extension 明确标记
  `fingerprint_basis=adapter_preflight`、`worker_runtime_started=false`；
- preflight fingerprint 被 hash-bound 到 immutable intent，但不冒充实际 Worker fingerprint；
- Adapter 成功 handle 返回其实际 dispatch fingerprint；第一个 node runtime event 必须与该
  receipt 完全一致，之后同节点 fingerprint 继续由事件存储边界保持不变。

因此 preflight 与实际 dispatch 间的环境变化不会被静默抹平。provenance 仍是 development，
不升级为跨环境 bitwise 可复现声明。

## 4. Dispatch 状态与 crash window

事务提交后，服务在第二个短事务写一次性 claim，再调用固定 Adapter，最后写不可变 outcome：

```text
pending -> dispatching -> dispatched
                       \-> reconciliation_required
```

- `pending`：admission 已提交，但尚未取得 claim；
- `dispatching`：已开始一次派发，可能因进程丢失而无法判断 Worker/receipt 的最终状态；
- `dispatched`：受控 handle 和实际 fingerprint 已持久；
- `reconciliation_required`：Adapter 返回稳定错误，但 P1 不猜测 Worker 一定未启动。

P1 对 pending/dispatching/reconciliation 状态都不自动重发、退款、取消或标记 task Failed。
精确 API replay 只返回持久状态。claim 只是防止当前 one-shot 路径并发双调用，不是 lease、
heartbeat、retry 或进程恢复；这些仍属于 P2。

无法在 P1 消除的两个窗口被显式保留：SQLite commit 后、Adapter 前崩溃会留下 pending；
Worker 启动后、receipt 前崩溃会留下 dispatching。Adapter 自身的 plan-scoped idempotency仍提供
去重证据，但本切片不会借此实现自动 reconciliation。

## 5. Migration 与损坏边界

v3 不修改已部署的 v1/v2 migration checksum。fresh、v1→v3、v2→v3 和并发升级均使用连续
版本记录与完整 schema manifest。由于历史版本没有合法产品 submit 入口，若 v2 数据库已含
runtime task、已消费预算或 submit idempotency，v3 无法可靠重建 intent，升级会整笔回滚，
而不是编造 provenance。

intent、claim、outcome 和 typed link 都不可更新/删除。读取时重新计算 JSON hash，并交叉检查
task scope、current plan/hash、approval、node key、Adapter identity、request、fingerprint、handle
和 idempotency response。runtime task 缺少 current dispatch intent 时 fail closed。

## 6. 验证与剩余工作

Scientific Runtime 聚焦组合当前为 117/117：contract 28、Registry 24、TaskService 47、Adapter
18。测试覆盖正常 admission、完整回滚、锁等待跨 expiry、manifest 漂移、单节点能力门、同 key/
不同 key 并发、跨重开 replay、commit 可见后才派发、Adapter 错误脱敏、两个 crash window、
fingerprint receipt 绑定、hash-consistent intent tamper、固定 Dispatcher 映射、v1/v2/v3 升级和
不可解释旧 runtime 拒绝。

本 checkpoint 当时仍 Pending 的部署 HTTP/Guided Web、Adapter status/collect 产品轮询与
结果展示已由 P1-005 验证。当前仍 Pending：完整 P2
lease/heartbeat/cancel/retry/reconciliation/SSE、P3 DAG，以及 P4 Agent Planner。
