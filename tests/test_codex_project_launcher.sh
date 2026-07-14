#!/usr/bin/env bash

set -Eeuo pipefail
umask 077

REPO_ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
LAUNCHER="$REPO_ROOT/scripts/codex-project.sh"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf -- "$TMP_ROOT"' EXIT

fail() {
    printf 'test_codex_project_launcher: %s\n' "$*" >&2
    exit 1
}

assert_contains() {
    local haystack="$1" needle="$2"
    [[ "$haystack" == *"$needle"* ]] || fail "missing expected text: $needle"
}

[[ -x "$LAUNCHER" ]] || fail "launcher is missing or not executable"
bash -n "$LAUNCHER"

# The collector itself must stay local/read-only. Codex may use its normal API
# connection after exec, but the launcher performs no endpoint/network probe.
if grep -Eq '^[[:space:]]*(exec[[:space:]]+)?(curl|wget|nc|ncat|ssh)([[:space:]]|$)' "$LAUNCHER"; then
    fail "launcher contains a network client command"
fi
if grep -Eq '(^|[[:space:]])(eval|source|inotifywait)([[:space:]]|$)|bash[[:space:]]+-c' "$LAUNCHER"; then
    fail "launcher contains dynamic shell evaluation, environment sourcing, or a watcher"
fi

FIXTURE="$TMP_ROOT/repository"
FAKE_BIN="$TMP_ROOT/bin"
FWI_ROOT="$TMP_ROOT/fwi-runs"
CAPTURE="$TMP_ROOT/codex-argv.bin"
JOB_ID='fwi-20260715T010203Z-acde1234'
mkdir -p -- "$FIXTURE/scripts" "$FIXTURE/docs" "$FAKE_BIN" "$FWI_ROOT/$JOB_ID"
cp -- "$LAUNCHER" "$FIXTURE/scripts/codex-project.sh"
chmod 755 "$FIXTURE/scripts/codex-project.sh"

printf '%s\n' '# Fixture instructions' > "$FIXTURE/AGENTS.md"
printf '%s\n' '# Fixture continuity' > "$FIXTURE/docs/PROJECT_CONTINUITY.md"
printf '%s\n' 'tracked baseline' > "$FIXTURE/tracked.txt"
printf '%s\n' 'safe example' > "$FIXTURE/.env.example"

git -C "$FIXTURE" init -q
git -C "$FIXTURE" config user.name 'Launcher Test'
git -C "$FIXTURE" config user.email 'launcher-test@example.invalid'
git -C "$FIXTURE" add AGENTS.md docs/PROJECT_CONTINUITY.md tracked.txt .env.example scripts/codex-project.sh
git -C "$FIXTURE" commit -qm 'fixture baseline'
git -C "$FIXTURE" switch -qc test/codex-context

printf '%s\n' 'tracked change' >> "$FIXTURE/tracked.txt"
printf '%s\n' 'safe example update' >> "$FIXTURE/.env.example"
printf '%s\n' 'ENV_CONTENT_MUST_NEVER_APPEAR=marker-secret-value' > "$FIXTURE/.env"
printf '%s\n' 'marker-production-secret' > "$FIXTURE/.env.production"
printf '%s\n' 'marker-private-key' > "$FIXTURE/client.key"
printf '%s\n' 'marker-credential-file' > "$FIXTURE/credentials-prod.json"

cat > "$FWI_ROOT/$JOB_ID/status.json" <<'JSON'
{
  "job_id": "fwi-20260715T010203Z-acde1234",
  "status": "running",
  "stage": "invert",
  "iteration": 17,
  "total_iterations": 50,
  "message": "API_KEY=marker-job-secret",
  "prompt": "marker-private-prompt"
}
JSON

cat > "$FAKE_BIN/codex" <<'SH'
#!/usr/bin/env bash
set -Eeuo pipefail
if [[ "${1:-}" == "--help" ]]; then
    cat <<'HELP'
Codex CLI
Usage: codex [OPTIONS] [PROMPT]
  -C, --cd <DIR>
  -s, --sandbox <SANDBOX_MODE>
      values: read-only, workspace-write
  -a, --ask-for-approval <APPROVAL_POLICY>
      values: untrusted, on-request, never
