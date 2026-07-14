# Agent Communication RPC Framework

这是一个基于 C++、gRPC、A2A、MCP 和 RAG 的多 Agent 科研助手框架。仓库同时保留稳定框架基线和实验性 FWI 实现，请先根据用途选择分支。

## 分支状态

| 分支 | 定位 | Deepwave 二维声学 FWI |
|---|---|---|
| `main` | 多 Agent 通信框架基线 | 不包含当前实验 Worker 和一键部署改造 |
| `feature/fwi-deepwave-2d-acoustic` | 当天可运行的端到端 FWI MVP | 支持 Deepwave `scalar`、CPU/单张 NVIDIA GPU、正演、2/5 次迭代 FWI、MCP 和 Web 图片展示 |

因此，旧文档中“项目不执行真实 CUDA”的笼统说法已经不准确。更精确的边界是：

- 实验分支的固定白名单 FWI 路径会实际运行 Deepwave CPU 或单 GPU 计算；
- `main` 仍是框架基线，本身没有合入该实验实现；
- 通用 `JobBackend` 仍保持 dry-run，没有开放 MPI、多 GPU、SSH、Slurm/PBS、远程集群或任意 shell 命令。

## 体验 FWI 实验分支

新克隆仓库后执行：

```bash
git fetch origin
git switch --track origin/feature/fwi-deepwave-2d-acoustic

cp .env.example .env
chmod 600 .env
# 用本地编辑器填写所选 LLM provider 的 API Key；不要输出或提交 .env

./start.sh
# 浏览器打开 http://127.0.0.1:8080
```

一键关闭：

```bash
./stop.sh
```

实验分支上的当前说明：

- [完整 README](https://github.com/kicbed/CPP_FWI-agent/tree/feature/fwi-deepwave-2d-acoustic)
- [部署、依赖、虚拟环境和 Docker](https://github.com/kicbed/CPP_FWI-agent/blob/feature/fwi-deepwave-2d-acoustic/docs/DEPLOYMENT.md)
- [更换模型和实际反演参数](https://github.com/kicbed/CPP_FWI-agent/blob/feature/fwi-deepwave-2d-acoustic/docs/MODEL_GUIDE.md)
- [浏览器端到端测试教程](https://github.com/kicbed/CPP_FWI-agent/blob/feature/fwi-deepwave-2d-acoustic/docs/FRONTEND_TEST.md)

## `main` 基线包含什么

- gRPC / Protocol Buffers 通信和 A2A HTTP/JSON-RPC；
- Registry、Orchestrator 和多个专业 Agent；
- MCP 插件发现、工具调用与 RAG 工具选择；
- Session Memory、Agent Memory 和任务状态；
- CMake 构建与 GoogleTest / RapidCheck 测试。

只验证主分支框架时，可以从源码编译：

```bash
cmake -S . -B build
cmake --build build -j"$(nproc)"
ctest --test-dir build --output-on-failure
```

主分支的历史内部启动脚本存在重叠，且不代表实验分支的当前部署方式。需要运行 FWI 时，请切换实验分支并只使用仓库根目录的 `./start.sh` 和 `./stop.sh`。

## 安全与仓库体积

- `.env`、API Key、私钥、模型、运行结果、日志、PID、虚拟环境和本地构建目录都不应进入 Git；
- 模型和 FWI 结果位于仓库外，拉取代码后在自己的环境中编译；
- 当前 Web 服务没有用户认证，默认只应监听回环地址，不要直接暴露到公网；
- FWI MCP 入口只接受固定的 `model_id`、preset 和 `cpu|cuda`，不接受路径、Python 可执行文件或 shell 参数。

## 实验性质说明

FWI 分支使用真实模型正演生成合成观测，再由同一 Deepwave 传播算子执行反演，属于合成端到端/逆犯罪验证。它用于验证数值、梯度、任务编排和结果展示链路，不能据此宣称对实际地震数据具有普遍反演效果。

请分别遵守本项目、PyTorch、Deepwave 及所用模型数据的许可证和使用条件。模型数据不随仓库分发。
