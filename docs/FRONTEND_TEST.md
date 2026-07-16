# Web 前端端到端测试

本文给第一次运行项目的使用者一条可执行的验收路径：配置本地 secret、核对固定模型、一键启动，
优先走完 P1 Guided 的确认/修改/批准/状态/八项结果闭环，验证对话与任务独立、任务回收站和
有界永久删除、六张标准图片及 P2-005A 无浏览器依赖的持续状态追赶，再可选检查旧 MCP/FWI
Result 兼容性，最后一键关闭。

只在受信任的本机或受控容器中执行。当前 Web 没有用户认证，不要把 8080 或 5000 端口暴露到公网；不要在终端、截图、聊天、Issue 或日志粘贴内容中输出 API Key。

## 1. 进入仓库并准备本地配置

```bash
cd /path/to/agent-communication-main-v2
cp .env.example .env
chmod 600 .env
```

用本地编辑器打开 `.env`，选择实际使用的 `LLM_PROVIDER`，只填写该 provider 对应的一项 key。不要使用 `echo KEY=...`，因为命令可能进入 shell history；也不要执行 `cat .env` 或把 `.env` 提交到 Git。

确认权限和忽略规则，不显示文件内容：

```bash
stat -c '%a %n' .env
git check-ignore .env
```

预期权限为 `600`，`git check-ignore` 输出 `.env`。

如果还要验收左侧的本地 Embedding 状态，首次先由你明确执行一次模型准备：

```bash
deploy/scripts/setup_embedding.sh
```

然后在 `.env` 中设置 `ROUTING_MODE=agent-rag`、`EMBEDDING_PROVIDER=local` 和
`ENABLE_LOCAL_EMBEDDING=auto`。日常 `./start.sh` 只读取已经缓存的模型，不会静默下载；
默认在 CPU 上运行，避免和 Deepwave 抢占同一张 GPU。FWI 本地文档检索本身不依赖这项
可选服务。

## 2. 核对固定模型三件套

当前 MCP/Web 只允许 `marmousi_94_288`，需要：

```text
/root/fwi-data/models/marmousi_94_288.mat
/root/fwi-data/models/marmousi_94_288.npy
/root/fwi-data/models/marmousi_94_288.json
```

先只检查文件是否可读：

```bash
test -r /root/fwi-data/models/marmousi_94_288.mat
test -r /root/fwi-data/models/marmousi_94_288.npy
test -r /root/fwi-data/models/marmousi_94_288.json
printf '模型三件套可读\n'
```

再核对两个数据文件的 SHA256：

```bash
sha256sum \
  /root/fwi-data/models/marmousi_94_288.mat \
  /root/fwi-data/models/marmousi_94_288.npy
```

预期分别为：

```text
4e1a50d4afc5c81016e775fe99c0ac716b975701fcb89885da6f4ce433dc4357  ...mat
b80918e3a609a679f16a47dd30978812d80e4fab1fcbd5ce692d9ca97022a688  ...npy
```

不要修改这三份文件。shape、dtype、轴顺序、物理单位和 sidecar 字段的完整契约见 [MODEL_GUIDE.md](MODEL_GUIDE.md)。

## 3. 一键启动

首次运行或希望清理后重编译：

```bash
./start.sh --rebuild
```

日常启动：

```bash
./start.sh
```

如果这次要测试页面左侧的 gRPC 模式，请改用：

```bash
./start.sh --grpc
```

脚本会依次：

1. 使用仓库外的 FWI Python 环境检查 PyTorch、Deepwave 和模型双哈希；
2. 本地配置并编译根 C++ 项目；
3. 本地配置并编译 MCP Server 和插件；
4. 启动 Registry、Agent、Orchestrator 和 MCP；
5. 启动 Web，并等待 HTTP 健康检查通过。

编译结果留在本地 `build/` 和 `mcp_server_integrated/build/`，不会作为部署制品提交。启动成功应显示：

