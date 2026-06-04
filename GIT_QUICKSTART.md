# Git 快速使用指南

## 仓库地址

```
git@github.com:kicbed/CPP_FWI-agent.git
```

## 日常使用（最常用的 5 个命令）

### 1. 查看你改了什么

```bash
git status
```

输出示例：
```
modified:   examples/ai_orchestrator/orchestrator_main.cpp
new file:   examples/ai_orchestrator/new_agent.cpp
```

### 2. 保存修改（提交）

```bash
# 添加所有修改
git add .

# 提交（写清楚你改了什么）
git commit -m "添加了 XXX 功能"

# 推送到 GitHub
git push
```

**三步走：add → commit → push**

### 3. 获取最新代码

```bash
git pull
```

### 4. 查看历史记录

```bash
# 简洁版
git log --oneline

# 详细版
git log
```

### 5. 撤销修改

```bash
# 撤销某个文件的修改（还没 add 的）
git checkout -- 文件名

# 撤销 add（取消暂存）
git reset HEAD 文件名
```

## 常见场景

### 场景 1: 改了几个文件，想保存

```bash
git status                    # 看看改了啥
git add .                     # 全部添加
git commit -m "修复了 XXX"    # 提交
git push                      # 推送
```

### 场景 2: 想看看别人改了什么

```bash
git pull                      # 拉取最新
git log --oneline -10         # 看最近 10 条记录
```

### 场景 3: 改错了，想恢复

```bash
# 还没 add 的修改
git checkout -- 文件名

# 已经 add 了
git reset HEAD 文件名
git checkout -- 文件名

# 已经 commit 了（回退到上一次提交）
git reset --hard HEAD~1
```

### 场景 4: 想创建新功能分支

```bash
git checkout -b feature/new-agent    # 创建并切换到新分支
# ... 开发 ...
git add .
git commit -m "新功能完成"
git checkout main                    # 切回主分支
git merge feature/new-agent          # 合并
git push
```

## 常用缩写

| 命令 | 缩写 | 说明 |
|------|------|------|
| `git status` | `git st` | 查看状态 |
| `git checkout` | `git co` | 切换/恢复 |
| `git commit` | `git ci` | 提交 |
| `git branch` | `git br` | 分支 |

设置缩写（只需执行一次）：
```bash
git config --global alias.st status
git config --global alias.co checkout
git config --global alias.ci commit
git config --global alias.br branch
```

## 注意事项

1. **提交前先 `git status` 看看**，确认你要提交的文件是对的
2. **commit 信息要写清楚**，以后回看能知道改了什么
3. **不要提交大文件**（>100MB），用 Git LFS
4. **不要提交敏感信息**（密码、API Key），已在 .gitignore 中排除

## 查看仓库

浏览器打开：https://github.com/kicbed/CPP_FWI-agent
