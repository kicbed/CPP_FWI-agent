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
    .dockerignore
    .gitignore
    AGENTS.md
    docs/PROJECT_CURRENT_STATE.md
    docs/PROJECT_CONTINUITY.md
    docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md
    docs/architecture/SCIENTIFIC_RUNTIME_P0_CONTRACTS.md
    docs/architecture/SCIENTIFIC_RUNTIME_P1_REGISTRY.md
    docs/architecture/SCIENTIFIC_RUNTIME_P1_FWI_ADAPTER.md
    docs/architecture/SCIENTIFIC_RUNTIME_P1_GUIDED_WEB.md
    docs/architecture/SCIENTIFIC_RUNTIME_P1_TASK_STORE.md
    docs/architecture/SCIENTIFIC_RUNTIME_P1_SUBMIT.md
    docs/PROJECT_PROGRESS.md
    docs/GIT_AND_PROMPT_POLICY.md
    scientific_runtime/__init__.py
    scientific_runtime/fwi_registry.py
    scientific_runtime/fwi_adapter.py
    scientific_runtime/registry_service.py
    scientific_runtime/registrations/deepwave_acoustic_fwi_v1.json
    scientific_runtime/registrations/deepwave_acoustic_fwi_v1_1.json
    scientific_runtime/registrations/deepwave_acoustic_fwi_v1_2.json
    scientific_runtime/registrations/deepwave_acoustic_fwi_v1_3.json
    scientific_runtime/registrations/deepwave_acoustic_fwi_v1_4.json
    scientific_runtime/task_store.py
    scientific_runtime/task_service.py
    scientific_runtime/task_dispatcher.py
    scientific_runtime/runtime_supervisor.py
    scientific_runtime/workbench_service.py
    worker_launch_bootstrap.py
    worker_launch_control.py
    scientific_runtime/migrations/0001_task_store.sql
    scientific_runtime/migrations/0002_catalog_registry.sql
    scientific_runtime/migrations/0003_submit_dispatch.sql
    scientific_runtime/migrations/0004_workbench_runtime.sql
    scientific_runtime/migrations/0005_task_discovery.sql
    scientific_runtime/migrations/0006_task_visibility.sql
    scientific_runtime/migrations/0007_task_purge.sql
    scientific_runtime/migrations/0008_runtime_supervisor.sql
    scientific_runtime/migrations/0009_worker_attempt_projection.sql
    scientific_runtime/migrations/0010_supervised_dispatch.sql
    scientific_runtime/migrations/0011_task_cancellation.sql
    tests/test_scientific_runtime_registry.py
    tests/test_scientific_runtime_fwi_adapter.py
    tests/test_scientific_runtime_fwi_purge.py
    tests/test_scientific_runtime_task_purge_store.py
    tests/test_scientific_runtime_task_service.py
    tests/test_scientific_runtime_runtime_supervisor.py
    tests/test_scientific_runtime_supervisor_store.py
    tests/test_scientific_runtime_workbench.py
    tests/test_worker_launch_control.py
    contracts/scientific_runtime/v1/common.schema.json
    contracts/scientific_runtime/v1/dataset-ref.schema.json
    contracts/scientific_runtime/v1/algorithm-manifest.schema.json
    contracts/scientific_runtime/v1/task-draft.schema.json
    contracts/scientific_runtime/v1/plan-graph.schema.json
    contracts/scientific_runtime/v1/approval-decision.schema.json
    contracts/scientific_runtime/v1/run-event.schema.json
    contracts/scientific_runtime/v1/artifact-manifest.schema.json
    fwi_worker/adapter_probe.py
    fwi_worker/__main__.py
    fwi_worker/artifacts.py
    fwi_worker/inversion.py
    fwi_worker/job_state.py
    tests/fwi_worker/test_state_artifacts.py
    tests/fwi_worker/test_worker_failure.py
    web/serve.py
    web/workbench_api.py
    web/tests/test_artifact_route.py
    web/tests/test_workbench_api.py
    web/tests/test_workbench_route.py
)

for file in "${required_files[@]}"; do
    resolved="$(realpath -e -- "$file" 2>/dev/null || true)"
    [[ -f "$file" && ! -L "$file" && "$resolved" == "$REPO_ROOT/$file" ]] || \
        fail "required continuity file is absent or escapes through a symlink: $file"
done

