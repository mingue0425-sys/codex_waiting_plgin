# Known limitations

v0.7 native yield limitations:

- The installed runtime currently omits the required turn-level model
  telemetry. `LUNA_MODEL_ATTESTATION=FAIL` and `EXPERIMENT_VALID=false`, so no
  candidate fixture, handoff, sandbox, approval, latency or benchmark claim
  is valid from this run. The controller does not substitute another model.
- All v0.7 model-facing paths require `gpt-5.6-luna` and
  `model_reasoning_effort="medium"`; `auto_review`, delegated models and
  automatic fallback are disabled. Historical live artifacts without this
  metadata are not v0.7 evidence.
- The installed runtime is authoritative. Current upstream source is not a
  substitute for a matching installed binary revision.
- Normal-thread output, logical process identity, native yield, turn survival,
  completion correlation and same-thread continuation must all be observed in
  the live artifacts before handoff can be enabled.
- `thread/backgroundTerminals/list` is an experimental observation surface;
  an empty list does not by itself disprove provenance, and it cannot be used
  as the only ownership source.
- Approval absence is recorded separately from approval parity. No blanket
  approval rule is used.

v0.6 native ownership limitations:

- The normal Codex runtime emits command item events and process identifiers,
  but the probe process cannot observe the disposable fixture side effects or
  an experimental background-terminal record. A and B remain UNKNOWN.
- `processId` without matching `itemId`, cwd, canonical command and bounded OS
  process identity is not accepted as ownership correlation.
- The runtime command is exposed as a normal shell wrapper in the observed
  item. That wrapper is recorded but does not prove semantic command integrity.
- No approval request was observed, so approval parity remains UNKNOWN. The
  scoped handler never blanket-approves and never changes global configuration.
- The 30-run p100 handoff sample was not met. No 10-second handoff, survival,
  completion or same-thread continuation claim is made.
- The crash matrix proves local durable CAS boundaries only. Native process
  survival across App Server reconnect or controller downtime remains UNKNOWN.
- The benchmark is blocked until security and lifecycle gates pass; telemetry
  observation does not produce a token-savings claim.

v0.5 native backend limitations:

- `THREAD_NATIVE_TERMINAL` is UNKNOWN. The installed schema exposes normal
  command item events and an experimental background-terminal list, but the
  live long fixture did not produce an observable command item or native
  process handle before the turn was interrupted.
- `SANDBOX_DESCENDANT_SUPERVISOR` is UNKNOWN. The normal descendant fixture
  was not visible in the controller's temporary tree; the host-only detached
  reference is not Codex evidence.
- Native sandbox parity and approval parity are UNKNOWN. Missing local side
  effects and missing approval requests are kept as an execution-namespace
  boundary rather than converted into a security result.
- `CONTROLLER_COMMAND_EXEC` remains FAIL because the v0.4 sibling-write
  regression is reproducible. It is reference-only and cannot be selected.
- The v0.5 ownership ledger proves one durable backend owner per local race,
  but it does not provide exactly-once completion delivery. The protocol lacks
  a client idempotency key.
- Production automation remains disabled and `CLI_RESUME_FALLBACK` remains
  the selected path until a candidate satisfies every security and lifecycle
  gate.
- macOS sleep/wake was not automated in v0.5; no survival claim is made for
  that boundary.

- The current capability evidence does not identify a user's active Desktop
  thread from an external controller.
- `codex exec resume` restored conversation history in the live probe, but cwd,
  model, current turn, tool state, sandbox policy and approval policy were not
  proven to be restored. The explicit resume command therefore stays
  `SENT_UNCONFIRMED` until an operator acknowledges it.
- App Server method availability does not establish that an external process can
  safely inject a completion into the same Desktop turn. No automatic
  `toolOutput`, `turn/steer`, queue or resume path is enabled.
- The live direct `turn/interrupt` probe could not observe its test child PID
  before interrupt; the real Snooze handoff probe did observe a completed child
  result once. A repeat handoff probe completed without invoking the requested
  terminal tool, so repeatability of model-directed E2E execution remains
  UNKNOWN; both outcomes are recorded separately in the generated evidence.
- The supervisor can manage descendants in its process group. A command that
  creates its own session or process group can outlive group cancellation and
  requires explicit operator investigation.
- macOS sleep, host restart, disk-full conditions and abrupt power loss are
  represented by recovery states where detectable, but no process can be
  recovered across a host restart.
- stdout/stderr files are individually ordered. Only `combined.log` provides a
  best-effort cross-stream sequence; simultaneous writes cannot be given a
  kernel-level total order.
- The command is stored in `spec.json` and metadata because it is part of the
  immutable JobSpec. The inherited environment is not persisted; environment
  variable names are the only form suitable for diagnostic hashing.
- The token benchmark cannot access Codex billing or turn-token telemetry from
  this local CLI, so token and model-turn fields are `null` rather than inferred.

v0.4 explicit handoff limitations:

- The owned App Server `command/exec` probe allowed a temporary sibling write
  despite an explicit workspace-only policy. Sandbox and approval parity are
  FAIL, so automatic production handoff is disabled.
- v0.4 continuation is proven only for threads created and owned by Snooze.
  Desktop attach remains FAIL and is not inferred from the owned-thread run.
- Completion routing remains at-least-once-compatible. The installed protocol
  has no client idempotency key, so exactly-once delivery is not claimed.
- The native process/spawn backend preserves exit and logging in the
  differential probe but has no demonstrated Codex sandbox and remains
  experimental only.
- The normal side of the latest security comparison did not exercise its
  fixture. The owned-side boundary violation still independently fails the
  security gate.

v0.2 control-plane limitations:

- The installed App Server accepts several active-turn candidate operations,
  but acceptance does not prove safe completion delivery, message ordering,
  Desktop UI visibility or receiver acknowledgement.
- The live interrupt race fixture produced UNKNOWN whenever the model did not
  invoke the requested terminal command. A single earlier handoff success is
  preserved, but repeatability is PARTIAL rather than PASS.
- The idle-check TOCTOU fixture could not establish a stable shared target and
  remains UNKNOWN. An idle read has no reservation semantics.
- The sandbox differential fixture did not execute its model-directed command;
  sandbox, approval and network boundary results remain UNKNOWN.
- The Desktop-to-CLI and Desktop-to-App-Server identity relationship is not
  established. `APP_SERVER_PARTIAL` describes measured protocol reachability,
  not permission to control the user's Desktop turn.

v0.3 control-plane limitations:

- The Desktop attach probe found a Desktop-owned stdio App Server child and no
  public external reconnect surface. Desktop live attach is FAIL.
- The owned App Server model handoff generated terminal events but no durable
  Snooze result in the temporary store. Ten-second handoff and same-thread
  automatic continuation remain UNKNOWN.
- The experimental native process API passed an explicit Python process, while
  model tool, shell-login and long-job behavior were not promoted from that
  fixture. Sandbox and approval parity remain UNKNOWN.
- Durable thread resume after an owned App Server restart passed, but live tool
  state and background job linkage were not restored or claimed.
- App Server server requests are rejected by default. The controller does not
  infer user approval and does not provide a blanket allow handler.
- The completion router is at-least-once compatible. Exactly-once continuation
  is unavailable without a protocol idempotency key and remains unclaimed.
- The installed App Server exposed token usage notifications, but the workload
  benchmark did not complete its marker in either compared path. Token savings
  remain UNKNOWN.
