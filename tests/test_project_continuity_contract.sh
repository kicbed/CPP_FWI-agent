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

reject_text() {
    local file="$1" text="$2"
    if grep -Fq -- "$text" "$file"; then
        fail "$file contains forbidden stale text: $text"
    fi
}

is_allowed_decision_token() {
    case "$1" in
        D-001|D-002|D-003|D-004|D-005|D-006|D-007|D-008|D-009|D-010|D-011|D-012)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

required_files=(
    .dockerignore
    .gitignore
    AGENTS.md
    README.md
    scripts/codex-project.sh
    docs/PROJECT_CURRENT_STATE.md
    docs/PROJECT_CONTINUITY.md
    docs/CODEX_WORKFLOW.md
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
    scientific_runtime/registrations/deepwave_acoustic_fwi_v1_5.json
    scientific_runtime/registrations/deepwave_acoustic_fwi_v1_6.json
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
    scientific_runtime/migrations/0012_task_timeout.sql
    scientific_runtime/migrations/0013_dispatch_reconciliation.sql
    scientific_runtime/migrations/0014_task_retry.sql
    scientific_runtime/migrations/0015_worker_exit_retry.sql
    scientific_runtime/migrations/0016_dispatch_negative_reconciliation.sql
    scientific_runtime/migrations/0017_checkpoint_wait_resume.sql
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
    fwi_worker/checkpoint.py
    fwi_worker/inversion.py
    fwi_worker/job_state.py
    tests/fwi_worker/test_checkpoint.py
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
require_text docs/PROJECT_CURRENT_STATE.md 'P2 已结束'
reject_text docs/PROJECT_CURRENT_STATE.md '完整 P2 仍在进行'
require_text AGENTS.md 'docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md'
current_state_lines="$(wc -l < docs/PROJECT_CURRENT_STATE.md)"
[[ "$current_state_lines" =~ ^[0-9]+$ && "$current_state_lines" -le 80 ]] || \
    fail "PROJECT_CURRENT_STATE.md exceeds the 80-line bounded bootstrap budget"
current_state_bytes="$(wc -c < docs/PROJECT_CURRENT_STATE.md)"
[[ "$current_state_bytes" =~ ^[0-9]+$ && "$current_state_bytes" -le 8192 ]] || \
    fail "PROJECT_CURRENT_STATE.md exceeds the 8192-byte bounded bootstrap budget"
if grep -Fq 'Read `docs/PROJECT_CONTINUITY.md` completely' AGENTS.md || \
   grep -Fq '`docs/PROJECT_PROGRESS.md` completely' AGENTS.md; then
    fail 'AGENTS.md restored unconditional long-document bootstrap reads'
fi
require_text AGENTS.md 'docs/PROJECT_PROGRESS.md'
require_text AGENTS.md 'docs/GIT_AND_PROMPT_POLICY.md'
require_text AGENTS.md "Do not alter or reinterpret an Accepted plan's scope"
require_text AGENTS.md 'Every estimate of remaining slices, time, or quantity must label its scope'
require_text AGENTS.md '## Highest-priority `D-*` authorization lock'
require_text AGENTS.md 'Never create, delete, renumber, reorder, or edit any numbered `D-*` entry'
require_text AGENTS.md 'Authorization exists only when the user later sends that fully populated'
require_text AGENTS.md '我原样确认 D-AUTH-<唯一编号>'
require_text AGENTS.md '<新增/删除/重编号/重排/修改>'
require_text AGENTS.md 'P6 exit criteria pass; P5 is never the project endpoint'
require_text AGENTS.md 'except for the highest-priority D lock'
require_text AGENTS.md 'Do not repair, revert, or update the'
require_text AGENTS.md 'Do not add a deeper `AGENTS.md` or any `AGENTS.override.md`'
require_text AGENTS.md 'After every Verified delivery slice, update the rolling remainder'
require_text AGENTS.md 'Any flat/increased result or new split requires the user'
expected_decisions=(D-001 D-002 D-003 D-004 D-005 D-006 D-007 D-008 D-009 D-010 D-011 D-012)
mapfile -t actual_decisions < <(
    sed -nE 's/^## (D-[0-9]{3})[：:].*/\1/p' docs/PROJECT_CONTINUITY.md
)
[[ "${#actual_decisions[@]}" -eq "${#expected_decisions[@]}" ]] || \
    fail 'PROJECT_CONTINUITY.md decision headings are missing, duplicated, or unapproved'
for decision_index in "${!expected_decisions[@]}"; do
    [[ "${actual_decisions[$decision_index]}" == "${expected_decisions[$decision_index]}" ]] || \
        fail 'PROJECT_CONTINUITY.md decision headings are not exactly D-001 through D-012 in order'
done
expected_numbered_headings=(
    '## D-001：FWI 任务入口、参数确认与人工控制'
    '## D-002：跨会话建议采纳和决策维护规则'
    '## D-003：双模式科研任务平台与持久任务内核'
    '## D-004：Git checkpoint 管理'
    '## D-005：AI 提示词分类管理'
    '## D-006：Marmousi FWI 高迭代上限'
    '## D-007：FWI 优化器可见性、非干扰轮询与持久任务找回'
    '### D-007 checkpoint 的实现与验证边界'
    '## D-008：对话—任务分离、可恢复删除与标准结果画廊'
    '## D-009：任务回收站永久删除本地结果'
    '## D-010：D-003 低 token 自动接续与 CodeGraph 导航'
    '## D-011：D-003 弹性中等切片、多算法边界与分级测试'
    '## D-012：有限自动重试次数、预算与失败边界'
)
mapfile -t actual_numbered_headings < <(
    grep -E '^#{1,6}[[:space:]].*D-[0-9]+' docs/PROJECT_CONTINUITY.md
)
[[ "${#actual_numbered_headings[@]}" -eq "${#expected_numbered_headings[@]}" ]] || \
    fail 'PROJECT_CONTINUITY.md contains a missing, extra, or malformed numbered D heading'
for heading_index in "${!expected_numbered_headings[@]}"; do
    [[ "${actual_numbered_headings[$heading_index]}" == "${expected_numbered_headings[$heading_index]}" ]] || \
        fail 'PROJECT_CONTINUITY.md numbered D headings changed or moved'
done
# Updating either lock baseline requires an exact copied D-AUTH targeting D-LOCK.
expected_agent_lock_hash='a2615e66dc88e64c6d37441573ad4f83b3a8e8a9450c47b632c031c7e15cf421'
actual_agent_lock_hash="$(
    awk '
        /^## Automatic session bootstrap/ { exit }
        { print }
    ' AGENTS.md | sha256sum | awk '{print $1}'
)"
[[ "$actual_agent_lock_hash" == "$expected_agent_lock_hash" ]] || \
    fail 'AGENTS.md D-LOCK changed without its exact copied D-AUTH authorization'
