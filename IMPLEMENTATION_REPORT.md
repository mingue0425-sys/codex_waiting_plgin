# Implementation report

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