```text
Web UI:       http://127.0.0.1:8080
Orchestrator: http://127.0.0.1:5000
```

额外做一次只读健康检查：

```bash
curl --fail --silent http://127.0.0.1:8080/ >/dev/null \
  && printf 'Web OK\n'
curl --fail --silent \
  http://127.0.0.1:5000/.well-known/agent-card.json >/dev/null \
  && printf 'Orchestrator OK\n'
```

## 4. 打开 Web 并选择通信模式

浏览器访问：

<http://127.0.0.1:8080>

普通 `./start.sh` 启动后请选择 **HTTP**，当前端点显示 `HTTP :5000`。gRPC 按钮会在
50052 健康检查失败时禁用，避免向不存在的服务发送请求。

如果使用 `./start.sh --grpc`，等待 gRPC 按钮变为可用后可以切换到 **gRPC**。浏览器
请求先到 50052 Web bridge，再由 bridge 调用 50051 的原生 gRPC 服务；可用下面命令
先独立检查 bridge：

```bash
curl --fail --silent http://127.0.0.1:50052/health
```

## 5. 验收 P1 Guided Web 闭环

首先确认使用默认 loopback 地址。P1 Guided API 在 `WEB_HOST=0.0.0.0` 下会 fail closed，
不要用容器 wildcard 绑定做这项验收。

### 5.1 用执行请求打开确认卡

可点击页面快捷入口 **Smoke CUDA**（无 CUDA 时选 **Smoke CPU**），也可在聊天输入：

```text
帮我做个 Marmousi FWI，使用 CUDA，迭代1次。
```

预期出现 Guided FWI 表单，而不是旧 `fwi_job_submitted` 结果或一段示例代码。表单应显示：

- session scope 与 capability；其中 `continuous_status_supervision=true`、
  `supervisor_leases=true` 只表示 Web 控制面的 observation-only 状态泵，
  `startup_dispatch_recovery=false`、`automatic_reconciliation=false` 仍应为 false；
- 固定 `marmousi_94_288@1.0.0` 数据和当前 `deepwave.acoustic_fwi@1.4.0` 算法；
- 只有实验目标、注册数据、preset、device、iterations、seed、optimizer 和 learning rate
  等受控字段，没有服务器
  路径、shell 或 Worker job ID 输入。
- 当前标准 manifest 只提供 `fwi_smoke|fwi_demo` 反演 preset，不把 legacy Worker/MCP
  `forward` 显示为可选 Algorithm capability。

自然语言请求如果没有明确写 CUDA/GPU，表单安全默认 CPU 并给出提示；系统不会
在创建或运行后自动切换已确认 task 的 device。

浏览器始终发送完整九个 form 字段，revise 另带 `expected_revision`。`/v1` API 仅为既有
loopback 客户端保留精确历史七个 form 字段（revise 仍要求 `expected_revision`）：同 key
升级重放会用旧 1.0/1.1 composer 重建并精确匹配 durable request hash；未命中历史记录才补为
Adam/LR 10 后按当前 `1.4.0` 处理。只提供 optimizer/learning rate 之一或其他部分 form
shape 必须返回 422，同 key 不同 payload 必须冲突。

纯理论问句“什么是 FWI？只解释，不要运行任务。”仍应走聊天，不打开执行卡。

### 5.2 创建、修改与放弃 pre-runtime 草稿

建议验收值：

| 字段 | CUDA 建议值 | CPU 建议值 |
|---|---:|---:|
| preset | `fwi_smoke` | `fwi_smoke` |
| device | `cuda` | `cpu` |
| iterations | `1` | `1` |
| seed | `20260715` | `20260715` |
| optimizer | `adam` | `adam` |
| learning rate | `10` | `10` |

点击 **生成 Draft / Plan 确认卡**。预期：

- 显示真实 `task_id`、draft revision 1、单节点 plan 和 64 位 `plan_hash`；
- 确认卡显示 Adam/LR 10 为固定 Marmousi 已验证基线、`gradient_clip_quantile=0.98`
  为版本固定值，持久 Draft/Plan 中的学习率为 `learning_rate_milli=10000`；