HELP
    exit 0
fi
: "${CODEX_CAPTURE:?}"
printf '%s\0' "$@" > "$CODEX_CAPTURE"
SH
chmod 755 "$FAKE_BIN/codex"

export PATH="$FAKE_BIN:$PATH"
export FWI_RUN_ROOT="$FWI_ROOT"
export CODEX_CAPTURE="$CAPTURE"

check_output="$($FIXTURE/scripts/codex-project.sh --check)"
assert_contains "$check_output" 'codex-project check: OK'
assert_contains "$check_output" 'local metadata only'
assert_contains "$check_output" 'workspace-write'
assert_contains "$check_output" 'web search not enabled'

before_status="$(git -C "$FIXTURE" status --short --untracked-files=all)"
context="$($FIXTURE/scripts/codex-project.sh --print-context)"
after_status="$(git -C "$FIXTURE" status --short --untracked-files=all)"
[[ "$before_status" == "$after_status" ]] || fail "context collection changed the worktree"

assert_contains "$context" 'Read AGENTS.md completely.'
assert_contains "$context" 'Read docs/PROJECT_CONTINUITY.md completely.'
assert_contains "$context" 'branch: test/codex-context'
assert_contains "$context" 'recent_commit:'
assert_contains "$context" 'tracked.txt'
assert_contains "$context" '[sensitive path redacted]'
assert_contains "$context" 'orchestrator=not-running'
assert_contains "$context" 'no HTTP or external network probe'
assert_contains "$context" 'fwi-20260715T010203Z-acde1234 status=running stage=invert iteration=17/50'
[[ "$context" != *'.env.example'* ]] || fail "environment template path was not redacted"
[[ "$context" != *'.env.production'* ]] || fail "environment-specific path was not redacted"
[[ "$context" != *'client.key'* ]] || fail "private-key path was not redacted"
[[ "$context" != *'credentials-prod.json'* ]] || fail "credential path was not redacted"
[[ "$context" != *'ENV_CONTENT_MUST_NEVER_APPEAR'* ]] || fail "environment-file content leaked"
[[ "$context" != *'marker-secret-value'* ]] || fail "environment-file value leaked"
[[ "$context" != *'marker-job-secret'* ]] || fail "FWI status message leaked"
[[ "$context" != *'marker-private-prompt'* ]] || fail "FWI prompt leaked"

marker="$TMP_ROOT/injection-ran"
request='Please preserve this literal text: $(touch '"$marker"') ; --search'
rm -f -- "$CAPTURE" "$marker"
"$FIXTURE/scripts/codex-project.sh" -- "$request"
[[ ! -e "$marker" ]] || fail "initial request was evaluated as shell input"
[[ -f "$CAPTURE" ]] || fail "fake Codex did not capture launch arguments"

mapfile -d '' -t codex_args < "$CAPTURE"
[[ ${#codex_args[@]} -eq 7 ]] || fail "unexpected Codex argument count: ${#codex_args[@]}"
[[ "${codex_args[0]}" == '--cd' ]] || fail "missing fixed --cd option"
[[ "${codex_args[1]}" == "$FIXTURE" ]] || fail "Codex root is not the fixture repository"
[[ "${codex_args[2]}" == '--sandbox' && "${codex_args[3]}" == 'workspace-write' ]] || \
    fail "safe sandbox policy was not applied"
[[ "${codex_args[4]}" == '--ask-for-approval' && "${codex_args[5]}" == 'on-request' ]] || \
    fail "approval policy was not applied"
assert_contains "${codex_args[6]}" "$request"
assert_contains "${codex_args[6]}" '<launcher_initial_request>'

if "$FIXTURE/scripts/codex-project.sh" --dangerously-bypass-approvals-and-sandbox >/dev/null 2>&1; then
    fail "launcher accepted an unsafe Codex option"
fi
if "$FIXTURE/scripts/codex-project.sh" --check -- 'request' >/dev/null 2>&1; then
    fail "--check accepted an initial request"
fi

printf 'codex project launcher tests passed\n'