expected_lock_hash='e7cf7a055bdf8ab958558439602402bf93a8079a79b4789ba9fa8e0715f8f0d8'
actual_lock_hash="$(
    awk -v prefix='## `D-*` 条目最高优先级授权锁（向前生效）' '
        index($0, prefix) == 1 { capture = 1 }
        capture && /^## / && index($0, prefix) != 1 { exit }
        capture { print }
    ' docs/PROJECT_CONTINUITY.md | sha256sum | awk '{print $1}'
)"
[[ "$actual_lock_hash" == "$expected_lock_hash" ]] || \
    fail 'D-LOCK changed without its exact copied D-AUTH authorization'
# Updating any value below is itself a protected D change and requires the
# exact copied D-AUTH protocol in AGENTS.md. Ordinary progress must not touch it.
declare -A expected_decision_hashes=(
    [D-001]='0d0bf7f3a0617014629218be1041159afe165bf2ea695d0818f4ff0d07136770'
    [D-002]='af1fae88690e5f943ecab613e83abecdc8da3a6c8bfa8b788d8de9cbe0b212ca'
    [D-003]='ac11b29c8f3faf143e30a4fa6b7a380fd7550b9904721a1324d8cbdde40822b7'
    [D-004]='f40b7a5f00682639c2ff4e053ad4b23cc0641e2871ea6ce141674a8c55d74cb2'
    [D-005]='2e25e29fd9d243c0337159fe1030d31b0d589e710d9388be7527416ee715bdce'
    [D-006]='6af3c4496a5f12dbc6ab24d5a7e519a279f9a913ce55c553d77a4524e0ca102e'
    [D-007]='c0401cd338339387d31c200bcf347a59059ccac26c27c20233afc0b39a2daead'
    [D-008]='a0e8ed7814d434edef95914f19ef38f4c53404df5e59024c35b927a1282fff03'
    [D-009]='27a08e843664f4bf90654e9112aa6737d2e941f04b97adbb6a08904e029c5946'
    [D-010]='ac36d7d5280d61d7265a10e79e58912a58c174a926f9601ab9d3aad9230fac65'
    [D-011]='76ed85c0a4d5c11041e884b98dae6ace9a80f81f90b33e6aa2e464876075f750'
    [D-012]='0aa0aa05188c87343c1a1b388b252176d7ee6983a989463b49fd3e9a14aaa03d'
)
for decision_id in "${expected_decisions[@]}"; do
    actual_decision_hash="$(
        awk -v prefix="## ${decision_id}：" '
            index($0, prefix) == 1 { capture = 1 }
            capture && /^## / && index($0, prefix) != 1 { exit }
            capture { print }
        ' docs/PROJECT_CONTINUITY.md | sha256sum | awk '{print $1}'
    )"
    [[ "$actual_decision_hash" == "${expected_decision_hashes[$decision_id]}" ]] || \
        fail "$decision_id body changed without updating the protected D-AUTH snapshot"