- task 为 `AwaitingApproval`，页面明确显示“批准前不会进入运行队列”；
- `FWI_RUN_ROOT` 没有因为创建确认卡而新增 Worker job。

选择 **SGD 校准起点** 时，卡片应说明 LR `10000000` 已通过固定 Marmousi CUDA
两步 finite/model-update 校准，但仍是实验性起点而非长程收敛推荐。

点击 **修改**，把 seed 改为 `20260716`，再点击 **重新生成确认卡**。预期 revision
变为 2，`plan_hash` 改变，仍为 `AwaitingApproval`。

如要验收放弃，点击 **放弃草稿**。预期任务变为 `Cancelled`，页面明确说明只终止了
pre-runtime 草稿，没有发送运行中 cancel。放弃后重新打开 Smoke 入口，创建另一个任务
继续下一步。

### 5.3 验收 10000 次上限而不启动长任务

把 iterations 改为 `10000`，只点击 **生成 Draft / Plan 确认卡**，不要批准。预期：

- 表单接受整数 10000，Draft/Plan 保留该值并停在 `AwaitingApproval`；
- 当前算法版本为 `deepwave.acoustic_fwi@1.4.0`；旧 `1.0.0` 快照仍保持上限 100，
  `1.1.0` 保留 10000 上限，`1.2.0`/`1.3.0` 保留不可变六参数历史快照；旧版均只供
  严格读兼容，不供新 Guided 任务选择；
- 当前 `1.4.0` manifest 必须同时体现 FWI-only、iterations `1..10000`、seed
  `0..2147483647`、Adam/SGD 条件学习率边界，以及两个数值输出和六个 figure 输出；
- 页面显示长任务警告，且创建确认卡不会新增 Worker job；
- 点击 **放弃草稿** 后变为 `Cancelled`。

再输入 `10001`、`2.5` 或 `-3`。预期前端明确拒绝，不能创建 Draft、Plan 或 Worker job。
10000 只是显式校验上限；默认 smoke/demo 仍为 2/5 次，P1 没有运行中取消、checkpoint、
retry 或完成时间保证。

### 5.4 人工批准、真实状态和结果

在新任务的确认卡点击 **批准运行**。预期：

1. dispatch 进入 `dispatched`，task 为 `Queued`、`Running`、`Succeeded` 之一；任务较快时
   页面可能看不到每个中间帧，但终态不会被伪造；
2. 状态来自 SQLite task + Adapter GET 查询，页面不显示 Adapter handle 或 Worker job ID；
3. `Succeeded` 后恰好显示八张 ArtifactManifest 卡：反演模型 NPY、损失 CSV，以及真实模型、
   初始模型、反演模型、模型误差、炮集和损失曲线六张 PNG；
4. 每项都显示 size 与 SHA-256；图片应直接形成两列结果画廊，所有读取/下载只走
   task-scoped 受控 endpoint，不使用 `/fwi-artifacts/<job_id>/...` 路径，也不显示 Worker job ID；
5. 单张图片加载失败时只在该卡显示错误，其余结果仍可查看；关闭或切换任务后 Blob URL 被释放。

P2-005A 启用后，页面 GET 不再是运行中 task 状态进入 SQLite 的唯一触发源。可关闭任务卡或
停止页面轮询，等待超过一个 Supervisor poll 周期后再重开：已有 `dispatched` task 的新状态仍应
已写入 SQLite。这个检查不得创建 pending/no-record task 来期待后台首次派发；Supervisor 没有
launcher/dispatch 能力。

