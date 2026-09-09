# Codex Snooze v0.4 capabilities

Generated: `2026-09-09T10:09:57.061475Z`

```text
CONTROL PLANE = SNOOZE_APP_SERVER_EXPERIMENTAL
SNOOZE-OWNED APP SERVER = PASS
EXPLICIT HANDOFF = PASS
10S HANDOFF = PASS
MODEL IDLE DURING WAIT = PASS
AUTO CONTINUATION = PASS
SANDBOX PARITY = FAIL
APPROVAL PARITY = FAIL
DESKTOP ATTACH = FAIL
AUTO PRETOOL INTERCEPTION = FAIL
PRODUCTION PATH = CLI_RESUME_FALLBACK
```

| Capability | Status |
|---|---|
| `DESKTOP_ATTACH` | **FAIL** |
| `AUTO_PRETOOL_INTERCEPTION` | **FAIL** |
| `SNOOZE_OWNED_APP_SERVER` | **PASS** |
| `EXPLICIT_HANDOFF` | **PASS** |
| `10S_HANDOFF` | **PASS** |
| `JOB_SURVIVES_TURN_END` | **PASS** |
| `MODEL_IDLE_DURING_WAIT` | **PASS** |
| `AUTO_CONTINUATION` | **PASS** |
| `COMPLETION_EVENT` | **PASS** |
| `CONTINUATION_DEDUP` | **PASS** |
| `APP_SERVER_CRASH_RECOVERY` | **PASS** |
| `CONTROLLER_CRASH_RECOVERY` | **PASS** |
| `FAILURE_PROPAGATION` | **PASS** |
| `STALE_PROPAGATION` | **PASS** |
| `THRESHOLD_SEMANTICS` | **PASS** |
| `HANDOFF_REPETITIONS` | **PASS** |
| `ZSH_HANDOFF` | **PASS** |
| `BASH_HANDOFF` | **PASS** |
| `SANDBOX_PARITY` | **FAIL** |
| `APPROVAL_PARITY` | **FAIL** |
| `NATIVE_BACKEND` | **PARTIAL** |
| `TOKEN_TELEMETRY` | **PASS** |
| `TOKEN_SAVINGS` | **UNKNOWN** |

Production automation: **DISABLED_SECURITY_GATE**

- Desktop attach and automatic PreToolUse interception remain fixed FAIL and were not re-researched.
- The model-backed explicit 10-second chain is separated from the sandbox/approval gate.
- The installed command/exec path allowed a temporary sibling write despite workspaceWrite/writableRoots; production automation is disabled.
- Completion delivery is at-least-once and exactly-once is not claimed.
- The native process/spawn backend remains experimental because its installed schema describes host execution without a Codex sandbox.
