# 生产环境部署指南

## 概述

本指南介绍如何在生产环境中部署 Agent Communication RPC Framework。

## 部署架构

### 单机部署

```
┌─────────────────────────────────────────────────────────────────┐
│                         单机服务器                               │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │
│  │ RPC Server  │  │ Orchestrator│  │ Math Agent  │             │
│  │   :50051    │  │   :5000     │  │   :5001     │             │
│  └─────────────┘  └─────────────┘  └─────────────┘             │
│         │                │                │                     │
│         └────────────────┴────────────────┘                     │
│                          │                                      │
│                    ┌─────────────┐                              │
│                    │  Registry   │                              │
│                    │   :8500     │                              │
│                    └─────────────┘                              │
│                          │                                      │
│                    ┌─────────────┐                              │
│                    │ MCP Server  │                              │
│                    │  (stdio)    │                              │
│                    └─────────────┘                              │
└─────────────────────────────────────────────────────────────────┘
```

### 分布式部署

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   负载均衡器     │     │   负载均衡器     │     │   负载均衡器     │
│   (Nginx/HAProxy)│     │   (Nginx/HAProxy)│     │   (Nginx/HAProxy)│
└────────┬────────┘     └────────┬────────┘     └────────┬────────┘
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  RPC Server 1   │     │  RPC Server 2   │     │  RPC Server N   │
│    :50051       │     │    :50051       │     │    :50051       │
└────────┬────────┘     └────────┬────────┘     └────────┬────────┘
         │                       │                       │
         └───────────────────────┴───────────────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │      Orchestrator       │
                    │   (多实例 + 负载均衡)    │
                    └────────────┬────────────┘
                                 │
         ┌───────────────────────┼───────────────────────┐
         ▼                       ▼                       ▼
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Math Agent    │     │   Code Agent    │     │  Other Agents   │
│   (多实例)       │     │   (多实例)       │     │   (多实例)       │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                                 │
                    ┌────────────┴────────────┐
                    ▼                         ▼
           ┌─────────────────┐       ┌─────────────────┐
           │     Redis       │       │    Registry     │
           │   (集群模式)     │       │   (Consul/etcd) │
           └─────────────────┘       └─────────────────┘
```

## 环境准备

### 系统要求

| 组件 | 最低配置 | 推荐配置 |
|------|----------|----------|
| CPU | 2 核 | 4+ 核 |
| 内存 | 4 GB | 8+ GB |
| 磁盘 | 20 GB | 50+ GB SSD |
| 网络 | 100 Mbps | 1 Gbps |

### 依赖安装

```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install -y \
    cmake build-essential pkg-config \
    libgrpc++-dev libprotobuf-dev protobuf-compiler protobuf-compiler-grpc \
    libcurl4-openssl-dev libjsoncpp-dev uuid-dev \
    redis-server nginx

# 安装 Consul (可选)
wget https://releases.hashicorp.com/consul/1.15.4/consul_1.15.4_linux_amd64.zip
unzip consul_1.15.4_linux_amd64.zip
sudo mv consul /usr/local/bin/
```

## 编译

### 生产环境编译

```bash
# 克隆代码
git clone <repository-url>
cd agent-communication

# 创建构建目录
mkdir build && cd build

# 配置 (Release 模式)
cmake -DCMAKE_BUILD_TYPE=Release ..

# 编译
make -j$(nproc)

# 运行测试
ctest --output-on-failure
```

### 编译 MCP Server

```bash
cd mcp_server_integrated
mkdir build && cd build
cmake -DCMAKE_BUILD_TYPE=Release ..
make -j$(nproc)
```

## 配置

### 环境变量

创建 `/etc/agent-rpc/env`:

```bash
# API Keys
QWEN_API_KEY=sk-your-qwen-api-key
DASHSCOPE_API_KEY=sk-your-dashscope-api-key

# 服务配置
RPC_SERVER_PORT=50051
ORCHESTRATOR_URL=http://localhost:5000
REGISTRY_URL=http://localhost:8500

