# 快速开始

本页只保留最短的当前使用路径。完整的依赖安装、仓库外 Python 虚拟环境、
CUDA、Docker、模型准备和故障排查见
[部署与使用说明](docs/DEPLOYMENT.md)。

需要更换/注册模型或确认反演参数时看 [模型与数值配置指南](docs/MODEL_GUIDE.md)；
需要逐步点击前端验收时看 [Web 前端端到端测试](docs/FRONTEND_TEST.md)。

## 1. 准备本地配置

```bash
cp .env.example .env
chmod 600 .env
```

编辑 `.env`，只填写你实际使用的 LLM provider 对应密钥。不要把 `.env`、
密钥、模型或运行结果提交到 Git。

当前固定演示模型必须位于：

```text
/root/fwi-data/models/marmousi_94_288.mat
/root/fwi-data/models/marmousi_94_288.npy
/root/fwi-data/models/marmousi_94_288.json
```

模型不随仓库分发，也不会被启动脚本复制或修改。

## 2. 一键启动

```bash
./start.sh
```

默认每次运行都会让 CMake 执行增量依赖检查；首次运行会从源码编译主项目和 MCP
Server。启动成功后访问：

<http://127.0.0.1:8080>

强制重新编译：

```bash
./start.sh --rebuild
```

只在已经完成编译时跳过构建：

```bash
./start.sh --no-build
```

## 3. 验证 FWI

在 Web UI 中输入：

```text
使用 marmousi_94_288 运行两次迭代的二维声学 FWI smoke test。
查看刚才 FWI 任务的状态。
显示刚才的反演结果和损失曲线。
```

运行结果写入 `/root/fwi-runs/<job_id>/`，不会写入 Git 仓库。

## 4. 一键关闭

```bash
./stop.sh
```

`stop.sh` 是幂等的，只处理本项目记录的 PID。

## 启动入口约定

- `./start.sh`、`./stop.sh`：唯一推荐的日常入口。
- `examples/ai_orchestrator/start_system.sh`：根脚本调用的内部组件启动器。
- `deploy/scripts/start*.sh`：兼容包装或独立 gRPC 调试入口，不再作为主流程。

仓库只保存源码和配置样例。`build/`、MCP 构建目录、虚拟环境、模型、日志、
PID 和 FWI 运行产物均由 `.gitignore` 排除，拉取代码后应在自己的环境中编译。
