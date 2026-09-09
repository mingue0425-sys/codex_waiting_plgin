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
