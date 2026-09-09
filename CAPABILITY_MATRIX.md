# Capability matrix

The authoritative generated evidence is [results/capabilities.json](results/capabilities.json)
and [results/capabilities.md](results/capabilities.md).

## v0.6 native ownership proof

The authoritative aggregate is [results/v0.6/capabilities.json](results/v0.6/capabilities.json)
and [results/v0.6/capabilities.md](results/v0.6/capabilities.md).

| Capability | Result | Production consequence |
|---|---|---|
| `THREAD_NATIVE_TERMINAL` | UNKNOWN | Normal command item observed, but no correlated native background record or fixture identity. |
| `SANDBOX_DESCENDANT_SUPERVISOR` | UNKNOWN | Normal descendant command item observed, but no Codex-visible descendant or sandbox inheritance evidence. |
| `CONTROLLER_COMMAND_EXEC` | FAIL | Immutable v0.4 sibling-write negative control; never selected. |
| Native sandbox parity | UNKNOWN | Fixture side effects were not visible for either candidate. |
| Native approval parity | UNKNOWN | No approval request or decision was observed. |
| Command integrity | UNKNOWN | Normal runtime wrapper was observed; exact semantic command/process evidence was incomplete. |
| Process identity | UNKNOWN | `osPid`/background record correlation was unavailable. |
| 10-second handoff | UNKNOWN | No ownership transfer or 30-run p100 sample. |
| Job survival/completion | UNKNOWN | Native background lifecycle was not established. |
| Ownership races | PASS | 100/100 local durable single-owner CAS races; exactly-once remains unclaimed. |
| Crash matrix | UNKNOWN | Durable CAS boundary passes; live native reconnect/survival is unproven. |
| Safe automatic handoff | NOT_SUPPORTED | Every candidate gate must be PASS before selection. |
| Production path | CLI_RESUME_FALLBACK | Native automation remains disabled. |
| Desktop attach | FAIL | Unchanged. |
| Automatic PreToolUse interception | FAIL | Unchanged. |

## v0.5 security-gated native backends

The authoritative aggregate is [results/v0.5/capabilities.json](results/v0.5/capabilities.json)
and [results/v0.5/capabilities.md](results/v0.5/capabilities.md).

| Capability | Result | Production consequence |
|---|---|---|
| Installed normal-terminal schema inventory | PASS | Protocol names and schema descriptions are recorded; availability is not ownership proof. |
| `THREAD_NATIVE_TERMINAL` | UNKNOWN | Normal command item was not tied to an observable native background handle; disabled. |
| `SANDBOX_DESCENDANT_SUPERVISOR` | UNKNOWN | Normal descendant fixture was not locally observable; host-only evidence is excluded. |
| `CONTROLLER_COMMAND_EXEC` | FAIL | v0.4 sibling-write regression reference only; never selected. |
| Native sandbox parity | UNKNOWN | Separate from approval and blocked by missing fixture evidence. |
| Native approval parity | UNKNOWN | Explicit deny/allow handler was scoped; no live request was observed. |
| Command integrity | UNKNOWN | Wrapper command text was observed, but exact process identity was not established. |
| Ownership race ledger | PASS | 100/100 single-owner CAS transfers; exactly-once delivery remains unclaimed. |
| Safe automatic handoff | NOT_SUPPORTED | All required native security and lifecycle gates are not PASS. |
| Production path | CLI_RESUME_FALLBACK | Native automation remains disabled. |
| Desktop attach | FAIL | No external Desktop attach path is enabled. |
| Automatic PreToolUse interception | FAIL | Hook remains transparent. |

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

## v0.3 owned control plane

The authoritative aggregate is [results/v0.3/capabilities.json](results/v0.3/capabilities.json)
and [results/v0.3/capabilities.md](results/v0.3/capabilities.md).

| Capability | Result | Production consequence |
|---|---|---|
| Desktop live attach | FAIL | Desktop attach research stops; no external injection is enabled. |
| Snooze-owned App Server lifecycle | PARTIAL | Explicit `agent` mode is available, but handoff safety gates remain closed. |
| Durable thread resume after owned-server restart | PASS | Conversation resume works in the disposable live probe; live tool state remains separate. |
| Experimental native `process/spawn` fixture | PASS | Backend is opt-in only until parity is proven. |
| Ten-second model handoff | UNKNOWN | Automatic completion continuation remains disabled. |
| Completion router duplicate guard | PASS | Durable at-least-once marker and explicit ACK/retry are enabled. |
| Sandbox parity | UNKNOWN | No automatic execution or wrapper rewrite. |
| Approval parity | UNKNOWN | No blanket allow behavior. |
| Token telemetry | PASS | Protocol usage events are recorded when exposed; no savings claim. |
| PreToolUse interception | FAIL | Hook remains transparent and disabled. |

## v0.4 explicit handoff

The authoritative v0.4 aggregate is [results/v0.4/capabilities.json](results/v0.4/capabilities.json)
and [results/v0.4/capabilities.md](results/v0.4/capabilities.md).

| Capability | Result | Production consequence |
|---|---|---|
| Snooze-owned App Server handoff | PASS | Explicit experimental path only. |
| Ten-second foreground handoff | PASS | The long job survives turn closure in the owned thread. |
| Model idle during wait | PASS | No model events were observed during the primary job interval. |
| Same-thread automatic continuation | PASS | Continuation uses the durable Snooze-owned registry mapping. |
| Failure and stale propagation | PASS | Exit 7 and `COMPLETED_STALE` reach the continuation. |
| App Server/controller crash recovery | PASS | Job and checkpoint survive; exactly-once remains unclaimed. |
| Sandbox parity | FAIL | Automatic production handoff is disabled. |
| Approval parity | FAIL | Automatic production handoff is disabled. |
| Desktop attach | FAIL | v0.4 does not control Desktop-owned threads. |
| Automatic PreToolUse interception | FAIL | The hook stays transparent. |
| Native backend | PARTIAL | Exit/logging pass; host sandbox parity is unproven. |
| Token savings | UNKNOWN | No savings claim is made. |

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
