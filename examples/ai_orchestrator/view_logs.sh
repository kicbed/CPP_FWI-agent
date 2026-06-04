#!/bin/bash
#
# FWI Agent 日志查看脚本
#
# 功能：
# 1. 查看所有日志
# 2. 查看特定 Agent 日志
# 3. 查看对话记录
# 4. 查看 Agent 调用链路
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

show_help() {
    echo ""
    echo -e "${BLUE}=========================================="
    echo "FWI Agent 日志查看工具"
    echo -e "==========================================${NC}"
    echo ""
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  all         - 查看所有日志（实时）"
    echo "  orch        - 查看 Orchestrator 日志"
    echo "  math        - 查看 Math Agent 日志"
    echo "  fwi         - 查看 FWI Theory Agent 日志"
    echo "  teaching    - 查看 FWI Teaching Agent 日志"
    echo "  research    - 查看 General Research Agent 日志"
    echo "  registry    - 查看 Registry 日志"
    echo "  embedding   - 查看 Embedding 服务日志"
    echo "  chat        - 查看对话记录"
    echo "  trace       - 查看 Agent 调用链路"
    echo "  errors      - 查看错误日志"
    echo "  status      - 查看服务状态"
    echo "  help        - 显示此帮助"
    echo ""
    echo "示例:"
    echo "  $0 all        # 实时查看所有日志"
    echo "  $0 chat       # 查看对话记录"
    echo "  $0 trace      # 查看 Agent 调用链路"
    echo ""
}

# 查看对话记录
view_chat() {
    echo -e "${BLUE}=========================================="
    echo "对话记录"
    echo -e "==========================================${NC}"
    echo ""

    if [ ! -f "$LOG_DIR/orchestrator.log" ]; then
        echo -e "${RED}未找到日志文件${NC}"
        return
    fi

    # 提取对话记录
    grep -E "\[REQ\]|\[RESP\]|收到消息|识别意图|调用.*Agent" "$LOG_DIR/orchestrator.log" 2>/dev/null | \
    while IFS= read -r line; do
        # 提取时间戳
        timestamp=$(echo "$line" | grep -oP '\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+\]' | head -1)
        # 提取 request_id
        req_id=$(echo "$line" | grep -oP '\[req:[^\]]+\]' | head -1)
        # 提取内容
        if echo "$line" | grep -q "\[REQ\]"; then
            content=$(echo "$line" | grep -oP '\[REQ\] .*' | sed 's/\[REQ\] //')
            echo -e "${GREEN}$timestamp${NC} ${CYAN}$req_id${NC}"
            echo -e "  👤 用户: $content"
        elif echo "$line" | grep -q "\[RESP\]"; then
            content=$(echo "$line" | grep -oP '\[RESP\] .*' | sed 's/\[RESP\] //')
            echo -e "  ✅ $content"
            echo ""
        elif echo "$line" | grep -q "识别意图"; then
            content=$(echo "$line" | grep -oP '识别意图: .*' | sed 's/识别意图: //')
            echo -e "  🎯 意图: $content"
        elif echo "$line" | grep -q "调用.*Agent"; then
            content=$(echo "$line" | grep -oP '调用.*Agent.*' | head -1)
            echo -e "  🔄 $content"
        fi
    done
}

