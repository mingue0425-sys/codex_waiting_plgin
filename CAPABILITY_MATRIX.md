# Capability matrix

The authoritative generated evidence is [results/capabilities.json](results/capabilities.json)
and [results/capabilities.md](results/capabilities.md).

| Capability | Result | Production consequence |
|---|---|---|
| Installed CLI and App Server protocol discovery | PASS | Static method names are recorded from the installed binary. |
| `thread/read` | PASS | An installed local thread can be read. |
| `thread/resume` | PASS / UNKNOWN by scenario | A listed completed thread resumed, while a newly started thread without a rollout could not. |
| `codex exec resume` history | PASS | Available only as an explicit user-supplied session fallback. |
| Desktop same-thread/UI state | UNKNOWN | No automatic Desktop resume. |
| Sandbox and approval restoration | UNKNOWN | No wrapper-based automatic handoff. |
| App Server `turn/interrupt` | PASS | The control request and `turn/completed=interrupted` were observed. |
| Busy-thread completion delivery | UNKNOWN | Candidate `toolOutput`, `turn/steer`, `thread/inject_items` and queue methods were inventoried, but no unverified live injection is used. |
| Snooze supervisor survival after interrupted turn | PARTIAL | One live run completed a job after interruption; a repeat model turn did not invoke the requested tool and is recorded as UNKNOWN. |
| Manual completion outbox | PASS | `deliver`, `ack` and explicit duplicate-aware retry are enabled. |
| Automatic completion injection | UNKNOWN | Feature gate keeps it disabled. |

## v0.2 control-plane measurement

The aggregate evidence is [results/v0.2/capabilities.json](results/v0.2/capabilities.json)
and [results/v0.2/capabilities.md](results/v0.2/capabilities.md). The selected
measured mode is **APP_SERVER_PARTIAL**; the production route remains explicit
CLI resume/manual delivery.

| Capability | Result | Production consequence |
|---|---|---|
| App Server disposable-thread control | PASS | Read/start/list/background/queue operations are available for probes. |
| `turn/interrupt` | PASS | Interrupt can be used as an explicit App Server probe. |
| Interrupt handoff survival | PARTIAL | One earlier E2E passed; repeat/race evidence is UNKNOWN. Automatic handoff stays off. |
| Busy-thread delivery | PARTIAL | Candidate APIs accept requests, but safe ordering/ack/UI behavior is UNKNOWN. |
| Idle-check TOCTOU safety | UNKNOWN | Idle state cannot be used as an automatic delivery reservation. |
| Delivery crash recovery | PASS | Local outbox recovery and duplicate-aware retry are enabled. |
| Project stale detection | PASS | tracked/untracked/branch/HEAD changes are marked `COMPLETED_STALE`. |
| Sandbox and approval preservation | UNKNOWN | Automatic wrapper rewrite remains disabled. |
| Desktop identity and UI integration | UNKNOWN | No external Desktop control path is enabled. |
| Automatic PreToolUse interception | FAIL | Hook remains pass-through. |
