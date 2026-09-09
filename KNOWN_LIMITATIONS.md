# Known limitations

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