done
require_text docs/PROJECT_CONTINUITY.md '## D-012：有限自动重试次数、预算与失败边界'
require_text docs/PROJECT_CONTINUITY.md '## `D-*` 条目最高优先级授权锁（向前生效）'
require_text docs/PROJECT_CONTINUITY.md '历史 `D-001`～`D-012` 既往不究并保持原状'
require_text docs/PROJECT_CONTINUITY.md '单独、原样复制 Codex 给出的完整实值句子才构成一次授权'
require_text docs/PROJECT_CONTINUITY.md '<新增/删除/重编号/重排/修改>'
require_text docs/PROJECT_CONTINUITY.md '不得自动修复、回滚或更新基线'
require_text docs/PROJECT_CONTINUITY.md '不得新增更深层的 `AGENTS.md` 或任何 `AGENTS.override.md`'
require_text docs/PROJECT_CONTINUITY.md 'Codex 也不得自行分配新的 `D-*` 编号（包括 Proposed）'
require_text docs/PROJECT_CONTINUITY.md '完整 D-003 的固定阶段顺序是 P0 → P1 → P2 → P3 → P4 → P5 → P6'
require_text docs/PROJECT_CONTINUITY.md '覆盖当时完整 P2 余项与 P3–P6，不是当前余量或配额'
require_text docs/PROJECT_CONTINUITY.md '本期剩余 = 上期剩余 - 本 checkpoint 新增 Verified + 本 checkpoint 用户明确批准的调整'
require_text docs/PROJECT_CONTINUITY.md '每个 Verified 后若余量持平或上调'
require_text docs/PROJECT_CONTINUITY.md 'P2-009B Implemented → Verified'
require_text docs/PROJECT_CONTINUITY.md 'P2-009B2 运行后退出重试均已 Verified'
require_text docs/PROJECT_CONTINUITY.md '低 token 自动接续与 CodeGraph 导航'
require_text docs/PROJECT_CONTINUITY.md 'D-003 是 D-001 的通用化，不替代 D-001'
require_text docs/PROJECT_CONTINUITY.md 'Proposed / awaiting user confirmation'
require_text docs/PROJECT_CONTINUITY.md 'P2.1–P2.9A 与 P2.9B1/B2'
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
require_text docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md 'P2.9B1 已于 2026-07-17 **Verified**'
require_text docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md 'P2.9B2 已于 2026-07-17 **Verified**'
require_text docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md 'legacy-private schema `1.0` 的 exact launched receipt'
require_text docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md '公共 Adapter `1.0`–`1.3` 不进入该边界'
require_text docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md '及 P3–P6 的历史粗估基线，不是当前余量或单个 P 级别配额'
require_text docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md '仅就 P2，B2 完成后剩余工作压缩为两个不降低出口质量的交付切片'
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
require_text docs/PROJECT_PROGRESS.md 'P2-001～P2-009B2'
require_text docs/PROJECT_PROGRESS.md '| P2 持久可靠性加固 | Verified（阶段完成） |'
require_text docs/PROJECT_PROGRESS.md '| P0 最小 FWI 契约 | Verified |'
require_text docs/PROJECT_PROGRESS.md '| P1 最小持久垂直切片 | Verified |'
require_text docs/PROJECT_PROGRESS.md '当前新 Guided 任务使用 contract minor `1.1.0`'
require_text docs/PROJECT_PROGRESS.md 'P1-008 / D-008'
require_text docs/PROJECT_PROGRESS.md 'P2-002 / D-008'
require_text docs/PROJECT_PROGRESS.md 'P2-003 / D-009'
require_text docs/PROJECT_PROGRESS.md 'P2-005C fenced Worker 证据投影与 late adoption'
require_text docs/PROJECT_PROGRESS.md 'P2-007 exact-attempt user cancellation'
require_text docs/PROJECT_PROGRESS.md 'P2-008 exact-attempt wall-time timeout'
require_text docs/PROJECT_PROGRESS.md 'P2 Completed / Verified'
require_text docs/PROJECT_PROGRESS.md 'P2-009A 有界 positive receipt resolution/adoption'
require_text docs/PROJECT_PROGRESS.md 'P2-009B1 已 Verified'
require_text docs/PROJECT_PROGRESS.md 'P2-009B2 已 Verified'
require_text docs/PROJECT_PROGRESS.md 'Runtime 360/360'
require_text docs/PROJECT_PROGRESS.md 'managed spawned + exact ready + heartbeat'
require_text docs/PROJECT_PROGRESS.md '有界 `action_required`/`resolved` 投影'
require_text docs/PROJECT_PROGRESS.md '`automatic_reconciliation=false` 与 `retry=false`'
require_text docs/PROJECT_PROGRESS.md '## 滚动剩余交付估算（D-011）'
require_text docs/PROJECT_PROGRESS.md '`0cbe131`（D-011 基线）'
require_text docs/PROJECT_PROGRESS.md '约 6 个；P2 = 2，P3–P6 合计暂估约 4'
require_text docs/PROJECT_PROGRESS.md '本 checkpoint 新增 Verified'
require_text docs/PROJECT_PROGRESS.md '自基线累计 Verified'
require_text docs/PROJECT_PROGRESS.md '本期剩余 = 上期剩余 - 本 checkpoint 新增 Verified + 本 checkpoint 用户明确批准的调整'
require_text docs/PROJECT_PROGRESS.md '避免重复扣减'
require_text docs/PROJECT_PROGRESS.md 'Verified 交付必须在同一 checkpoint 按公式递减'
require_text docs/PROJECT_PROGRESS.md 'P2 剩余一个路线切片但跨两个工作轮次'
require_text docs/PROJECT_PROGRESS.md '约 5 个；P2 = 1，P3–P6 合计暂估约 4'
require_text docs/PROJECT_PROGRESS.md 'checkpoint / Waiting / resume'
require_text docs/PROJECT_PROGRESS.md 'launch/ticket failed'
require_text docs/PROJECT_PROGRESS.md '合同当前 32/32'
require_text docs/PROJECT_CURRENT_STATE.md '全项目 P2–P6 粗估基线约 12 个'
require_text docs/PROJECT_CURRENT_STATE.md '当前约 3 个，P2/P3 为 0'
require_text docs/PROJECT_CURRENT_STATE.md 'P0、P1、P2、P3'
require_text docs/PROJECT_CURRENT_STATE.md '完整回归和 fresh CPU/CUDA HTTP/SSE 阶段出口通过'
require_text docs/PROJECT_CURRENT_STATE.md 'P4–P6 仍按固定顺序 Pending'
require_text docs/PROJECT_CURRENT_STATE.md '最高优先级 D 锁'
require_text docs/PROJECT_CURRENT_STATE.md 'P6 评测/观测/安全加固是全项目最终出口，P5 不是项目终点'
require_text docs/PROJECT_CURRENT_STATE.md '默认执行一个有界工作轮次'
require_text docs/PROJECT_CURRENT_STATE.md '第三轮及以后须先获用户明确批准'
require_text docs/PROJECT_CURRENT_STATE.md '不得复制完整实现史、测试日志或状态矩阵'
reject_text docs/PROJECT_CURRENT_STATE.md 'Runtime 360/360'
require_text docs/PROJECT_PROGRESS.md '完整 reconciliation 矩阵'
require_text docs/PROJECT_PROGRESS.md 'D 的任何新增/删除/重编号/重排/正文修改'
require_text docs/PROJECT_PROGRESS.md '单独原样复制 `D-AUTH` 句'
require_text docs/PROJECT_PROGRESS.md '路线切片与有界工作轮次分离'
require_text docs/PROJECT_PROGRESS.md '第三轮及以后须先获用户明确批准'
require_text AGENTS.md 'A bare “continue D-003” advances one work cycle'
require_text AGENTS.md 'a third or later independent audit requires explicit user'
require_text AGENTS.md 'within both 80 lines and 8192 bytes'
require_text docs/CODEX_WORKFLOW.md '历史 D-001～D-012 保持原状'
require_text docs/CODEX_WORKFLOW.md '单独、原样复制该句才'
require_text docs/CODEX_WORKFLOW.md '只有 P6 出口通过才算全项目完成'
require_text docs/CODEX_WORKFLOW.md '当前对话中的直接要求除最高优先级 D 锁外优先'
require_text docs/CODEX_WORKFLOW.md '默认执行一个有界工作轮次'
require_text docs/CODEX_WORKFLOW.md '路线切片出口只运行一次相关 aggregate'
require_text docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md '完整项目固定为 P0→P1→P2→P3→P4→P5→P6'
require_text docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md '只有 P6 出口通过才完成'
require_text docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md '当前对话中的最新明确指示除最高优先级 D 锁外优先'
require_text docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md '工作轮次是一次 Codex'
require_text docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md '阶段出口才跑完整回归'
require_text README.md '固定全项目顺序为 P0→P1→P2→P3→P4→P5→P6'
require_text README.md '只有通过 P6 的评测、观测和安全加固出口才算完整项目完成'
require_text README.md '单独原样复制其唯一 `D-AUTH` 授权句'
require_text README.md '成功后预期恰好八张标准结果卡'
require_text README.md '初始模型、反演模型、模型误差、炮集和损失曲线六张 PNG'
require_text scripts/codex-project.sh 'Highest-priority D-* lock'
require_text scripts/codex-project.sh 'fully populated one-time D-AUTH sentence alone and verbatim'
for continuity_file in \
    AGENTS.md \
    README.md \
    scripts/codex-project.sh \
    docs/CODEX_WORKFLOW.md \
    docs/PROJECT_CURRENT_STATE.md \
    docs/PROJECT_CONTINUITY.md \
    docs/PROJECT_PROGRESS.md \
    docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md; do
    reject_text "$continuity_file" 'P3–P5'
    reject_text "$continuity_file" 'P3、P4、P5）的粗估'
    reject_text "$continuity_file" '尚未实现：有限 retry'
    reject_text "$continuity_file" '视为明确批准'
    reject_text "$continuity_file" '视为对该决定的明确批准'
    reject_text "$continuity_file" '只有具体安全或验证证据才允许继续拆分'
    reject_text "$continuity_file" '仍可记录后拆分'
    reject_text "$continuity_file" '可以合理增加切片并在进度账本记录依据'
    reject_text "$continuity_file" '若出现新的具体安全边界仍须记录后拆分'
