# Codex environment (v0.2)

Generated: `2026-09-09T07:24:20Z`
Codex: `0.153.4`
Binary: `/opt/homebrew/bin/codex`
Platform: `Darwin arm64`
Status: **PASS**

The installed binary is authoritative for this probe. Help output is
stored verbatim in the JSON so protocol names can be audited later.

| Probe | Return code | Duration |
|---|---:|---:|
| `codex_version` | 0 | 0.312486 s |
| `codex_help` | 0 | 0.046253 s |
| `codex_exec_help` | 0 | 0.045275 s |
| `codex_exec_resume_help` | 0 | 0.045867 s |
| `codex_resume_help` | 0 | 0.045617 s |
| `codex_app_server_help` | 0 | 0.046202 s |
| `codex_app_server_schema_help` | 0 | 0.04992 s |
| `codex_app_server_ts_help` | 0 | 0.0466 s |
| `codex_debug_help` | 0 | 0.04504 s |
| `codex_debug_app_server_help` | 0 | 0.045125 s |
| `codex_queue_help` | 0 | 0.045931 s |
| `codex_agents_help` | 0 | 0.04544 s |
| `codex_remote_control_help` | 0 | 0.045863 s |
| `codex_features_help` | 0 | 0.046583 s |
| `codex_sandbox_help` | 0 | 0.046986 s |
| `codex_exec_server_help` | 0 | 0.044808 s |

## Parsed commands and options

### `codex_help`

- commands: `agents, app, app-server, apply, archive, cloud, completion, debug, delete, doctor, exec, exec-server, features, fork, help, login, logout, mcp, mcp-server, migrate-rollouts, plugin, queue, remote-control, resume, review, sandbox, unarchive, update`
- options: `--add-dir, --approve-for-me, --dangerously-bypass-approvals-and-sandbox, --dangerously-bypass-hook-trust, --disable, --enable, --local-provider, --no-alt-screen, --oss, --remote, --remote-auth-token-env, --search, --strict-config, -C, -V, -a, -c, -h, -i, -m, -p, -s`

### `codex_exec_help`

- commands: `fork, help, resume, review`
- options: `--add-dir, --approve-for-me, --color, --dangerously-bypass-approvals-and-sandbox, --dangerously-bypass-hook-trust, --disable, --enable, --ephemeral, --ignore-rules, --ignore-user-config, --json, --local-provider, --oss, --output-schema, --skip-git-repo-check, --strict-config, --thread-source, -C, -V, -c, -h, -i, -m, -o, -p, -s`

### `codex_exec_resume_help`

- commands: `none`
- options: `--all, --dangerously-bypass-approvals-and-sandbox, --dangerously-bypass-hook-trust, --disable, --enable, --ephemeral, --ignore-rules, --ignore-user-config, --json, --last, --output-schema, --skip-git-repo-check, --strict-config, --thread-source, -c, -h, -i, -m, -o`

### `codex_resume_help`

- commands: `none`
- options: `--add-dir, --all, --approve-for-me, --dangerously-bypass-approvals-and-sandbox, --dangerously-bypass-hook-trust, --disable, --enable, --include-non-interactive, --last, --local-provider, --no-alt-screen, --oss, --remote, --remote-auth-token-env, --search, --strict-config, -C, -V, -a, -c, -h, -i, -m, -p, -s`

### `codex_app_server_help`

- commands: `daemon, generate-json-schema, generate-ts, help, proxy`
- options: `--analytics-default-enabled, --code-mode-host, --disable, --enable, --listen, --stdio, --strict-config, --ws-audience, --ws-auth, --ws-issuer, --ws-max-clock-skew-seconds, --ws-shared-secret-file, --ws-token-file, --ws-token-sha256, -c, -h`

### `codex_app_server_schema_help`

- commands: `none`
- options: `--disable, --enable, --experimental, -c, -h, -o`

### `codex_app_server_ts_help`

- commands: `none`
- options: `--disable, --enable, --experimental, -c, -h, -o, -p`

### `codex_debug_help`

- commands: `app-server, help, models, prompt-input`
- options: `--disable, --enable, -c, -h`

### `codex_debug_app_server_help`

- commands: `help, send-message-v2`
- options: `--disable, --enable, -c, -h`

### `codex_queue_help`

- commands: `none`
- options: `--add-dir, --approve-for-me, --dangerously-bypass-approvals-and-sandbox, --dangerously-bypass-hook-trust, --disable, --enable, --local-provider, --message, --oss, --remote, --remote-auth-token-env, --strict-config, --thread, -C, -c, -h, -i, -m, -p, -s`

### `codex_agents_help`

- commands: `none`
- options: `--disable, --enable, --no-alt-screen, --remote, --remote-auth-token-env, -C, -c, -h`

### `codex_remote_control_help`

- commands: `help, pair, start, stop`
- options: `--disable, --enable, --json, -c, -h`

### `codex_features_help`

- commands: `disable, enable, help, list`
- options: `--disable, --enable, -c, -h`

### `codex_sandbox_help`

- commands: `none`
- options: `--allow-unix-socket, --disable, --enable, --include-managed-config, --log-denials, --sandbox-state-disable-network, --sandbox-state-json, --sandbox-state-readable-root, -C, -P, -c, -h, -p`

### `codex_exec_server_help`

- commands: `forward, help`
- options: `--concurrent-requests, --disable, --enable, --environment-id, --exit-on-stdin-close, --listen, --name, --remote, --strict-config, --use-agent-identity-auth, -c, -h`
