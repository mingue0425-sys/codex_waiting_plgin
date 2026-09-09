# v0.2 control-plane capability report

Generated: `2026-09-09T07:51:46Z`
Codex: `0.153.4`
Control plane: **APP_SERVER_PARTIAL**

| Feature | Status |
|---|---|
| `app_server_control` | **PASS** |
| `conversation_history_shared` | **PASS** |
| `thread_identifier_shared` | **PASS** |
| `desktop_thread_visible` | **UNKNOWN** |
| `desktop_ui_integration` | **UNKNOWN** |
| `cwd_restored` | **UNKNOWN** |
| `model_config_restored` | **UNKNOWN** |
| `sandbox_policy_restored` | **UNKNOWN** |
| `approval_policy_restored` | **UNKNOWN** |
| `tool_state_restored` | **UNKNOWN** |
| `cli_resume` | **PASS** |
| `turn_interrupt` | **PASS** |
| `job_survives_interrupt` | **PARTIAL** |
| `actual_command_continues_after_interrupt` | **PARTIAL** |
| `exit_code_authoritative_after_interrupt` | **PARTIAL** |
| `turn_handoff_supported` | **PARTIAL** |
| `busy_thread_delivery` | **PARTIAL** |
| `idle_check_to_external_turn_race` | **UNKNOWN** |
| `automatic_completion_delivery` | **UNKNOWN** |
| `delivery_deduplication_and_recovery` | **PASS** |
| `project_change_stale` | **PASS** |
| `actual_command_integrity` | **PASS** |
| `environment_value_persistence` | **PASS** |
| `sandbox_preserved` | **UNKNOWN** |
| `approval_preserved` | **UNKNOWN** |
| `network_boundary` | **UNKNOWN** |
| `auto_pretool_rewrite` | **FAIL** |
| `auto_pretool_interception` | **FAIL** |

## Required integration flags

| Flag | Status |
|---|---|
| `TURN_INTERRUPT_SUPPORTED` | **PASS** |
| `JOB_SURVIVES_INTERRUPT` | **PARTIAL** |
| `BUSY_THREAD_DELIVERY_SUPPORTED` | **PARTIAL** |
| `DESKTOP_UI_INTEGRATION_SUPPORTED` | **UNKNOWN** |
| `SANDBOX_PRESERVED` | **UNKNOWN** |
| `APPROVAL_PRESERVED` | **UNKNOWN** |
| `AUTO_RESUME_SUPPORTED` | **UNKNOWN** |
| `AUTO_HANDOFF_SUPPORTED` | **PARTIAL** |
| `AUTO_PRETOOL_INTERCEPTION_SUPPORTED` | **FAIL** |

## Automatic feature state

- Auto resume: **disabled**
- Auto handoff: **disabled**
- Auto PreToolUse interception: **disabled**

## Decision reasons

- Installed App Server initialize, thread list/read/start and queue/background APIs were exercised on disposable threads.
- turn/interrupt was observed to complete an App Server turn as interrupted.
- Desktop same-thread identity/UI visibility, busy-thread safety, policy restoration and automatic acknowledgement are not proven.
- One earlier handoff survival run passed, while repeat/race runs were UNKNOWN; the survival gate is therefore PARTIAL.
- Automatic PreToolUse rewrite/interception remains FAIL and disabled.

## Evidence sources

- `v01_capabilities`: `/Volumes/game/codex_hook_waiting_plgin/codex-snooze/results/capabilities.json`
- `app_server`: `/Volumes/game/codex_hook_waiting_plgin/codex-snooze/results/v0.2/app-server-probes.json`
- `interrupt`: `/Volumes/game/codex_hook_waiting_plgin/codex-snooze/results/v0.2/interrupt-tests.json`
- `delivery`: `/Volumes/game/codex_hook_waiting_plgin/codex-snooze/results/v0.2/delivery-races.json`
- `concurrency`: `/Volumes/game/codex_hook_waiting_plgin/codex-snooze/results/v0.2/concurrency-tests.json`
- `security`: `/Volumes/game/codex_hook_waiting_plgin/codex-snooze/results/v0.2/security-probes.json`
- `project_stale`: `/Volumes/game/codex_hook_waiting_plgin/codex-snooze/results/v0.2/project-stale-tests.json`