done
while IFS= read -r -d '' tracked_file; do
    case "$tracked_file" in
        AGENTS.md)
            ;;
        AGENTS.override.md|*/AGENTS.md|*/AGENTS.override.md)
            fail "$tracked_file can override the repository D-LOCK"
            ;;
    esac
    while IFS= read -r path_decision_token; do
        [[ -n "$path_decision_token" ]] || continue
        is_allowed_decision_token "$path_decision_token" || \
            fail "$tracked_file path contains an unapproved decision token: $path_decision_token"
    done < <(printf '%s\n' "$tracked_file" | grep -Eo 'D-[0-9]{2,}' | LC_ALL=C sort -u || true)
    [[ -f "$tracked_file" && ! -L "$tracked_file" ]] || continue
    while IFS= read -r decision_token; do
        [[ -n "$decision_token" ]] || continue
        is_allowed_decision_token "$decision_token" || \
            fail "$tracked_file contains an unapproved decision token: $decision_token"
    done < <(grep -I -Eo 'D-[0-9]{2,}' -- "$tracked_file" | LC_ALL=C sort -u || true)
    [[ "$tracked_file" == docs/PROJECT_CONTINUITY.md ]] && continue
    while IFS= read -r decision_heading; do
        [[ -n "$decision_heading" ]] || continue
        if [[ "$tracked_file" == docs/PROJECT_CURRENT_STATE.md && \
              "$decision_heading" == '# D-003 当前工作入口' ]]; then
            continue
        fi
        fail "$tracked_file contains a numbered decision heading outside PROJECT_CONTINUITY.md"
    done < <(grep -I -E '^#{1,6}[[:space:]]+D-[0-9]+' -- "$tracked_file" || true)