如 approval 已持久化但 submit 预检失败，页面应停止自动轮询，显示
**继续已批准提交（复用原 Idempotency-Key）**。这是由用户显式重放同一 approve/submit
mutation，不是 P2 task retry。approve 即使返回结构化 4xx，只要没有合法成功 projection，
页面也必须先保留原 key 并进入 GET 审计态，期间不能关闭/重开来清掉该 key。Artifact GET
临时失败或数量不符合当前 Plan 时，应显示
**重新获取 artifacts（GET）**，不重跑 Worker。

### 5.5 轮询不抢占滚动位置

任务运行时向上滚动阅读其他内容，等待至少两次状态刷新。预期 `scrollTop` 保持，
不会每次被拉到最下方。用户自己滚回底部后，后续轮询才继续跟随底部；显式
打开或重开 task 时可一次性把任务卡展示到视口。

### 5.6 关闭卡片、左栏找回与重启边界

点击 Guided 任务卡的 `×`，预期页面明确说明只关闭视图、不取消任务；同一 task
仍出现在左栏“持久 FWI 任务”中，点击后可重开。刷新整个页面后，左栏应再次从
SQLite 发现 task，而不是从聊天记录或 `localStorage` 猜测。

`./stop.sh` 后用 `./start.sh --no-build` 重启，左栏仍应发现原 task；重开后 SQLite 终态、
事件和 artifact 仍可查询。P2-004 还会在 socket bind 成功但 API 可用前，只读收养 Adapter
已 durable `launched` 但 SQLite outcome 丢失的 current 1.4 exact receipt，并对 dispatched task
做一次状态追赶；pending/no-record/preparing/launching 不首次派发，已有
`reconciliation_required` 仍只读 fail closed。P2-005A 会在 recovery 后、listen/publish 前取得
scope-level fenced 控制面 lease；另一存活 Web owner 已持有该 scope 时，新 Web 应在提供 API 前
失败。lease 只围栏后台 status commit，不是 Worker fenced capacity/attempt lease 或 heartbeat，
也不代表 cancel、retry、pending 调度、完整 reconciliation 或 SSE 已实现。

### 5.7 验证对话、任务引用与删除边界

1. 输入纯理论问题并获得回答。预期不会创建 task，说明“对话”不是任务容器；
2. 输入执行型 FWI 请求。预期原文字先保留在对话中，并出现“仅打开独立草稿，尚未创建/运行”
   的说明；生成 Draft/Plan 后当前对话才出现 task 引用卡；
3. 新建另一对话，从左栏打开同一 task 并选择关联。预期两个对话都可引用它；从任一对话
   “移除引用”不会删除或取消 task；
4. 删除一个对话。确认框必须明确这只删除本浏览器副本且不影响独立任务；刷新后 task 仍在
   SQLite 左栏。服务器 transcript 仍按既有 TTL 管理，当前页面不承诺服务器永久删除；
5. 对 Succeeded/Failed/Cancelled 任务点击“删除”。预期它从 active 视图消失、出现在任务
   回收站，详情、事件和结果仍可读取；点击“恢复”后回到 active，且不会重新运行 Worker；
6. Queued/Running/Waiting/Retrying 或结果未知任务不得出现可用删除操作；AwaitingApproval 要先
   “放弃草稿”成为 Cancelled。这里的普通“删除”仍只是可恢复 Trash，不会清理本地结果；
7. 对一个可丢弃的回收站任务点击红色“永久删除”。确认框必须要求逐字输入完整 `task_id`，
   并说明本机 Worker 运行目录/结果不可恢复、SQLite 任务审计和对话仍保留；输入不匹配时不得
   发请求；
8. 对已运行的终态任务确认后，预期其专属本地目录（config、日志、status、NPY、CSV、PNG）
   被删除，任务从 active/trash 列表消失，不能 Restore、查看详情或重新读取 artifact；对仅
   abandon 的 pre-runtime 任务，响应应说明未曾创建本地运行目录（`not_created`）；
9. 永久删除不得级联删除 conversation/message。所有引用该 task 的对话卡改为“任务已永久
   删除”，用户可以单独移除失效引用；已下载到浏览器外部的副本不受本功能控制；
