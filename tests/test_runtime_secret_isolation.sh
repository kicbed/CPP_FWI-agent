#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
root_launcher="$repo_root/start.sh"
internal_launcher="$repo_root/examples/ai_orchestrator/start_system.sh"
mcp_client="$repo_root/mcp/src/mcp_client.cpp"
llm_client="$repo_root/a2a/include/a2a/examples/llm_client.hpp"

bash -n "$root_launcher" "$internal_launcher"

line_of() {
    local pattern="$1" file="$2"
    grep -nF -m1 -- "$pattern" "$file" | cut -d: -f1
}

internal_call_line="$(line_of '"$INTERNAL_DIR/start_system.sh"' "$root_launcher")"
clear_line="$(grep -n -- '^clear_provider_api_keys$' "$root_launcher" | tail -n1 | cut -d: -f1)"
embedding_line="$(line_of 'nohup "$FWI_WORKER_PYTHON" "$embedding_script"' "$root_launcher")"
web_line="$(line_of 'nohup "$FWI_WORKER_PYTHON" "$PROJECT_ROOT/web/serve.py"' "$root_launcher")"
((internal_call_line < clear_line && clear_line < embedding_line && clear_line < web_line))

grep -q -- '-u DEEPSEEK_API_KEY' "$root_launcher"
grep -q -- 'provider_secret_free_env.*' "$internal_launcher"
grep -q -- 'launch_service registry' "$internal_launcher"
grep -q -- 'specialist_agent_env' "$internal_launcher"
grep -q -- 'AGENT_EMBEDDING_CACHE_DIR' "$internal_launcher"

for key in DEEPSEEK_API_KEY QWEN_API_KEY OPENAI_API_KEY DASHSCOPE_API_KEY; do
    unset_line="$(line_of "unsetenv(\"$key\")" "$mcp_client")"
    exec_line="$(line_of 'execv(server_path_' "$mcp_client")"
    ((unset_line < exec_line))
done

grep -q -- 'CURLOPT_NOPROXY, "\*"' "$llm_client"
grep -q -- 'CURLOPT_FOLLOWLOCATION, 0L' "$llm_client"

printf 'Runtime provider-secret isolation checks passed\n'