done < <(git ls-files --cached --others --exclude-standard -z)

agents_scan="$TMP_ROOT/agents-files"
if ! find -P . -path './.git' -prune -o \
    \( -name AGENTS.md -o -name AGENTS.override.md \) -print0 > "$agents_scan"; then
    fail 'could not audit the worktree for nested AGENTS files'
fi
while IFS= read -r -d '' agents_file; do
    [[ "$agents_file" == './AGENTS.md' && ! -L "$agents_file" ]] || \
        fail "$agents_file can override the repository D-LOCK"
done < "$agents_scan"

for project_scope_file in \
    AGENTS.md \
    README.md \
    docs/CODEX_WORKFLOW.md \
    docs/PROJECT_CURRENT_STATE.md \
    docs/PROJECT_CONTINUITY.md \
    docs/PROJECT_PROGRESS.md \
    docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md; do
    p5_only_range="$({
        grep -En 'P[0-4][[:space:]]*[-–—至到][[:space:]]*P5([^0-9]|$)|P2[^P]{0,20}P3[^P]{0,20}P4[^P]{0,20}P5|P3[^P]{0,20}P4[^P]{0,20}P5' \
            "$project_scope_file" || true
    } | grep -Ev 'P6' || true)"
    if [[ -n "$p5_only_range" ]]; then
        fail "$project_scope_file contains a project range that stops at P5"
    fi
    if grep -Eq 'P5[[:space:]]*(是|为|作为|就是)[[:space:]]*((完整|整个|全)[[:space:]]*)?项目(的)?[[:space:]]*(终点|最终出口|结束点|完成点)|(完整|整个|全)[[:space:]]*项目[[:space:]]*(到|止于|截至|结束于|完成于)[[:space:]]*P5([[:space:]]*(完成|结束))?' \
        "$project_scope_file"; then
        fail "$project_scope_file describes P5 as the project endpoint"
    fi