# Redis 配置
REDIS_HOST=localhost
REDIS_PORT=6379

# 日志配置
LOG_LEVEL=INFO
LOG_DIR=/var/log/agent-rpc
```

### RPC Server 配置

创建 `/etc/agent-rpc/rpc-server.conf`:

```ini
[server]
address = 0.0.0.0
port = 50051
max_threads = 100
max_message_size = 4194304

[a2a]
orchestrator_url = http://localhost:5000
timeout_seconds = 60
enable_a2a = true

[ssl]
enabled = false
cert_path = /etc/agent-rpc/ssl/server.crt
key_path = /etc/agent-rpc/ssl/server.key

[logging]
level = INFO
file = /var/log/agent-rpc/rpc-server.log
max_size_mb = 100
max_files = 10
```

### Orchestrator 配置

创建 `/etc/agent-rpc/orchestrator.conf`:

```ini
[agent]
id = orchestrator-001
name = Orchestrator
port = 5000

[registry]
url = http://localhost:8500
heartbeat_interval = 30

[routing]
strategy = SKILL_MATCH
timeout_seconds = 30

[mcp]
enabled = true
server_path = /opt/agent-rpc/mcp_server
plugins_dir = /opt/agent-rpc/plugins

[rag]
enabled = true
top_k = 5
similarity_threshold = 0.3
cache_enabled = true
cache_max_size = 1000
```

## Systemd 服务

### RPC Server 服务

创建 `/etc/systemd/system/agent-rpc-server.service`:

```ini
[Unit]
Description=Agent RPC Server
After=network.target redis.service

[Service]
Type=simple
User=agent-rpc
Group=agent-rpc
EnvironmentFile=/etc/agent-rpc/env
ExecStart=/opt/agent-rpc/bin/rpc_server \
    --config /etc/agent-rpc/rpc-server.conf
Restart=always
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
```

### Orchestrator 服务

创建 `/etc/systemd/system/agent-orchestrator.service`:

```ini
[Unit]
Description=Agent Orchestrator
After=network.target redis.service agent-registry.service

[Service]
Type=simple
User=agent-rpc
Group=agent-rpc
EnvironmentFile=/etc/agent-rpc/env
ExecStart=/opt/agent-rpc/bin/ai_orchestrator \
    orchestrator-001 5000 http://localhost:8500 ${QWEN_API_KEY} \
    --enable-mcp \
    --mcp-server /opt/agent-rpc/mcp_server
Restart=always
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
```

### Math Agent 服务

创建 `/etc/systemd/system/agent-math.service`:

```ini
[Unit]
Description=Math Agent
After=network.target agent-registry.service

[Service]
Type=simple
User=agent-rpc
Group=agent-rpc
EnvironmentFile=/etc/agent-rpc/env
ExecStart=/opt/agent-rpc/bin/ai_math_agent \
    math-agent-001 5001 http://localhost:8500 ${QWEN_API_KEY} \
    --enable-mcp \
    --mcp-server /opt/agent-rpc/mcp_server
Restart=always
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
```

### Registry 服务

创建 `/etc/systemd/system/agent-registry.service`:

```ini
[Unit]
Description=Agent Registry Server
After=network.target

[Service]
Type=simple
User=agent-rpc
Group=agent-rpc
ExecStart=/opt/agent-rpc/bin/ai_registry_server 8500
Restart=always
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
```

### 启动服务

```bash
# 重新加载 systemd
sudo systemctl daemon-reload

# 启动服务
sudo systemctl start agent-registry
sudo systemctl start agent-math
sudo systemctl start agent-orchestrator
sudo systemctl start agent-rpc-server

# 设置开机启动
sudo systemctl enable agent-registry
sudo systemctl enable agent-math
sudo systemctl enable agent-orchestrator
sudo systemctl enable agent-rpc-server

