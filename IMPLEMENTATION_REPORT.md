# Implementation report

## v0.4 explicit handoff

v0.4 adds an explicit model-selected `codex_snooze_handoff` dynamic tool for
Snooze-owned App Server threads. The handler validates the command argv,
workspace and foreground threshold, then invokes the ordinary nested
`command/exec` path with an explicit workspace-write policy. The JSONL reader
dispatches server request handlers on a background thread so that nested
requests can be read without a protocol deadlock.

The CLI persists a structured `DETACHED` marker in the job store. The
`HandoffController` records the thread/turn/job mapping, interrupts only the
current turn, waits on the supervisor result, and routes a continuation through
the same owned thread. The completion router rejects concurrent claims and
keeps `SENT_UNCONFIRMED` duplicate permission explicit. App Server and
controller crash probes recover the job/checkpoint; exactly-once delivery is
not claimed.

The primary live run passed the ten-second threshold, turn closure, 12-second
job completion, zero model activity during the wait, same-thread continuation,
result-token delivery and one-run guard. Failure exit status, Git stale-state
propagation, 20 repetitions, 100 continuation races, zsh/bash behavior and
both crash cases also passed their stated probes.

The security probe failed independently: the installed `command/exec` path
allowed a temporary sibling write despite the workspace policy and did not
produce an approval request. `SANDBOX_PARITY` and `APPROVAL_PARITY` therefore
remain FAIL, and automatic production use is disabled. Desktop attach and
automatic PreToolUse interception remain unavailable. See
[V0.4_FAILURE_ANALYSIS.md](V0.4_FAILURE_ANALYSIS.md) and
[V0.4_SECURITY_PARITY_REPORT.md](V0.4_SECURITY_PARITY_REPORT.md).

## v0.2 control-plane extension

The v0.2 work adds evidence collection around the existing supervisor without
rewriting its data plane. The installed `codex-cli 0.153.4` App Server was
queried through a reusable redacted JSON-RPC harness, and disposable runtime
threads were used for protocol operations. The aggregate result is
`APP_SERVER_PARTIAL`: `turn/interrupt` works at the protocol level and several
thread/queue/background operations are available, while Desktop identity,
safe busy-thread delivery, policy restoration and repeatable command handoff
remain unproven.

The new probes are [probes/v0_2_environment.py](probes/v0_2_environment.py),
[probes/v0_2_app_server.py](probes/v0_2_app_server.py),
[probes/v0_2_interrupt_probe.py](probes/v0_2_interrupt_probe.py),
[probes/v0_2_concurrency_probe.py](probes/v0_2_concurrency_probe.py),
[probes/v0_2_delivery_races.py](probes/v0_2_delivery_races.py),
[probes/v0_2_security_probe.py](probes/v0_2_security_probe.py),
[probes/v0_2_stale_probe.py](probes/v0_2_stale_probe.py), and
[probes/v0_2_report.py](probes/v0_2_report.py). They write bounded,
machine-readable evidence under `results/v0.2/` and preserve PASS/PARTIAL/
FAIL/UNKNOWN independently for each capability.

The v0.2 delivery race cases pass for the local manual-file outbox and support
at-least-once-compatible recovery with explicit duplicate acknowledgement.
The live interrupt harness had five UNKNOWN model-invocation outcomes; one
earlier E2E survival run is retained, so the command handoff gate is PARTIAL.
The project stale probe passed tracked-file, untracked-file, branch and HEAD
mutations in disposable repositories. Automatic resume, handoff, completion
injection and PreToolUse interception remain disabled.

## v0.3 owned App Server

Track A inspected the live Desktop process tree and public App Server help. The
Desktop-owned child used stdio and exposed no supported external attach or
reconnect endpoint, so `DESKTOP_LIVE_ATTACH=FAIL` and the investigation stops.

Track B adds `AppServerProcess`, `ThreadRegistry`, `AgentController`,
`CompletionRouter` and an experimental `NativeAppServerBackend`. The owned
process performs the installed initialize handshake, buffers partial JSONL,
dispatches responses and notifications, records token usage, rejects approval
server requests by default, and separates App Server crash state from job and
thread state. `agent` is the explicit CLI mode; `submit` remains unchanged.

The live disposable thread completed a simple turn and produced terminal and
token events. A separate restart probe resumed a durable thread after the
original owned App Server exited. The model-backed long-job handoff produced a
terminal event but no durable Snooze result, so `E2E_LONG_JOB_HANDOFF` remains
UNKNOWN. Sandbox and approval parity also remain UNKNOWN. The native process
fixture passed for an explicit Python child, but that does not promote it above
the parity gates.

The v0.3 aggregate is `APP_SERVER_PARTIAL`, with `CLI_RESUME_FALLBACK` selected
for production. No automatic features are enabled and
`AUTO_PRETOOL_INTERCEPTION=FAIL` remains an architectural fact.

Codex Snooze v0.1 implements the explicit supervisor milestone and the
recoverable completion outbox. The design follows the capability probe rather
than assuming that a Desktop thread can be controlled from an external process.

The capability probe was run on macOS Apple Silicon with `codex-cli 0.153.4`.
`codex exec resume` restored a marker in conversation history. The probe did
not establish Desktop UI identity, execution-state restoration, sandbox-policy
restoration or approval-policy restoration, so those values remain UNKNOWN.
App Server `turn/interrupt` returned `interrupted`. An earlier live probe started
the real `codex-snooze submit` launcher from an App Server turn, interrupted that
turn, and then observed the detached supervisor write exit code 0 and a marker.
A repeat probe completed the model turn without invoking the requested terminal
tool, so the latest E2E capability is recorded as UNKNOWN and both runs are
preserved in `results/snooze-e2e-history.json`.

The implementation is split into these parts:

- `snooze_core`: immutable JobSpec hashing, atomic persistence, process identity,
  bounded logging, Git fingerprints, supervisor, cancellation and recovery.
- `snooze_controller`: manual-file delivery, explicit CLI resume fallback,
  acknowledgement, retry and conservative capability routing.
- `probes`: installed CLI/App Server evidence and a local token-telemetry
  benchmark that records unavailable fields as null.
- `tests`: standard-library unit and subprocess integration coverage.

The supervisor starts the selected shell with `stdin=DEVNULL`, pipes stdout and
stderr to concurrent readers, creates a new session/process group, calls
`wait()`, computes the result from the return code, and writes the result before
metadata and delivery. That write order makes the result the recovery source
when a process dies between persistence steps. Metadata and delivery updates
also use per-job `flock` files to keep read-modify-write operations serialized.

The first milestone deliberately stops at explicit opt-in. It does not rewrite
Codex tool calls, intercept PreToolUse, claim that `codex exec resume` restores a
Desktop turn, or send an unverified App Server injection. A later integration
can be added only after the corresponding capability fields become PASS.
For Git working directories the fingerprint also includes an untracked-file
manifest hash, alongside repository root, `HEAD`, branch, status, index diff and
worktree diff hashes.
