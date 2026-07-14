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

`stop.sh` 可以重复执行；已经停止的服务不会导致脚本失败。

需要保留旧 Web gRPC 模式做本机调试时，也仍从同一个根入口启动：

```bash
ENABLE_GRPC=true ./start.sh
```

该选项会额外启动仅监听 `127.0.0.1:50051/50052` 的 gRPC Server 和 HTTP bridge，
不会再自动启动 Embedding 服务、占用者清理命令或前台客户端。

根目录的 `./start.sh` 与 `./stop.sh` 是唯一推荐的日常启停入口。`examples/ai_orchestrator/start_system.sh` 属于内部实现，`deploy/scripts/` 下的旧入口只保留给兼容或高级调试场景，不建议直接调用。

首次安装、C++ 本地编译、Python 虚拟环境、CUDA 和 Docker 隔离部署请先阅读 [部署与使用说明](docs/DEPLOYMENT.md)。仓库不包含编译产物、Python 虚拟环境、模型文件、运行结果或 `.env`。

- 更换/注册模型及当前全部反演参数：[模型与数值配置指南](docs/MODEL_GUIDE.md)
- 自己从浏览器逐步验收：[Web 前端端到端测试](docs/FRONTEND_TEST.md)

## 当前能做什么

- 使用 Deepwave `scalar` 求解二维常密度声学波动方程。
- 支持 CPU 和单张 NVIDIA GPU，使用 `float32`、Ricker 子波和单参数纵波速度 `Vp`。
- 从固定 Marmousi NPY 与 sidecar JSON 读取并验证 shape、轴顺序、网格、速度范围和哈希。
- 用真实速度模型生成合成观测数据，从慢度平滑初始模型执行 L2 波形残差 FWI。
- 提供 `forward`、2 次迭代 `fwi_smoke` 和 5 次迭代 `fwi_demo` preset。
- 生成模型、炮集、残差、损失曲线、状态、指标和 manifest 等结构化产物。
- 通过白名单 MCP 工具异步提交任务，并在 Web UI 中查看状态、指标和图片。

这不是生产级反演系统。观测数据与反演传播均由 Deepwave 生成，属于合成端到端/逆犯罪验证，主要用于验证系统调用、梯度、优化和结果展示流程，不能据此宣称对实际数据的普遍反演效果。

## 快速体验

启动后访问 <http://127.0.0.1:8080>，依次输入：

```text
使用 marmousi_94_288 运行一个二维声学正演演示。
使用 marmousi_94_288 运行两次迭代的 FWI smoke test。
查看刚才 FWI 任务的状态。
显示刚才的反演结果和损失曲线。
```

理论问题不会触发计算，例如：

```text
什么是 FWI？只解释概念，不要运行任务。
```

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
python -m fwi_worker status --run-dir /root/fwi-runs/<job_id>
```

## 架构

```text
浏览器 Web UI (:8080)
        │
        ▼
C++ Orchestrator (:5000) ── Agent / RAG / 理论问答
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

- 不要提交 `.env`，不要把 API Key 写入命令、README、配置样例或日志。
- MCP 只接受固定的 `model_id`、preset 和 `cpu|cuda`，不接受 shell、Python 路径、模型路径或额外参数。
- Worker 通过部署时受控的绝对 Python 路径、固定模块和参数数组启动；该路径不接受 MCP 用户输入，不使用 `std::system`，也不执行用户提供的任意命令。
- Artifact 路由会解析并约束真实路径，拒绝 `..`、绝对路径、符号链接逃逸、目录列表和非白名单后缀。
- 当前 Web 服务面向本机实验使用，不带身份认证；不要直接暴露到公网。
- 当前不连接 SSH、Slurm、PBS 或远程集群，也不支持任意本地作业后端。

## 范围与限制

当前只覆盖二维常密度声学、单参数 `Vp`、单频 8 Hz、CPU/单 GPU 和小规模合成验证。尚不支持弹性波、密度或 `Vs` 反演、3D、MPI、多 GPU、SEG-Y 直接读取、远程调度、复杂目标函数和任务取消。

完整的宿主机环境配置、仓库外 Python venv、Docker 隔离、模型放置、启动参数、健康检查、日志、故障排查及卸载步骤见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。模型注册和参数解释见 [docs/MODEL_GUIDE.md](docs/MODEL_GUIDE.md)，浏览器验收步骤见 [docs/FRONTEND_TEST.md](docs/FRONTEND_TEST.md)。

## 许可证与使用提示

请分别遵守本项目及其依赖（包括 PyTorch、Deepwave 和模型数据）的许可证与使用条件。模型数据不会随仓库分发。