# 查看状态
sudo systemctl status agent-rpc-server
```

## Nginx 反向代理

### gRPC 代理配置

创建 `/etc/nginx/conf.d/agent-rpc.conf`:

```nginx
upstream grpc_servers {
    server 127.0.0.1:50051;
    # 添加更多服务器实现负载均衡
    # server 127.0.0.1:50052;
    # server 127.0.0.1:50053;
}

server {
    listen 443 ssl http2;
    server_name api.example.com;

    ssl_certificate /etc/nginx/ssl/server.crt;
    ssl_certificate_key /etc/nginx/ssl/server.key;
    ssl_protocols TLSv1.2 TLSv1.3;

    location / {
        grpc_pass grpc://grpc_servers;
        grpc_set_header Host $host;
        grpc_set_header X-Real-IP $remote_addr;
        
        # 超时设置
        grpc_read_timeout 300s;
        grpc_send_timeout 300s;
    }
}
```

### HTTP 代理配置 (Orchestrator)

```nginx
upstream orchestrator_servers {
    server 127.0.0.1:5000;
}

server {
    listen 80;
    server_name orchestrator.example.com;

    location / {
        proxy_pass http://orchestrator_servers;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        
        # SSE 支持
        proxy_set_header Connection '';
        proxy_buffering off;
        proxy_cache off;
        chunked_transfer_encoding off;
    }
}
```

## Redis 配置

### 单机模式

编辑 `/etc/redis/redis.conf`:

```conf
bind 127.0.0.1
port 6379
maxmemory 1gb
maxmemory-policy allkeys-lru
appendonly yes
```

### 集群模式

```bash
# 创建集群
redis-cli --cluster create \
    192.168.1.1:6379 \
    192.168.1.2:6379 \
    192.168.1.3:6379 \
    --cluster-replicas 0
```

## 监控

### Prometheus 配置

创建 `/etc/prometheus/prometheus.yml`:

```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'agent-rpc'
    static_configs:
      - targets:
        - 'localhost:9090'  # RPC Server metrics
        - 'localhost:9091'  # Orchestrator metrics
        - 'localhost:9092'  # Math Agent metrics
