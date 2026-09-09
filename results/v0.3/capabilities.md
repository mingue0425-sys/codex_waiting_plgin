# Codex Snooze v0.3 capabilities

Generated: `2026-09-09T08:14:12.667250Z`

```text
CONTROL PLANE = APP_SERVER_PARTIAL
DESKTOP ATTACH = FAIL
SNOOZE-OWNED APP SERVER = PARTIAL
10S HANDOFF = UNKNOWN
AUTO CONTINUATION = UNKNOWN
SANDBOX PARITY = UNKNOWN
APPROVAL PARITY = UNKNOWN
AUTO PRETOOL INTERCEPTION = FAIL
```

| Capability | Status | Evidence |
|---|---|---|
| `DESKTOP_LIVE_ATTACH` | **FAIL** | `desktop-attach.json` |
| `EXTERNAL_DESKTOP_ATTACH` | **FAIL** | `desktop-attach.json` |
| `DESKTOP_UI_REFLECTION` | **UNKNOWN** | `No external UI acknowledgement surface.` |
| `DURABLE_THREAD_RESUME` | **PASS** | `crash-reconnect.json` |
| `LIVE_THREAD_CONTINUATION` | **UNKNOWN** | `app-server-e2e.json` |
| `SNOOZE_OWNED_APP_SERVER` | **PARTIAL** | `app-server-e2e.json, security-parity.json, crash-reconnect.json` |
| `APP_SERVER_INITIALIZE` | **PASS** | `schema-probe.json and owned process trace` |
| `NATIVE_PROCESS_SPAWN` | **PASS** | `native-backend.json` |
| `TURN_HANDOFF` | **UNKNOWN** | `app-server-e2e.json` |
| `LONG_JOB_SURVIVAL` | **UNKNOWN** | `native-backend.json and app-server-e2e.json` |
| `COMPLETION_CONTINUATION` | **PARTIAL** | `app-server-e2e.json and handoff-races.json` |
| `BUSY_THREAD_DELIVERY` | **PARTIAL** | `v0.2 concurrency-tests.json` |
| `APP_SERVER_CRASH_DETECTION` | **PASS** | `crash-reconnect.json` |
| `JOB_SURVIVES_APP_SERVER_LOSS` | **PASS** | `crash-reconnect.json` |
| `SANDBOX_PARITY` | **UNKNOWN** | `security-parity.json` |
| `APPROVAL_PARITY` | **UNKNOWN** | `security-parity.json` |
| `TOKEN_TELEMETRY` | **PASS** | `token-benchmark.json/app-server-e2e.json` |
| `TOKEN_SAVINGS` | **UNKNOWN** | `No savings inference.` |
| `AUTO_RESUME` | **UNKNOWN** | `Automatic Desktop resume is disabled.` |
| `AUTO_CONTINUATION` | **UNKNOWN** | `app-server-e2e.json` |
| `AUTO_PRETOOL_INTERCEPTION` | **FAIL** | `The hook remains pass-through by design.` |

Selected integration mode: **`CLI_RESUME_FALLBACK`**

No automatic features are enabled. The owned App Server path is an
explicit experimental controller route until live handoff and parity
evidence are repeatable.

- Durable thread history, live core runtime, Desktop UI and owned App Server are separate objects.
- The model-backed handoff fixture generated terminal events but no durable result, so automatic continuation remains disabled.
- The experimental process/spawn fixture passed for an explicit Python process; sandbox and approval parity remain separate gates.
- Exactly-once is not claimed because the installed protocol exposes no client idempotency key.