require_text AGENTS.md '<!-- codex-auto-bootstrap: v2 -->'
require_text AGENTS.md 'docs/PROJECT_CURRENT_STATE.md'
require_text AGENTS.md 'codegraph_explore'
require_text docs/PROJECT_CURRENT_STATE.md '<!-- project-current-state: v1 -->'
require_text docs/PROJECT_CURRENT_STATE.md '继续 D-003 时的最小读取集'
require_text docs/PROJECT_CURRENT_STATE.md 'CodeGraph 使用策略'
require_text docs/PROJECT_CURRENT_STATE.md '完整 P2 仍在进行'
require_text AGENTS.md 'docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md'
current_state_lines="$(wc -l < docs/PROJECT_CURRENT_STATE.md)"
[[ "$current_state_lines" =~ ^[0-9]+$ && "$current_state_lines" -le 160 ]] || \
    fail "PROJECT_CURRENT_STATE.md exceeds the 160-line bounded bootstrap budget"
if grep -Fq 'Read `docs/PROJECT_CONTINUITY.md` completely' AGENTS.md || \
   grep -Fq '`docs/PROJECT_PROGRESS.md` completely' AGENTS.md; then
    fail 'AGENTS.md restored unconditional long-document bootstrap reads'
fi
require_text AGENTS.md 'docs/PROJECT_PROGRESS.md'
require_text AGENTS.md 'docs/GIT_AND_PROMPT_POLICY.md'
require_text docs/PROJECT_CONTINUITY.md '## D-003：'
require_text docs/PROJECT_CONTINUITY.md '## D-004：'
require_text docs/PROJECT_CONTINUITY.md '## D-005：'
require_text docs/PROJECT_CONTINUITY.md '## D-006：'
require_text docs/PROJECT_CONTINUITY.md '## D-007：'
require_text docs/PROJECT_CONTINUITY.md '## D-008：'
require_text docs/PROJECT_CONTINUITY.md '## D-009：'
require_text docs/PROJECT_CONTINUITY.md '## D-010：'
require_text docs/PROJECT_CONTINUITY.md '低 token 自动接续与 CodeGraph 导航'
require_text docs/PROJECT_CONTINUITY.md 'D-003 是 D-001 的通用化，不替代 D-001'
require_text docs/PROJECT_CONTINUITY.md 'Proposed / awaiting user confirmation'
require_text docs/PROJECT_CONTINUITY.md 'P2.1–P2.8 有界切片'
require_text docs/PROJECT_CONTINUITY.md 'P2-008 exact-attempt wall-time timeout 已验证'
require_text docs/PROJECT_CONTINUITY.md '`not_triggered`'
require_text docs/PROJECT_CONTINUITY.md '`suppressed`'
require_text docs/PROJECT_CONTINUITY.md '`superseded`'
require_text docs/PROJECT_CONTINUITY.md '`timed_out`'
require_text docs/PROJECT_CONTINUITY.md '`POST /timeout` mutation 不存在'
require_text docs/PROJECT_CONTINUITY.md '完整 P2 Pending'
require_text docs/PROJECT_CONTINUITY.md '精确历史七个 form 字段'
require_text docs/PROJECT_CONTINUITY.md '当前 Algorithm/Adapter `deepwave.acoustic_fwi@1.4.0`/`1.4.0`'
require_text docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md '<!-- scientific-agent-runtime-plan: v1 -->'
require_text docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md '完整 P2 仍 Pending'
require_text docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md 'P2.7 exact-attempt user cancellation'
require_text docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md 'P2.8 exact-attempt wall-time timeout'
require_text docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md 'P2.8 exact-attempt wall-time timeout** 也已 Verified'
require_text docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md 'P2.9A 有界 positive receipt resolution/adoption'
require_text docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md 'P2.9A 有界 positive receipt resolution/adoption** 已 Verified'
require_text docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md 'legacy-private schema `1.0` 的 exact launched receipt'
require_text docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md '公共 Adapter `1.0`–`1.3` 不进入该边界'
require_text docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md 'finite retry 具体策略仍 Pending'
require_text docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md '`positive_receipt_reconciliation=true`'
require_text docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md 'terminal heartbeat'
require_text docs/architecture/SCIENTIFIC_RUNTIME_P0_CONTRACTS.md '<!-- scientific-runtime-p0-contracts: v1 -->'
require_text docs/architecture/SCIENTIFIC_RUNTIME_P0_CONTRACTS.md 'P1.1a TaskStore、P1.1b Registry 与 P1.2a 固定 Deepwave'
require_text docs/architecture/SCIENTIFIC_RUNTIME_P0_CONTRACTS.md 'submit/API/调度仍未实现'
require_text docs/architecture/SCIENTIFIC_RUNTIME_P1_REGISTRY.md '<!-- scientific-runtime-p1-registry: v1 -->'
require_text docs/architecture/SCIENTIFIC_RUNTIME_P1_REGISTRY.md 'registered/allowlisted 不等于 executable/ready'
require_text docs/architecture/SCIENTIFIC_RUNTIME_P1_REGISTRY.md '`task_queued` 和所有 pre-runtime→runtime store 转换继续'
require_text docs/architecture/SCIENTIFIC_RUNTIME_P1_FWI_ADAPTER.md '<!-- scientific-runtime-p1-fwi-adapter: v1 -->'
require_text docs/architecture/SCIENTIFIC_RUNTIME_P1_FWI_ADAPTER.md 'CANCEL_NOT_SUPPORTED_IN_P1'
require_text docs/architecture/SCIENTIFIC_RUNTIME_P1_FWI_ADAPTER.md 'registry_snapshot_provider'
require_text docs/architecture/SCIENTIFIC_RUNTIME_P1_TASK_STORE.md '<!-- scientific-runtime-p1-task-store: v1 -->'
require_text docs/architecture/SCIENTIFIC_RUNTIME_P1_TASK_STORE.md '父工作项 P1.1 仍为 **Partially implemented**'
require_text docs/architecture/SCIENTIFIC_RUNTIME_P1_TASK_STORE.md '也没有开放 `submit`/`Queued` 入口'
require_text docs/architecture/SCIENTIFIC_RUNTIME_P1_SUBMIT.md '<!-- scientific-runtime-p1-submit: v1 -->'
require_text docs/architecture/SCIENTIFIC_RUNTIME_P1_SUBMIT.md 'pending -> dispatching -> dispatched'
require_text docs/architecture/SCIENTIFIC_RUNTIME_P1_SUBMIT.md '不自动重发、退款、取消或标记 task Failed'
require_text docs/architecture/SCIENTIFIC_RUNTIME_P1_GUIDED_WEB.md '<!-- scientific-runtime-p1-guided-web: v1 -->'
require_text docs/architecture/SCIENTIFIC_RUNTIME_P1_GUIDED_WEB.md '实现状态：**Verified**'
require_text docs/architecture/SCIENTIFIC_RUNTIME_P1_GUIDED_WEB.md 'Guided 路由 fail closed 为 503'
require_text docs/architecture/SCIENTIFIC_RUNTIME_P1_GUIDED_WEB.md '当前浏览器的 create/revise mutation 始终发送完整九个 form 字段'
require_text docs/architecture/SCIENTIFIC_RUNTIME_P1_GUIDED_WEB.md '不广告 legacy Worker/MCP `forward`'
require_text docs/architecture/SCIENTIFIC_RUNTIME_P1_GUIDED_WEB.md '`P2-007` 已实现有界 exact-attempt user cancellation'
require_text docs/architecture/SCIENTIFIC_RUNTIME_P1_GUIDED_WEB.md '`P2-008` 已实现有界'
require_text docs/PROJECT_PROGRESS.md '<!-- project-progress-schema: v1 -->'
require_text docs/PROJECT_PROGRESS.md 'P2-001 有界发现/重开已验证'
require_text docs/PROJECT_PROGRESS.md '完整 P2 Pending'
require_text docs/PROJECT_PROGRESS.md '| P0 最小 FWI 契约 | Verified |'
require_text docs/PROJECT_PROGRESS.md '| P1 最小持久垂直切片 | Verified |'
require_text docs/PROJECT_PROGRESS.md '当前新 Guided 任务使用 contract minor `1.1.0`'
require_text docs/PROJECT_PROGRESS.md 'P1-008 / D-008'
require_text docs/PROJECT_PROGRESS.md 'P2-002 / D-008'
require_text docs/PROJECT_PROGRESS.md 'P2-003 / D-009'
require_text docs/PROJECT_PROGRESS.md 'P2-005C fenced Worker 证据投影与 late adoption'
require_text docs/PROJECT_PROGRESS.md 'P2-007 exact-attempt user cancellation'
require_text docs/PROJECT_PROGRESS.md 'P2-008 exact-attempt wall-time timeout'
require_text docs/PROJECT_PROGRESS.md 'P2-001–P2-009A 有界切片 Verified'
require_text docs/PROJECT_PROGRESS.md 'P2-009A 有界 positive receipt resolution/adoption'
require_text docs/PROJECT_PROGRESS.md 'managed spawned + exact ready + heartbeat'
require_text docs/PROJECT_PROGRESS.md '有界 `action_required`/`resolved` 投影'
require_text docs/PROJECT_PROGRESS.md '`automatic_reconciliation=false` 与 `retry=false`'
require_text docs/PROJECT_PROGRESS.md 'finite retry 具体策略仍 Pending'
require_text docs/PROJECT_PROGRESS.md 'launch/ticket failed'
require_text docs/PROJECT_CURRENT_STATE.md 'Failed / WALL_TIME_EXCEEDED'
require_text docs/PROJECT_CURRENT_STATE.md 'Store 对该 current Worker 的首条 durable `spawned + ready + running` observation'
require_text docs/PROJECT_CURRENT_STATE.md '`terminal_status` 七个字段'
require_text docs/PROJECT_CURRENT_STATE.md 'deadline 到来且 active term 持久授权前 Worker mutation 为零'
require_text docs/PROJECT_CURRENT_STATE.md 'P2-009A 已验证'
require_text docs/PROJECT_CURRENT_STATE.md '每个 Supervisor 周期最多执行一次 receipt probe'
require_text docs/PROJECT_CURRENT_STATE.md 'reconcile/retry/timeout POST mutation'
require_text docs/PROJECT_PROGRESS.md '完整 reconciliation/SSE、P3 DAG'
require_text scientific_runtime/migrations/0011_task_cancellation.sql 'CREATE TABLE task_cancel_requests'
require_text scientific_runtime/migrations/0011_task_cancellation.sql 'deliver_exact_attempt_cancel'
require_text scientific_runtime/migrations/0012_task_timeout.sql 'CREATE TABLE worker_attempt_timeout_windows'
require_text scientific_runtime/migrations/0012_task_timeout.sql 'deliver_exact_attempt_timeout'
require_text docs/PROJECT_PROGRESS.md 'D-010 / PREP-004'
require_text docs/GIT_AND_PROMPT_POLICY.md '<!-- git-prompt-policy: v1 -->'
require_text docs/GIT_AND_PROMPT_POLICY.md 'feature/scientific-agent-runtime'
require_text docs/GIT_AND_PROMPT_POLICY.md 'D-005` / **Proposed'
require_text docs/GIT_AND_PROMPT_POLICY.md '.local-prompts/'
require_text .dockerignore '**/*.sqlite3-*'
require_text .dockerignore '**/*.db-*'

