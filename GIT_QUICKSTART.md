# Git 快速使用指南

本仓库通过 SSH 使用 GitHub。完整的 SSH 配置、首次绑定和安全发布说明见
[docs/github-setup.md](docs/github-setup.md)。

## 日常检查

```bash
git branch --show-current
git status --short
git diff --stat
git diff
```

只添加本次确认要提交的路径，不要直接把整个工作区全部暂存：

```bash
git add path/to/source.cpp path/to/test.cpp docs/relevant-guide.md
git diff --cached --stat
git diff --cached
git commit -m "feat: describe the change"
git push
```

提交前确认暂存区没有 `.env`、API Key、私钥、模型、FWI 运行结果、日志、缓存和编译目录。

## 获取远端更新

工作区干净时使用：

```bash
git fetch --prune origin
git pull --ff-only
```

## 创建功能分支

```bash
git switch -c feature/descriptive-name
```

开发完成后先运行测试、检查暂存差异，再提交和推送该功能分支。不要未经评审直接覆盖
`main`。

## 安全撤销

取消暂存但保留工作区内容：

```bash
git restore --staged path/to/file
```

已经发布的提交需要撤回时，创建可审计的反向提交：

```bash
git revert COMMIT_ID
```

不要在有未保存修改时运行 `git reset --hard`、`git checkout --` 或强制推送；这些命令
可能不可恢复地丢失其他人的本地工作。

## 查看历史

```bash
git log --oneline --decorate --graph -20
git log --follow -p -- path/to/file
```

仓库地址和远端归属以 `git remote -v` 的现场结果为准，不在教程里写死个人账户。
