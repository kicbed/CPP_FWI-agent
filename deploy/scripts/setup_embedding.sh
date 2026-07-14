#!/usr/bin/env bash
# Prepare the optional local Embedding runtime in the repository-external venv.

set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
PYTHON=/root/.venvs/cpp-fwi-agent/bin/python
MODEL_NAME="${1:-${LOCAL_EMBEDDING_MODEL:-Qwen/Qwen3-Embedding-0.6B}}"

[[ -x "$PYTHON" ]] || {
    printf '错误: 隔离环境不存在: %s\n请先按 docs/DEPLOYMENT.md 创建仓库外 venv。\n' \
        "$PYTHON" >&2
    exit 1
}
[[ -n "$MODEL_NAME" && ${#MODEL_NAME} -le 200 && "$MODEL_NAME" != *$'\r'* && \
   "$MODEL_NAME" != *$'\n'* ]] || {
    printf '错误: 模型名称必须是 1～200 字节且不含换行\n' >&2
    exit 2
}

printf '[1/2] 在仓库外环境安装本地 Embedding 依赖……\n'
"$PYTHON" -m pip install \
    'flask>=3.1,<4' \
    'sentence-transformers>=5.5,<6'

printf '[2/2] 显式下载并验证模型缓存（启动脚本本身不会联网下载）……\n'
"$PYTHON" - "$MODEL_NAME" <<'PY'
import sys
from sentence_transformers import SentenceTransformer

name = sys.argv[1]
model = SentenceTransformer(name, device="cpu", trust_remote_code=False)
dimension = model.get_sentence_embedding_dimension()
if not isinstance(dimension, int) or dimension <= 0:
    raise SystemExit("Embedding model returned an invalid dimension")
print(f"Embedding model cache ready: model={name}, dimension={dimension}, device=cpu")
PY

printf '\n准备完成。建议在可信的 .env 中设置：\n'
printf '  ENABLE_LOCAL_EMBEDDING=auto\n'
printf '  EMBEDDING_PROVIDER=local\n'
printf '  LOCAL_EMBEDDING_MODEL=%s\n' "$MODEL_NAME"
printf '  LOCAL_EMBEDDING_DEVICE=cpu\n'
printf '随后使用统一入口启动：%s/start.sh\n' "$PROJECT_ROOT"