done
require_text scientific_runtime/migrations/0011_task_cancellation.sql 'CREATE TABLE task_cancel_requests'
require_text scientific_runtime/migrations/0011_task_cancellation.sql 'deliver_exact_attempt_cancel'
require_text scientific_runtime/migrations/0012_task_timeout.sql 'CREATE TABLE worker_attempt_timeout_windows'
require_text scientific_runtime/migrations/0012_task_timeout.sql 'deliver_exact_attempt_timeout'
require_text scientific_runtime/migrations/0014_task_retry.sql 'CREATE TABLE worker_retry_reservations'
require_text scientific_runtime/migrations/0014_task_retry.sql 'CREATE TABLE worker_retry_exhaustions'
require_text scientific_runtime/migrations/0015_worker_exit_retry.sql 'CREATE TABLE worker_exit_retry_reservations'
require_text scientific_runtime/migrations/0015_worker_exit_retry.sql 'CREATE TABLE worker_exit_retry_dispatch_replacements'
require_text scientific_runtime/migrations/0015_worker_exit_retry.sql 'CREATE TABLE worker_exit_retry_exhaustions'
require_text scientific_runtime/migrations/0016_dispatch_negative_reconciliation.sql 'CREATE TABLE dispatch_reconciliation_observations'
require_text scientific_runtime/migrations/0016_dispatch_negative_reconciliation.sql 'CREATE TABLE dispatch_reconciliation_negative_resolutions'
require_text docs/PROJECT_PROGRESS.md 'D-010 / PREP-004'
require_text docs/GIT_AND_PROMPT_POLICY.md '<!-- git-prompt-policy: v1 -->'
require_text docs/GIT_AND_PROMPT_POLICY.md 'feature/scientific-agent-runtime'
require_text docs/GIT_AND_PROMPT_POLICY.md 'D-005` / **Proposed'
require_text docs/GIT_AND_PROMPT_POLICY.md '.local-prompts/'
require_text .dockerignore '**/*.sqlite3-*'
require_text .dockerignore '**/*.db-*'

previous_phase_line=0
for phase in P0 P1 P2 P3 P4 P5 P6; do
    phase_line="$(grep -n -m1 -E "^### ${phase}：" \
        docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md | cut -d: -f1 || true)"
    [[ "$phase_line" =~ ^[0-9]+$ ]] || fail "runtime plan is missing phase $phase"
    (( phase_line > previous_phase_line )) || fail "runtime plan phase $phase is out of order"
    previous_phase_line="$phase_line"
done
require_text docs/architecture/SCIENTIFIC_AGENT_RUNTIME_PLAN.md '### P6：评测、观测和安全加固'

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