```

### Grafana 仪表板

导入以下指标：

- `agent_rpc_requests_total` - 请求总数
- `agent_rpc_request_duration_ms` - 请求延迟
- `agent_rpc_errors_total` - 错误总数
- `a2a_tasks_active` - 活跃任务数
- `rag_cache_hit_rate` - 缓存命中率

## 日志管理

### Logrotate 配置

创建 `/etc/logrotate.d/agent-rpc`:

```
/var/log/agent-rpc/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 0640 agent-rpc agent-rpc
    postrotate
        systemctl reload agent-rpc-server
    endscript
}
```

### 日志聚合 (ELK)

```yaml
# Filebeat 配置
filebeat.inputs:
  - type: log
    enabled: true
    paths:
      - /var/log/agent-rpc/*.log
    json.keys_under_root: true
    json.add_error_key: true

output.elasticsearch:
  hosts: ["localhost:9200"]
  index: "agent-rpc-%{+yyyy.MM.dd}"
```

## 安全配置

### SSL/TLS

```bash
# 生成自签名证书 (测试用)
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout /etc/agent-rpc/ssl/server.key \
    -out /etc/agent-rpc/ssl/server.crt

# 设置权限
chmod 600 /etc/agent-rpc/ssl/server.key
chown agent-rpc:agent-rpc /etc/agent-rpc/ssl/*
```

### 防火墙

```bash
# UFW
sudo ufw allow 50051/tcp  # gRPC
sudo ufw allow 5000/tcp   # Orchestrator
sudo ufw allow 8500/tcp   # Registry

# iptables
iptables -A INPUT -p tcp --dport 50051 -j ACCEPT
iptables -A INPUT -p tcp --dport 5000 -j ACCEPT
iptables -A INPUT -p tcp --dport 8500 -j ACCEPT
```

### API Key 管理

```bash
# 使用 HashiCorp Vault
vault kv put secret/agent-rpc \
    qwen_api_key=sk-xxx \
    dashscope_api_key=sk-xxx

# 在服务启动脚本中获取
export QWEN_API_KEY=$(vault kv get -field=qwen_api_key secret/agent-rpc)
```

## 健康检查

### HTTP 健康检查端点

```bash
# 检查 RPC Server
curl http://localhost:50051/health

# 检查 Orchestrator
curl http://localhost:5000/health

# 检查 Registry
curl http://localhost:8500/health
```

### 自动化健康检查脚本

```bash
#!/bin/bash
# /opt/agent-rpc/scripts/health-check.sh

check_service() {
    local name=$1
    local url=$2
    
    if curl -sf "$url" > /dev/null; then
        echo "$name: OK"
        return 0
    else
        echo "$name: FAILED"
        return 1
    fi
}

check_service "Registry" "http://localhost:8500/health"
check_service "Orchestrator" "http://localhost:5000/health"
check_service "RPC Server" "http://localhost:50051/health"
```

## 故障排除

### 常见问题

| 问题 | 可能原因 | 解决方案 |
|------|----------|----------|
| 服务启动失败 | 端口被占用 | `netstat -tlnp \| grep <port>` |
| 连接超时 | 防火墙阻止 | 检查防火墙规则 |
| API 调用失败 | API Key 无效 | 检查环境变量 |
| 内存不足 | 缓存过大 | 调整缓存配置 |

### 日志分析

```bash
# 查看错误日志
grep -i error /var/log/agent-rpc/*.log

# 查看最近的日志
tail -f /var/log/agent-rpc/rpc-server.log

# 统计错误类型
grep -i error /var/log/agent-rpc/*.log | cut -d: -f4 | sort | uniq -c
```

### 性能调优

```bash
# 增加文件描述符限制
echo "agent-rpc soft nofile 65535" >> /etc/security/limits.conf
echo "agent-rpc hard nofile 65535" >> /etc/security/limits.conf

# 调整内核参数
sysctl -w net.core.somaxconn=65535
sysctl -w net.ipv4.tcp_max_syn_backlog=65535
```

## 备份与恢复

### 备份

```bash
#!/bin/bash
# /opt/agent-rpc/scripts/backup.sh

BACKUP_DIR=/backup/agent-rpc/$(date +%Y%m%d)
mkdir -p $BACKUP_DIR

# 备份配置
cp -r /etc/agent-rpc $BACKUP_DIR/config

# 备份 Redis 数据
redis-cli BGSAVE
cp /var/lib/redis/dump.rdb $BACKUP_DIR/

# 备份索引文件
cp /var/lib/agent-rpc/index.json $BACKUP_DIR/
```

### 恢复

```bash
#!/bin/bash
# /opt/agent-rpc/scripts/restore.sh

BACKUP_DIR=$1

# 停止服务
systemctl stop agent-rpc-server agent-orchestrator agent-math agent-registry

# 恢复配置
cp -r $BACKUP_DIR/config/* /etc/agent-rpc/

# 恢复 Redis 数据
cp $BACKUP_DIR/dump.rdb /var/lib/redis/

# 恢复索引
cp $BACKUP_DIR/index.json /var/lib/agent-rpc/

# 启动服务
systemctl start agent-registry agent-math agent-orchestrator agent-rpc-server
```

## 升级流程

### 滚动升级

```bash
# 1. 备份当前版本
./backup.sh

# 2. 下载新版本
wget https://releases.example.com/agent-rpc-v1.2.0.tar.gz
tar xzf agent-rpc-v1.2.0.tar.gz

# 3. 逐个升级服务
for service in agent-math agent-orchestrator agent-rpc-server; do
    systemctl stop $service
    cp -r agent-rpc-v1.2.0/bin/* /opt/agent-rpc/bin/
    systemctl start $service
    sleep 10
    # 验证服务健康
    ./health-check.sh
done
```

### 回滚

```bash
# 恢复到之前的版本
./restore.sh /backup/agent-rpc/20241213
```
