# Web 前端端到端测试

本文给第一次运行项目的使用者一条可执行的验收路径：配置本地 secret、核对固定模型、一键启动、从中文请求提交 FWI、查看六张图和真实指标，最后一键关闭。

只在受信任的本机或受控容器中执行。当前 Web 没有用户认证，不要把 8080 或 5000 端口暴露到公网；不要在终端、截图、聊天、Issue 或日志粘贴内容中输出 API Key。

## 1. 进入仓库并准备本地配置

```bash
cd /root/projects/project/agent-communication-main-v2
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

## 4. 打开 Web 并选择 HTTP 模式

浏览器访问：

<http://127.0.0.1:8080>

左侧模式选择器应选 **HTTP**，当前端点显示 `HTTP :5000`。页面会记住上一次模式；如果之前选过 gRPC，必须手动切回 HTTP。本教程不要求启动 gRPC bridge。

## 5. 依次测试四类中文请求

### 5.1 提交正演

在聊天输入：

```text
使用 marmousi_94_288 运行一个二维声学正演演示。
```

预期：

- 回复被解析为 `fwi_job_submitted`；
- FWI Result 面板显示严格格式的 `job_id`；
- 初始状态为 `queued` 或 `running`；任务很快时也可能在首次轮询前直接变为 `succeeded`；
- 任务最终明确进入 `succeeded` 或 `failed`，不会一直伪装成功。

### 5.2 提交两次更新 smoke

正演完成后输入：

```text
使用 marmousi_94_288 运行两次迭代的二维声学 FWI smoke test。
```

没有 CUDA 时明确要求 CPU：

```text
使用 CPU 和 marmousi_94_288 运行两次迭代的二维声学 FWI smoke test。
```

预期新任务的 `total_iterations` 为 2。smoke 的验收目标是 forward/backward、梯度裁剪和模型更新链路均为 finite；它不以高质量反演为目标。

### 5.3 查询刚才任务状态

输入：

```text
查看刚才 FWI 任务的状态。
```

预期面板显示：

- `job_id` 与刚才 smoke 一致；
- `status` 为 `queued|running|succeeded|failed` 之一；
- `stage`、`iteration / total_iterations` 和 `message` 来自真实 `status.json`；
- 未结束时可点击“刷新状态”，页面也会自动轮询。

### 5.4 获取结果和损失曲线

状态成功后输入：

```text
显示刚才的反演结果和损失曲线。
```

预期系统读取该 job 的 `manifest.json`、`metrics.json` 和 `config.resolved.json`，而不是重新启动计算。

## 6. 核对六张图片

FWI Result 面板必须出现以下六张卡片：

1. 真实速度模型；
2. 初始速度模型；
3. 反演速度模型；
4. 模型误差；
5. 观测 / 模拟 / 残差炮集；
6. 损失曲线。

图片加载失败时，卡片应明确显示“artifact 不存在或无法解码”，不能只是空白。

从页面复制 `job_id`，仅把下面占位符替换为该 ID：

```bash
JOB_ID='fwi-YYYYMMDDTHHMMSSZ-xxxxxxxxxxxx'
RUN_DIR="/root/fwi-runs/$JOB_ID"

test -s "$RUN_DIR/figures/true_model.png"
test -s "$RUN_DIR/figures/initial_model.png"
test -s "$RUN_DIR/figures/inverted_model.png"
test -s "$RUN_DIR/figures/model_error.png"
test -s "$RUN_DIR/figures/shot_gathers.png"
test -s "$RUN_DIR/figures/loss_curve.png"
printf '六张 PNG 均存在且非空\n'
```

还可以通过与浏览器相同的受控路由检查 Content-Type：

```bash
curl --fail --head \
  "http://127.0.0.1:8080/fwi-artifacts/$JOB_ID/figures/loss_curve.png"
```

预期包含 `Content-Type: image/png`。不要尝试用该路由读取模型三件套或 `.env`；它们不在允许的 artifact 范围内。

## 7. 核对页面指标与文件

先查看结构化文件。它们不包含 LLM API Key：

```bash
/root/.venvs/cpp-fwi-agent/bin/python -m json.tool "$RUN_DIR/status.json"
/root/.venvs/cpp-fwi-agent/bin/python -m json.tool "$RUN_DIR/metrics.json"
/root/.venvs/cpp-fwi-agent/bin/python -m json.tool "$RUN_DIR/manifest.json"
```

页面和文件至少应一致显示：

| 项目 | 当前 Marmousi 预期 |
|---|---|
| shape | `94 × 288` |
| dx / dz | `10 m / 10 m` |
| source frequency | `8 Hz` |
| dt / nt | `0.001 s / 2000` |
| shots / receivers | `3 / 96` |
| iterations | forward 为 0；smoke 为 2；demo 为 5 |
| initial/final loss | 与 `metrics.json` 数值一致 |
| loss reduction | 与 `(initial-final)/initial` 一致 |
| model relative L2 | initial → final，与文件一致 |
| device | CPU 名称或实际 CUDA 设备名称 |
| runtime | 与 `elapsed_seconds` 一致 |

还要确认：

```bash
/root/.venvs/cpp-fwi-agent/bin/python - "$RUN_DIR/metrics.json" <<'PY'
import json, math, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    m = json.load(stream)
assert m["nan_count"] == 0, m
assert m["inf_count"] == 0, m
assert math.isfinite(m["initial_loss"]), m
assert math.isfinite(m["final_loss"]), m
print("metrics finite: PASS")
PY
```

对于 `fwi_demo`，只有 `final_loss < initial_loss` 才能标为成功。两次更新的 `fwi_smoke` 只要求数值链路和模型更新有效，不应据此声称反演质量改善。

## 8. 理论问题不得启动任务

在终端记录当前 job 目录数量：

```bash
BEFORE="$(find /root/fwi-runs -mindepth 1 -maxdepth 1 -type d -name 'fwi-*' | wc -l)"
printf 'before=%s\n' "$BEFORE"
```

回到 Web 输入：

```text
什么是 FWI？只解释概念，不要运行任务。
```

回答完成后在终端检查：

```bash
AFTER="$(find /root/fwi-runs -mindepth 1 -maxdepth 1 -type d -name 'fwi-*' | wc -l)"
printf 'after=%s\n' "$AFTER"
test "$BEFORE" = "$AFTER" && printf '理论问题未创建任务：PASS\n'
```

理论回答不应出现新的 `fwi_job_submitted` 或 job_id。

## 9. 常见失败排查

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

## 10. 一键关闭

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

运行结果保留在 `/root/fwi-runs`，不会因停止服务而删除。清理结果前先确认 job 不再需要；不要删除或覆盖 `/root/fwi-data/models` 中的原始模型。