10. 若响应中断、结果未知，回收站行显示“继续永久删除”且“恢复”禁用；再次确认只继续同一
    purge，不启动 Worker。为避免耗时，不必新跑真实 FWI：可用现有可丢弃终态任务做一次人工
    删除；自动化已用临时 Worker 树验证实际文件清理、崩溃恢复和符号链接边界。

刷新页面后，对话 task 引用卡应先显示“状态需从 SQLite 刷新”，不能用 `localStorage` 中旧的
成功/进度缓存冒充事实；打开任务后再显示服务器返回的当前状态。

## 6. 验收 legacy MCP/FWI Result 兼容性（自动化）

正常页面中的执行型 FWI 快捷入口和聊天文本现在有意统一进入 Guided 确认卡，不能再用手工
聊天绕过批准去触发旧 MCP 提交。旧 `fwi_job_submitted` renderer、六张 PNG、状态查询和
`/fwi-artifacts` 路由只作为兼容边界保留，由回归测试验收：

两条普通聊天 transport 还固定携带 `allow_legacy_fwi_submit=false`。后端根据 actual tool
plan 在 MCP 执行前拒绝旧 `fwi_submit_demo`，因此前端 classifier 将来发生漂移时也不会直接
提交；该 caller-carried 字段用于本机 Web 产品策略，不是用户认证或远程权限边界。

```bash
python3 -m unittest discover -s web/tests -p 'test_*.py' -v
node web/tests/ui_message_rendering_test.js
```

当前预期为 Web Python 45/45 PASS，UI Node 输出
`ui message rendering tests passed`。这组测试同时证明执行型文本先进入 Guided、纯理论文本仍
走聊天、旧结果不会把无合法回执的说明或代码误标成已提交，并覆盖旧 artifact 路径/后缀边界，
以及 Web 的 lease-before-listen、Supervisor 自我隔离、信号清理、零请求关闭和 bounded drain。
如要人工查看旧 Worker 目录、六张 PNG 或 `metrics.json`，只使用已经由兼容 MCP 或 Worker
CLI 创建的已知 `job_id`；这不是 P1 Guided 的验收结果。当前 Guided 页面通过任务作用域
ArtifactManifest 展示标准 NPY/CSV 和六张 PNG，不复用 legacy URL。

## 7. 验收本地知识库、公式与“不启动任务”

在终端记录当前 job 目录数量：

```bash
BEFORE="$(find /root/fwi-runs -mindepth 1 -maxdepth 1 -type d -name 'fwi-*' | wc -l)"
printf 'before=%s\n' "$BEFORE"
```

回到 Web 输入：

```text
什么是 cycle skipping？请优先使用本地资料，解释半周期判据并给出公式。只回答理论，不要运行任务。
```

预期：

- 回复在资料支持的结论后出现类似 `【本地资料：Cycle Skipping（周波跳跃）】` 的标注；
- 半周期判据显示为排版后的行内或块级公式，而不是一整串未处理的 LaTeX；
- 代码块中的 `$...$` 仍按代码显示，普通金额不会被误判为公式；
- 如果外部 CDN 暂时不可用，公式区域会明确保留经过转义的原始 TeX，不会空白；
- 不出现 `fwi_job_submitted`，也不生成新 job 目录。

左侧 Embedding 仅表示 Agent-RAG 的 AgentCard 语义选路状态。启用了上一节的可选配置时，
应显示“运行中 · 1024维 · CPU”；未启用时会显示“未启用（FWI 知识库仍可用）”。也可从
Web 的同源、脱敏健康端点检查，不要让浏览器直接访问 6000：

```bash
curl --fail --silent http://127.0.0.1:8080/api/embedding-health \
  | /root/.venvs/cpp-fwi-agent/bin/python -m json.tool
```

回答完成后在终端检查：