for phase in P0 P1 P2 P3 P4 P5 P6; do
    grep -Eq "^### ${phase}：" docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md || \
        fail "runtime plan is missing phase $phase"
done

ignore_source="$(git check-ignore -v -- .local-prompts/example.md 2>/dev/null || true)"
[[ "$ignore_source" == .gitignore:* ]] || fail '.local-prompts/ is not ignored by the repository .gitignore'
ignore_source="$(git check-ignore -v -- scratch.local-prompt.md 2>/dev/null || true)"
[[ "$ignore_source" == .gitignore:* ]] || fail '*.local-prompt.md is not ignored by the repository .gitignore'
for runtime_database in \
    scratch.sqlite scratch.sqlite-wal scratch.sqlite3 scratch.sqlite3-shm \
    scratch.db scratch.db-wal; do
    ignore_source="$(git check-ignore -v -- "$runtime_database" 2>/dev/null || true)"
    [[ "$ignore_source" == .gitignore:* ]] || \
        fail "$runtime_database is not ignored by the repository .gitignore"
done

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
# Require a token boundary so ordinary identifiers such as
# "task-list-foreign-principal" do not expose the embedded "sk-list..."
# substring as a credential. The synthetic probes below still exercise every
# high-confidence token family at the beginning of a line.
secret_token_pattern='(^|[^A-Za-z0-9_])(sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|[Aa][Kk][Ii][Aa][0-9A-Za-z]{16})'
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
