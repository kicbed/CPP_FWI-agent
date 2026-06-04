#!/bin/bash
#
# FWI Agent 平台一键设置脚本
#
# 用法: ./setup.sh
#

set -e

# 颜色
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}"
echo "=========================================="
echo "  FWI Agent 平台 - 一键设置"
echo "=========================================="
echo -e "${NC}"

# 检查 .env 文件
if [ ! -f .env ]; then
    echo -e "${YELLOW}[1/5] 创建配置文件...${NC}"
    cp .env.example .env
    echo -e "${GREEN}  已创建 .env 文件${NC}"
    echo ""
    echo -e "${RED}  ⚠️  请编辑 .env 文件，填入你的 API Key:${NC}"
    echo "     nano .env"
    echo ""
    echo "  填好后重新运行 ./setup.sh"
    exit 0
fi

echo -e "${GREEN}[1/5] 配置文件已存在 ✓${NC}"

# 加载配置
source .env

# 检查 API Key
echo -e "${YELLOW}[2/5] 检查 API Key...${NC}"
if [ "$LLM_PROVIDER" == "deepseek" ] && [ -z "$DEEPSEEK_API_KEY" ]; then
    echo -e "${RED}  错误: 请在 .env 中设置 DEEPSEEK_API_KEY${NC}"
    exit 1
elif [ "$LLM_PROVIDER" == "qwen" ] && [ -z "$QWEN_API_KEY" ]; then
    echo -e "${RED}  错误: 请在 .env 中设置 QWEN_API_KEY${NC}"
    exit 1
fi
echo -e "${GREEN}  API Key 已配置 ✓${NC}"

# 检查 Redis
echo -e "${YELLOW}[3/5] 检查 Redis...${NC}"
if ! redis-cli ping > /dev/null 2>&1; then
    echo "  启动 Redis..."
    redis-server --daemonize yes
    sleep 1
fi
echo -e "${GREEN}  Redis 运行中 ✓${NC}"

# 检查本地 Embedding 服务
echo -e "${YELLOW}[4/5] 检查 Embedding 服务...${NC}"
if [ "$EMBEDDING_PROVIDER" == "local" ]; then
    if curl -s http://localhost:6000/health > /dev/null 2>&1; then
        echo -e "${GREEN}  本地 Embedding 服务运行中 ✓${NC}"
    else
        echo -e "${YELLOW}  本地 Embedding 服务未启动${NC}"
        echo "  启动命令:"
        echo "    python3 deploy/scripts/embedding_server.py &"
        echo ""
        read -p "  是否现在启动? (y/n) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            nohup python3 deploy/scripts/embedding_server.py > deploy/logs/embedding.log 2>&1 &
            sleep 3
            echo -e "${GREEN}  Embedding 服务已启动 ✓${NC}"
        fi
    fi
else
    echo -e "${GREEN}  使用 DashScope API ✓${NC}"
fi

# 检查编译
echo -e "${YELLOW}[5/5] 检查编译...${NC}"
if [ ! -f build/examples/ai_orchestrator/ai_orchestrator ]; then
    echo "  编译项目..."
    mkdir -p build && cd build
    cmake .. > /dev/null 2>&1
    make -j$(nproc) > /dev/null 2>&1
    cd ..
    echo -e "${GREEN}  编译完成 ✓${NC}"
else
    echo -e "${GREEN}  已编译 ✓${NC}"
fi

# 完成
echo ""
echo -e "${GREEN}=========================================="
echo "  设置完成！"
echo -e "==========================================${NC}"
echo ""
echo "当前配置:"
echo "  LLM: $LLM_PROVIDER"
echo "  Embedding: $EMBEDDING_PROVIDER"
echo "  路由模式: $ROUTING_MODE"
echo ""
echo "启动系统:"
echo "  source .env && ./examples/ai_orchestrator/start_system.sh"
echo ""
echo "停止系统:"
echo "  ./examples/ai_orchestrator/stop_system.sh"