```bash
AFTER="$(find /root/fwi-runs -mindepth 1 -maxdepth 1 -type d -name 'fwi-*' | wc -l)"
printf 'after=%s\n' "$AFTER"
test "$BEFORE" = "$AFTER" && printf '理论问题未创建任务：PASS\n'
```

理论回答不应出现新的 `fwi_job_submitted` 或 job_id。再用“什么是 FWI？只解释概念，
不要运行任务。”复测基础资料也可以，但不要把“Embedding 在线”误当成本地 FWI 资料是否
可用的唯一判断。

## 8. 常见失败排查

### 启动时模型或哈希失败

不要修改 sidecar 来迁就错误文件。重新核对三件套来源和 SHA256，然后用 CPU 只做配置校验：

```bash
source /root/.venvs/cpp-fwi-agent/bin/activate
VALIDATE_CONFIG="$(mktemp)"
printf '%s\n' '{"preset":"forward","device":"cpu"}' > "$VALIDATE_CONFIG"
python -m fwi_worker validate --config "$VALIDATE_CONFIG"
rm -f "$VALIDATE_CONFIG"
```

### CUDA 不可用

```bash
/root/.venvs/cpp-fwi-agent/bin/python - <<'PY'
import torch
print("torch:", torch.__version__)
print("cuda available:", torch.cuda.is_available())
print("device:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU only")
PY
```

CUDA 不可用时在执行请求中明确写“使用 CPU”。不要为 Deepwave wheel 构建失败直接安装完整 CUDA Toolkit；先按 [DEPLOYMENT.md](DEPLOYMENT.md) 检查 PyTorch/Deepwave wheel 和容器 GPU runtime。

### Web 或 Agent 无法连接

```bash
curl -v http://127.0.0.1:8080/
curl -v http://127.0.0.1:5000/.well-known/agent-card.json
ss -ltnp | grep -E ':(5000|8080)\b'
```

日志位于：

```text
examples/ai_orchestrator/logs/
```

优先查看对应服务日志末尾。日志设计上不应包含 key，但分享日志前仍要自行检查和脱敏，不要连同 `.env` 打包。

### 任务进入 failed

```bash
/root/.venvs/cpp-fwi-agent/bin/python -m json.tool "$RUN_DIR/status.json"
/root/.venvs/cpp-fwi-agent/bin/python -m json.tool "$RUN_DIR/manifest.json"
tail -n 80 "$RUN_DIR/run.log"
```

保留明确失败原因；不要手改 `status.json` 或 `metrics.json` 伪造成功。常见原因包括模型哈希不一致、请求 CUDA 但设备不可见、非有限 loss/gradient、模型未更新或 demo loss 未下降。

### 图片缺失

确认任务已经 `succeeded`，`manifest.json` 中有六个 figure 条目，文件存在且 artifact URL 以同一个 job_id 开头。404 表示文件或 job 不存在；403 通常表示路径、安全后缀、符号链接或目录边界校验拒绝了请求。

## 9. 一键关闭

```bash
./stop.sh
```

脚本只停止本项目 PID 文件记录且身份匹配的进程，可以重复执行。关闭后验证：

```bash
if curl --silent --fail --max-time 2 http://127.0.0.1:8080/ >/dev/null; then
  printf '警告：8080 仍有服务响应，请用 ss 检查占用者\n'
else
  printf 'Web 已停止\n'
fi
```

Web 停止流程先关闭 listener，再 cooperative stop/release Supervisor，随后最多定界等待已有
Handler；脚本为 Web 预留 30 秒，超时后以 KILL 作为最终边界。任意底层 I/O 阻塞时不能假设
进程一定完成优雅 drain；异常退出后由 lease expiry 允许后继 owner 接管。KILL 不会取消或重启
Worker，也不会释放所谓 Worker 容量，因为 P2-005A 尚未实现这类 Worker lease。

运行结果保留在 `/root/fwi-runs`，不会因停止服务而删除。清理结果前先确认 job 不再需要；不要删除或覆盖 `/root/fwi-data/models` 中的原始模型。
