#!/bin/bash
#
# 本地 Embedding 服务设置脚本
#
# 功能:
# 1. 安装 Python 依赖
# 2. 下载模型
# 3. 启动 Embedding 服务
#

set -e

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}=========================================="
echo "本地 Embedding 服务设置"
echo -e "==========================================${NC}"

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}错误: 未找到 python3${NC}"
    exit 1
fi

# 安装依赖
echo -e "${YELLOW}[1/3] 安装 Python 依赖...${NC}"
pip3 install sentence-transformers flask --quiet
echo -e "${GREEN}依赖安装完成${NC}"

# 模型名称
MODEL_NAME="${1:-Qwen/Qwen3-Embedding-0.6B}"
PORT="${2:-6000}"

echo -e "${YELLOW}[2/3] 下载模型: $MODEL_NAME${NC}"
echo "首次运行会自动下载模型，请耐心等待..."

# 启动服务
echo -e "${YELLOW}[3/3] 启动 Embedding 服务 (端口: $PORT)...${NC}"
echo ""
echo "启动命令:"
echo "  python3 deploy/scripts/embedding_server.py --model $MODEL_NAME --port $PORT"
echo ""
echo "测试命令:"
echo "  curl http://localhost:$PORT/health"
echo ""
echo "后台启动:"
echo "  nohup python3 deploy/scripts/embedding_server.py --model $MODEL_NAME --port $PORT > deploy/logs/embedding.log 2>&1 &"
echo ""

# 询问是否立即启动
read -p "是否立即启动服务? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${GREEN}启动 Embedding 服务...${NC}"
    python3 deploy/scripts/embedding_server.py --model "$MODEL_NAME" --port "$PORT"
fi
