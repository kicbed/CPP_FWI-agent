#!/usr/bin/env bash

set -Eeuo pipefail
umask 077

REPO_ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$REPO_ROOT"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf -- "$TMP_ROOT"' EXIT

fail() {
    printf 'test_project_continuity_contract: %s\n' "$*" >&2
    exit 1
}

require_text() {
    local file="$1" text="$2"
    grep -Fq -- "$text" "$file" || fail "$file is missing required text: $text"
}

required_files=(
    AGENTS.md
    docs/PROJECT_CONTINUITY.md
    docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md
    docs/architecture/SCIENTIFIC_RUNTIME_P0_CONTRACTS.md
    docs/PROJECT_PROGRESS.md
    docs/GIT_AND_PROMPT_POLICY.md
    contracts/scientific_runtime/v1/common.schema.json
    contracts/scientific_runtime/v1/dataset-ref.schema.json
    contracts/scientific_runtime/v1/algorithm-manifest.schema.json
    contracts/scientific_runtime/v1/task-draft.schema.json
    contracts/scientific_runtime/v1/plan-graph.schema.json
    contracts/scientific_runtime/v1/approval-decision.schema.json
    contracts/scientific_runtime/v1/run-event.schema.json
    contracts/scientific_runtime/v1/artifact-manifest.schema.json
)

for file in "${required_files[@]}"; do
    resolved="$(realpath -e -- "$file" 2>/dev/null || true)"
    [[ -f "$file" && ! -L "$file" && "$resolved" == "$REPO_ROOT/$file" ]] || \
        fail "required continuity file is absent or escapes through a symlink: $file"
done

require_text AGENTS.md 'docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md'
require_text AGENTS.md 'docs/PROJECT_PROGRESS.md'
require_text AGENTS.md 'docs/GIT_AND_PROMPT_POLICY.md'
require_text docs/PROJECT_CONTINUITY.md '## D-003：'
require_text docs/PROJECT_CONTINUITY.md '## D-004：'
require_text docs/PROJECT_CONTINUITY.md '## D-005：'
require_text docs/PROJECT_CONTINUITY.md 'D-003 是 D-001 的通用化，不替代 D-001'
require_text docs/PROJECT_CONTINUITY.md 'Proposed / awaiting user confirmation'
require_text docs/PROJECT_CONTINUITY.md 'P0 contracts Verified / Durable runtime pending'
require_text docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md '<!-- scientific-agent-runtime-plan: v1 -->'
require_text docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md '实现状态：**Pending**'
require_text docs/architecture/SCIENTIFIC_RUNTIME_P0_CONTRACTS.md '<!-- scientific-runtime-p0-contracts: v1 -->'
require_text docs/architecture/SCIENTIFIC_RUNTIME_P0_CONTRACTS.md 'TaskService、API、调度和 Adapter 尚未实现'
require_text docs/PROJECT_PROGRESS.md '<!-- project-progress-schema: v1 -->'
require_text docs/PROJECT_PROGRESS.md '当前阶段：**P1（尚未开始）**'
require_text docs/PROJECT_PROGRESS.md '| P0 最小 FWI 契约 | Verified |'
require_text docs/PROJECT_PROGRESS.md '下一可执行切片：P1.1'
require_text docs/GIT_AND_PROMPT_POLICY.md '<!-- git-prompt-policy: v1 -->'
require_text docs/GIT_AND_PROMPT_POLICY.md 'feature/scientific-agent-runtime'
require_text docs/GIT_AND_PROMPT_POLICY.md 'D-005` / **Proposed'
require_text docs/GIT_AND_PROMPT_POLICY.md '.local-prompts/'

for phase in P0 P1 P2 P3 P4 P5 P6; do
    grep -Eq "^### ${phase}：" docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md || \
        fail "runtime plan is missing phase $phase"
done

ignore_source="$(git check-ignore -v -- .local-prompts/example.md 2>/dev/null || true)"
[[ "$ignore_source" == .gitignore:* ]] || fail '.local-prompts/ is not ignored by the repository .gitignore'
ignore_source="$(git check-ignore -v -- scratch.local-prompt.md 2>/dev/null || true)"
[[ "$ignore_source" == .gitignore:* ]] || fail '*.local-prompt.md is not ignored by the repository .gitignore'

