# GitHub SSH 配置与安全发布

本项目已经是 Git 仓库并已配置远端。日常开发时不要再次执行 `git init`、不要覆盖
`.gitignore`，也不要用 `git reset --hard` 清理工作区。发布前先检查实际差异和敏感文件。

## 1. 配置 SSH Key

先查看是否已有可用公钥：

```bash
ls -l ~/.ssh/*.pub 2>/dev/null
```

没有时生成一对新密钥。建议为私钥设置口令，不要把私钥复制到仓库或聊天中：

```bash
ssh-keygen -t ed25519 -C "your-email@example.com"
```

只复制 `.pub` 公钥内容：

```bash
cat ~/.ssh/id_ed25519.pub
```

在 GitHub 的 **Settings → SSH and GPG keys → New SSH key** 中添加公钥，然后验证：

```bash
ssh -T git@github.com
```

GitHub 会说明认证成功但不提供 shell，这是正常结果。

## 2. 检查本仓库远端

```bash
cd /path/to/agent-communication-main-v2
git remote -v
git branch --show-current
git status --short
```

远端应使用 SSH 形式，例如：

```text
git@github.com:OWNER/REPOSITORY.git
```

如果尚无 `origin`，先在 GitHub 创建空仓库，再添加它：

```bash
git remote add origin git@github.com:OWNER/REPOSITORY.git
```

如果 `origin` 已存在，不要重复添加；需要更换地址时先人工确认仓库归属，再执行
`git remote set-url origin ...`。

## 3. 安全提交与推送

先同步远端信息并检查工作区：

```bash
git fetch --prune origin
git status --short
git diff --stat
git diff
```

只暂存本次明确要发布的文件。不要习惯性使用 `git add .`：

```bash
git add README.md docs/DEPLOYMENT.md scripts/codex-project.sh
git diff --cached --stat
git diff --cached
```

确认暂存区不包含 `.env`、API Key、私钥、模型、运行结果、日志或编译目录后再提交：

```bash
git commit -m "docs: update deployment workflow"
git push -u origin "$(git branch --show-current)"
```

后续同一分支通常只需 `git push`。本项目约定不要自动推送主分支；先在实现分支完成测试和
评审，再按团队流程合并。

## 4. 拉取与撤销

工作区干净时优先快进拉取，避免意外生成合并提交：

```bash
git pull --ff-only
```

撤销暂存但保留本地修改：

```bash
git restore --staged path/to/file
```

已经发布的错误提交应创建可审计的反向提交：

```bash
git revert COMMIT_ID
```

不要在包含未保存修改的工作区使用 `git reset --hard`、`git checkout --` 或强制推送。

## 5. 新仓库的首次发布（仅限全新目录）

以下流程只适用于一个尚未初始化、且内容已经人工检查过的全新目录：

```bash
cd /path/to/new-repository
git init
git status --short
# 先用编辑器创建并审查 .gitignore，再逐项添加需要发布的文件
git add .gitignore README.md CMakeLists.txt src/ include/
git diff --cached
git commit -m "chore: initialize repository"
git branch -M main
git remote add origin git@github.com:OWNER/REPOSITORY.git
git push -u origin main
```

路径不存在时不要照抄示例文件列表；根据实际项目逐项选择。

## 6. 大文件与本项目边界

本仓库不提交 CMake build、Python 虚拟环境、Deepwave/PyTorch 缓存、Marmousi 模型、
`/root/fwi-runs` 结果、Redis 数据、日志或 PID。它们应由使用者在本地构建、下载或挂载。
确实需要版本化的大型公开数据，应先讨论制品仓库或 Git LFS，而不是直接加入 Git 历史。

参考：[GitHub SSH 文档](https://docs.github.com/en/authentication/connecting-to-github-with-ssh)
和 [Pro Git](https://git-scm.com/book/zh/v2)。