# 查看 Agent 调用链路
view_trace() {
    echo -e "${BLUE}=========================================="
    echo "Agent 调用链路"
    echo -e "==========================================${NC}"
    echo ""

    if [ ! -f "$LOG_DIR/orchestrator.log" ]; then
        echo -e "${RED}未找到日志文件${NC}"
        return
    fi

    # 提取调用链路
    grep -E "ROUTE|CALL|RETRIEVE|CANDIDATE|FALLBACK" "$LOG_DIR/orchestrator.log" 2>/dev/null | \
    while IFS= read -r line; do
        timestamp=$(echo "$line" | grep -oP '\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+\]' | head -1)
        req_id=$(echo "$line" | grep -oP '\[req:[^\]]+\]' | head -1)

        if echo "$line" | grep -q "\[RETRIEVE\]"; then
            content=$(echo "$line" | grep -oP '\[RETRIEVE\] .*' | sed 's/\[RETRIEVE\] //')
            echo -e "${GREEN}$timestamp${NC} ${CYAN}$req_id${NC}"
            echo -e "  🔍 检索: $content"
        elif echo "$line" | grep -q "\[CANDIDATE\]"; then
            content=$(echo "$line" | grep -oP '\[CANDIDATE\] .*' | sed 's/\[CANDIDATE\] //')
            echo -e "  📋 候选: $content"
        elif echo "$line" | grep -q "\[ROUTE\]"; then
            content=$(echo "$line" | grep -oP '\[ROUTE\] .*' | sed 's/\[ROUTE\] //')
            echo -e "  🎯 路由: $content"
        elif echo "$line" | grep -q "\[CALL\]"; then
            content=$(echo "$line" | grep -oP '\[CALL\] .*' | sed 's/\[CALL\] //')
            echo -e "  📞 调用: $content"
        elif echo "$line" | grep -q "\[FALLBACK\]"; then
            content=$(echo "$line" | grep -oP '\[FALLBACK\] .*' | sed 's/\[FALLBACK\] //')
            echo -e "  ⚠️ 回退: $content"
        fi
    done
}

# 查看错误日志
view_errors() {
    echo -e "${RED}=========================================="
    echo "错误日志"
    echo -e "==========================================${NC}"
    echo ""

    for log_file in "$LOG_DIR"/*.log; do
        if [ -f "$log_file" ]; then
            name=$(basename "$log_file" .log)
            errors=$(grep -i "error\|fail\|exception\|abort" "$log_file" 2>/dev/null)
            if [ -n "$errors" ]; then
                echo -e "${YELLOW}[$name]${NC}"
                echo "$errors" | tail -5
                echo ""
            fi
        fi
    done

    echo -e "${GREEN}检查完成${NC}"
}

# 查看服务状态
view_status() {
    echo -e "${BLUE}=========================================="
    echo "服务状态"
    echo -e "==========================================${NC}"
    echo ""

    # 检查端口
    echo "端口占用:"
    for port in 8500 5000 5001 5002 5003 5004 6000; do
        pid=$(ss -tlnp | grep ":$port " | grep -oP 'pid=\d+' | grep -oP '\d+')
        if [ -n "$pid" ]; then
            name=$(ps -p $pid -o comm= 2>/dev/null)
            echo -e "  端口 $port: ${GREEN}运行中${NC} (PID: $pid, 进程: $name)"
        else
            echo -e "  端口 $port: ${RED}未使用${NC}"
        fi
    done

    echo ""
    echo "Redis 状态:"
    if redis-cli ping > /dev/null 2>&1; then
        echo -e "  ${GREEN}运行中${NC}"
        echo "  会话数: $(redis-cli keys 'a2a:session:*' 2>/dev/null | wc -l)"
    else
        echo -e "  ${RED}未运行${NC}"
    fi

    echo ""
    echo "Embedding 服务:"
    if curl -s http://localhost:6000/health > /dev/null 2>&1; then
        echo -e "  ${GREEN}运行中${NC} (端口 6000)"
    else
        echo -e "  ${RED}未运行${NC}"
    fi
}

# 主逻辑
case "${1:-help}" in
    all)
        echo -e "${BLUE}实时查看所有日志 (Ctrl+C 退出)${NC}"
        tail -f "$LOG_DIR"/*.log 2>/dev/null
        ;;
    orch)
        tail -f "$LOG_DIR/orchestrator.log" 2>/dev/null
        ;;
    math)
        tail -f "$LOG_DIR/math_agent.log" 2>/dev/null
        ;;
    fwi)
        tail -f "$LOG_DIR/fwi_theory_agent.log" 2>/dev/null
        ;;
    teaching)
        tail -f "$LOG_DIR/fwi_teaching_agent.log" 2>/dev/null
        ;;
    research)
        tail -f "$LOG_DIR/general_research_agent.log" 2>/dev/null
        ;;
    registry)
        tail -f "$LOG_DIR/registry.log" 2>/dev/null
        ;;
    embedding)
        tail -f "$PROJECT_ROOT/deploy/logs/embedding.log" 2>/dev/null
        ;;
    chat)
        view_chat
        ;;
    trace)
        view_trace
        ;;
    errors)
        view_errors
        ;;
    status)
        view_status
        ;;
    help|*)
        show_help
        ;;
esac