is_forbidden_git_path() {
    local path="$1" base lower_path lower_base wrapped
    base="${path##*/}"
    lower_path="${path,,}"
    lower_base="${base,,}"
    wrapped="/$lower_path/"

    case "$path" in
        resources/lab_code_adapter/logs/fwi_loss_stagnation.log|\
        resources/lab_code_adapter/logs/fwi_nan_instability.log)
            return 1
            ;;
    esac

    case "$wrapped" in
        */build/*|*/cmake-build-*/*|*/cmakefiles/*|*/testing/*|*/_deps/*|\
        */.ninja_*/*|*/.vscode/*|*/.idea/*|*/node_modules/*|*/__pycache__/*|\
        */.pytest_cache/*|*/.mypy_cache/*|*/.ruff_cache/*|*/.eggs/*|\
        */*.egg-info/*|*/.venv/*|*/venv/*|*/env/*|*/htmlcov/*|*/coverage/*|\
        */.cache/*|*/.claude/*|*/.codegraph/*|*/.runtime/*|\
        */.local-prompts/*|*/.ssh/*|*/.aws/*|*/.kube/*|*/redis-data/*|\
        */appendonlydir/*|*/resources/embeddings/*|*/local_knowledge/*|\
        */fwi-runs/*|*/runs/*|*/artifacts/*|*/logs/*|*/pids/*)
            return 0
            ;;
    esac

    case "$lower_path" in
        models/*|docs/upgrade/*prompts*.md|docs/upgrade/local-*.md|\
        docs/upgrade/next-session*.md|docs/superpowers/plans/*.md)
            return 0
            ;;
    esac

    case "$lower_base" in
        .env|.env.*)
            [[ "$lower_base" == '.env.example' ]] && return 1
            return 0
            ;;
        credentials*.json|secrets*.json|id_rsa*|id_ed25519*|.netrc|kubeconfig*|\
        *.pem|*.key|*.p12|*.pfx|*.jks|*.local-prompt.md|\
        *.o|*.obj|*.so|*.so.*|*.a|*.dylib|*.dll|*.lib|*.exe|*.pdb|a.out|\
        cmakecache.txt|ctesttestfile.cmake|cmake_install.cmake|compile_commands.json|*.ninja|\
        *.pyc|*.pyo|*.log|*.pid|*.aof|dump.rdb|*.sqlite|*.sqlite3|*.sqlite-*|*.db|*.db-*|\
        *.mat|*.npy|*.npz|*.sgy|*.segy|*.pt|*.pth|*.ckpt|\
        *.tmp|*.bak|*.backup|*.swp|*.swo|*~|.coverage|.ds_store|thumbs.db)
            return 0
            ;;
    esac
    return 1
}

for forbidden_example in \
    nested/.env nested/.env.production certs/client.pem tls/client.key \
    config/credentials-prod.json private/secrets.json models/model.npy models/model.npz \
    weights/model.pt checkpoints/model.ckpt output/run.log .runtime/task.sqlite3 \
    .runtime/task.db-wal lib/libworker.so.1 bin/helper.dll bin/helper.exe \
    node_modules/pkg/index.js .venv/bin/python redis/appendonly.aof service.pid \
    .local-prompts/window.md scratch.local-prompt.md .claude/settings.json \
    .codegraph/index.bin .cache/model/blob .vscode/settings.json .idea/workspace.xml \
    cmake/.ninja_deps/cache Python/pkg.egg-info/PKG-INFO htmlcov/index.html coverage/lcov.info \
    scratch.tmp scratch.bak scratch.backup scratch.swp scratch.swo editor~ .coverage \
    .DS_Store Thumbs.db docs/upgrade/next-session-private.md \
    docs/superpowers/plans/window.md models/private.bin; do
    is_forbidden_git_path "$forbidden_example" || \
        fail "forbidden-path matcher missed: $forbidden_example"
done

for allowed_example in \
    .env.example a2a/include/a2a/models/task.h src/algorithm.cpp \
    docs/GIT_AND_PROMPT_POLICY.md \
    resources/lab_code_adapter/logs/fwi_loss_stagnation.log \
    resources/lab_code_adapter/logs/fwi_nan_instability.log; do
    if is_forbidden_git_path "$allowed_example"; then
        fail "forbidden-path matcher rejected an explicit source/fixture path: $allowed_example"
    fi
done

visible_paths="$TMP_ROOT/git-visible-paths.bin"
forbidden_paths="$TMP_ROOT/forbidden-paths.txt"
git ls-files --cached --others --exclude-standard -z > "$visible_paths"
: > "$forbidden_paths"
while IFS= read -r -d '' path; do
    if is_forbidden_git_path "$path"; then
        printf '%q\n' "$path" >> "$forbidden_paths"
    fi
done < "$visible_paths"
if [[ -s "$forbidden_paths" ]]; then
    forbidden_summary="$(<"$forbidden_paths")"
    fail "generated, secret, model, prompt, or runtime paths are visible to Git: $forbidden_summary"
fi

secret_hits="$TMP_ROOT/secret-hits.txt"
: > "$secret_hits"
secret_token_pattern='sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|[Aa][Kk][Ii][Aa][0-9A-Za-z]{16}'
private_key_prefix='-----BEGIN'
private_key_marker="${private_key_prefix} PRIVATE KEY-----"
private_key_pattern="${private_key_prefix} (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
allowed_private_one='    write_file("certs/server.pem", "'"$private_key_marker"'\nTEST_ONLY\n");'
allowed_private_two='               "'"$private_key_marker"'\n"'

probe_file="$TMP_ROOT/secret-pattern-probe.txt"
for probe_value in \
    's''k-1234567890abcdefghijkl' \
    'g''ithub_pat_1234567890abcdefghijkl' \
    'A''KIA1234567890ABCDEF'; do
    printf '%s\n' "$probe_value" > "$probe_file"
    rg -q -e "$secret_token_pattern" -- "$probe_file" || \
        fail 'high-confidence token scanner failed its synthetic probe'
done
for private_kind in '' 'RSA ' 'EC ' 'OPENSSH ' 'DSA '; do
    printf '%s %s%s\n' "$private_key_prefix" "$private_kind" 'PRIVATE KEY-----' > "$probe_file"
    rg -q -e "$private_key_pattern" -- "$probe_file" || \
        fail "private-key scanner missed a synthetic ${private_kind:-generic}probe"
done

index_probe_repo="$TMP_ROOT/index-probe-repository"
mkdir -p -- "$index_probe_repo"
git -C "$index_probe_repo" init -q
index_probe_token='s''k-1234567890abcdefghijkl'
printf '%s\n' "$index_probe_token" > "$index_probe_repo/staged-token.txt"
printf '%s %s\n' "$private_key_prefix" 'OPENSSH PRIVATE KEY-----' > "$index_probe_repo/staged-key.txt"
git -C "$index_probe_repo" add staged-token.txt staged-key.txt
printf '%s\n' 'safe worktree replacement' > "$index_probe_repo/staged-token.txt"
printf '%s\n' 'safe worktree replacement' > "$index_probe_repo/staged-key.txt"
git -C "$index_probe_repo" grep --cached -I -E -q -e "$secret_token_pattern" -- || \
    fail 'index token scanner missed content present only in the staged blob'
git -C "$index_probe_repo" grep --cached -I -E -q -e "$private_key_pattern" -- || \
    fail 'index private-key scanner missed content present only in the staged blob'

validate_private_key_allowlist() {
    local logical_path="$1" content_file="$2" match_count
    [[ "$logical_path" == tests/test_code_agent_tools.cpp ]] || return 1
    match_count="$(rg -c -e "$private_key_pattern" -- "$content_file")"
    [[ "$match_count" == 2 ]] || return 1
    grep -Fxq -- "$allowed_private_one" "$content_file" || return 1
    grep -Fxq -- "$allowed_private_two" "$content_file" || return 1
}

scan_worktree_candidate() {
    local path="$1" rg_status
    [[ -f "$path" && ! -L "$path" ]] || return 0
    if rg -q -e "$secret_token_pattern" -- "$path"; then
        printf '%q\n' "$path" >> "$secret_hits"
    else
        rg_status=$?
        [[ $rg_status -eq 1 ]] || fail "worktree secret scanner could not read: $path"
    fi

    if rg -q -e "$private_key_pattern" -- "$path"; then
        validate_private_key_allowlist "$path" "$path" || printf '%q\n' "$path" >> "$secret_hits"
    else
        rg_status=$?
        [[ $rg_status -eq 1 ]] || fail "worktree private-key scanner could not read: $path"
    fi
}

while IFS= read -r -d '' path; do
    scan_worktree_candidate "$path"
done < "$visible_paths"

index_token_hits="$TMP_ROOT/index-token-hits.bin"
if git grep --cached -I -E -z -l -e "$secret_token_pattern" -- > "$index_token_hits"; then
    while IFS= read -r -d '' path; do
        printf '%q\n' "$path" >> "$secret_hits"
    done < "$index_token_hits"
else
    git_grep_status=$?
    [[ $git_grep_status -eq 1 ]] || fail 'unable to scan staged blobs for high-confidence tokens'
fi

index_private_hits="$TMP_ROOT/index-private-hits.bin"
if git grep --cached -I -E -z -l -e "$private_key_pattern" -- > "$index_private_hits"; then
    while IFS= read -r -d '' path; do
        if [[ "$path" == tests/test_code_agent_tools.cpp ]]; then
            index_blob="$TMP_ROOT/index-private-allowlist.cpp"
            git cat-file blob ":$path" > "$index_blob" || fail "unable to read staged blob: $path"
            validate_private_key_allowlist "$path" "$index_blob" || printf '%q\n' "$path" >> "$secret_hits"
        else
            printf '%q\n' "$path" >> "$secret_hits"
        fi
    done < "$index_private_hits"
else
    git_grep_status=$?
    [[ $git_grep_status -eq 1 ]] || fail 'unable to scan staged blobs for private-key material'
fi

if [[ -s "$secret_hits" ]]; then
    secret_summary="$(sort -u -- "$secret_hits")"
    fail "high-confidence credential material found in Git-visible files: $secret_summary"
fi

printf 'project continuity contract tests passed\n'
