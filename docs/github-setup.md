# GitHub 仓库绑定教程

## 一、为什么需要绑定 GitHub

- **版本控制**: 记录每次修改，可以回滚
- **多人协作**: 团队成员可以同步代码
- **备份**: 代码保存在云端，不怕丢失
- **免密推送**: 不用每次输入密码

## 二、设置步骤

### 步骤 1: 生成 SSH Key

```bash
# 生成 SSH Key（如果还没有）
ssh-keygen -t ed25519 -C "your-email@example.com"

# 一路回车使用默认设置
# 生成的文件在 ~/.ssh/id_ed25519（私钥）和 ~/.ssh/id_ed25519.pub（公钥）
```

### 步骤 2: 复制公钥

```bash
# 查看并复制公钥
cat ~/.ssh/id_ed25519.pub
```

输出类似：
```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA... your-email@example.com
```

**复制整行内容**。

### 步骤 3: 添加公钥到 GitHub

1. 打开 GitHub: https://github.com/settings/keys
2. 点击 "New SSH key"
3. Title: 填 "WSL" 或任意名称
4. Key: 粘贴刚才复制的公钥
5. 点击 "Add SSH key"

### 步骤 4: 测试连接

```bash
ssh -T git@github.com
```

预期输出：
```
Hi username! You've successfully authenticated, but GitHub does not provide shell access.
```

### 步骤 5: 创建 GitHub 仓库

1. 打开 https://github.com/new
2. Repository name: `agent-communication`
3. 选择 Private（私有仓库）
4. 不要勾选 "Initialize this repository with a README"
5. 点击 "Create repository"

### 步骤 6: 初始化本地仓库并推送

```bash
# 进入项目目录
cd /root/projects/project/agent-communication-main-v2

# 初始化 Git
git init

# 添加所有文件
git add .

# 创建 .gitignore（排除不需要的文件）
cat > .gitignore << 'EOF'
# Build
build/
*.o
*.so
*.a

# IDE
.vscode/
.idea/
*.swp
*.swo

# Logs
*.log
deploy/logs/
examples/ai_orchestrator/logs/

# PIDs
deploy/pids/
examples/ai_orchestrator/pids/

# Cache
.claude/
resources/embeddings/

# Environment
.env
*.env.local
EOF

# 再次添加文件（.gitignore 会排除不需要的）
git add .

# 提交
git commit -m "Initial commit: FWI Agent Platform"

# 设置主分支
git branch -M main

# 添加远程仓库（替换为你的 GitHub 用户名）
git remote add origin git@github.com:YOUR_USERNAME/agent-communication.git

# 推送
git push -u origin main
```

## 三、日常使用

### 拉取最新代码

```bash
git pull
```

### 提交修改

```bash
# 查看修改
git status

# 添加修改的文件
git add <file>
# 或添加所有修改
git add .

# 提交
git commit -m "描述你的修改"

# 推送
git push
```

### 查看历史

```bash
# 查看提交历史
git log --oneline

# 查看某个文件的修改历史
git log --follow -p -- <file>
```

### 回滚修改

```bash
# 回滚某个文件
git checkout -- <file>

# 回滚到某个提交
git reset --hard <commit-id>
```

## 四、多人协作

### 克隆仓库（其他成员）

```bash
# 其他成员需要先将自己的 SSH 公钥添加到 GitHub
git clone git@github.com:YOUR_USERNAME/agent-communication.git
cd agent-communication
```

### 分支管理

```bash
# 创建新功能分支
git checkout -b feature/new-agent

# 开发完成后合并到主分支
git checkout main
git merge feature/new-agent

# 推送
git push
```

## 五、常见问题

### Q: 推送时要求输入密码

说明 SSH Key 没有配置好，重新检查步骤 2-4。

### Q: 权限被拒绝

```bash
# 检查 SSH Key 权限
chmod 600 ~/.ssh/id_ed25519
chmod 644 ~/.ssh/id_ed25519.pub
```

### Q: 大文件推送失败

```bash
# 使用 Git LFS 管理大文件
git lfs install
git lfs track "*.bin"
git lfs track "*.dat"
git add .gitattributes
```

## 六、参考

- [GitHub SSH 文档](https://docs.github.com/en/authentication/connecting-to-github-with-ssh)
- [Git 基础命令](https://git-scm.com/book/zh/v2/Git-%E5%9F%BA%E7%A1%80-%E8%8E%B7%E5%8F%96-Git-%E4%BB%93%E5%BA%93)
