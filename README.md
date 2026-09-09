# Codex Snooze

Codex Snooze keeps an explicitly selected, non-interactive terminal command
under a detached supervisor. The supervisor owns the child process, waits for
the real operating-system exit status, stores bounded logs and a durable result,
and leaves a completion event in an outbox for explicit delivery.

## Current gate

The v0.2 control-plane measurement is `APP_SERVER_PARTIAL`. The installed App
Server responds to several disposable-thread operations, but Desktop identity,
safe busy-thread delivery, policy restoration and repeatable command handoff
are not proven. The production route therefore remains explicit CLI fallback
or manual delivery.

| Capability | Status |
|---|---|
| Desktop same-thread resume | **UNKNOWN** |
| Turn interruption | **PASS** |
| Supervisor/child survival after interruption | **PARTIAL** |
| Exit status integrity | **PASS** |
| Sandbox and approval preservation | **UNKNOWN** |
| Measured control-plane mode | **APP_SERVER_PARTIAL** |
| Automatic Codex delivery | **UNKNOWN / disabled** |
| Manual completion delivery | **PASS** |

The v0.2 aggregate is in [results/v0.2/capabilities.md](results/v0.2/capabilities.md)
and [V0.2_CONTROL_PLANE_REPORT.md](V0.2_CONTROL_PLANE_REPORT.md). The v0.1
measured evidence is in [results/capabilities.md](results/capabilities.md)
and the repeat history is in [results/snooze-e2e-history.json](results/snooze-e2e-history.json).
The selected production route is `CLI_RESUME_FALLBACK` for an explicit
user-supplied CLI session plus `MANUAL_DELIVERY` when the target thread or its
policy cannot be verified. One live E2E run demonstrated supervisor/child survival; a repeat
turn completed without invoking the requested terminal tool, so the generated
latest capability remains `UNKNOWN`. No automatic PreToolUse rewrite or Desktop
resume is enabled.

## v0.3 result

The v0.3 aggregate is [results/v0.3/capabilities.md](results/v0.3/capabilities.md).
The Desktop attach probe is **FAIL**: the running Desktop owns a stdio App
Server child and no supported external reconnect surface was observed. The new
Snooze-owned App Server controller is implemented and its initialize, JSONL,
thread registry, crash/reconnect, native process fixture and duplicate-marker
tests are available, but the model-backed ten-second handoff and sandbox/
approval parity are **UNKNOWN**. The selected production route remains
`CLI_RESUME_FALLBACK`; the owned App Server route is explicit and experimental.

Run an explicit owned thread:

```bash
./scripts/codex-snooze --store "$HOME/.codex-snooze" agent \
  --cwd "$PWD" \
  --task 'Use the terminal tool to run the explicitly named benign fixture and report its result.' \
  --event-trace /tmp/codex-snooze-agent-events.json
```

The agent command uses `on-request` approval by default and rejects unsolicited
server requests. It does not rewrite tool calls or infer which commands are
long-running. See [V0.3_APP_SERVER_ARCHITECTURE.md](V0.3_APP_SERVER_ARCHITECTURE.md),
[V0.3_LIVE_THREAD_REPORT.md](V0.3_LIVE_THREAD_REPORT.md), and
[V0.3_HANDOFF_E2E_REPORT.md](V0.3_HANDOFF_E2E_REPORT.md).

## Quick start

Run a command to completion in the foreground:

```bash
./scripts/codex-snooze --store "$HOME/.codex-snooze" submit -- ./RUN_MACOS_VERIFY.sh
```

The command string after `--` is reconstructed from shell arguments. Use
`--command` when the exact shell source contains operators or quoting:

```bash
./scripts/codex-snooze --store "$HOME/.codex-snooze" submit \
  --shell /bin/zsh \
  --command 'make test && ./verify.sh 2>&1'
```

Return foreground ownership after ten seconds while the supervisor continues:

```bash
./scripts/codex-snooze --store "$HOME/.codex-snooze" submit \
  --handoff-after 10 --command 'sleep 30; ./verify.sh'
```

The threshold is a foreground wait limit. It is not a command timeout. An
explicit `--detach` returns immediately.

Inspect, recover, cancel, and deliver a completed result:

```bash
./scripts/codex-snooze --store "$HOME/.codex-snooze" status JOB_ID
./scripts/codex-snooze --store "$HOME/.codex-snooze" recover JOB_ID
./scripts/codex-snooze --store "$HOME/.codex-snooze" cancel JOB_ID
./scripts/codex-snooze --store "$HOME/.codex-snooze" deliver JOB_ID
./scripts/codex-snooze --store "$HOME/.codex-snooze" ack JOB_ID
```

`deliver` writes `delivery_payload.json` and leaves delivery in
`SENT_UNCONFIRMED`. An operator or another integration can inspect or forward
that payload, then run `ack`. Retrying a `SENT_UNCONFIRMED` event requires
`--allow-duplicate` because the receiver may already have accepted it.

When the user has independently verified a CLI session id, an explicit compact
completion prompt can be sent through `codex exec resume`:

```bash
./scripts/codex-snooze --store "$HOME/.codex-snooze" resume JOB_ID \
  --session-id SESSION_ID
```

This also remains `SENT_UNCONFIRMED`; a successful CLI process is not treated as
a Desktop acknowledgement. The prompt contains result metadata and a bounded
log preview, never the complete log.

## Execution contract

Version 0.1 accepts explicit opt-in jobs intended for a non-interactive command:
stdin is `/dev/null`, no TTY is allocated, and the supported shell is the
selected executable `/bin/zsh` or `/bin/bash` invoked with `-c`. Aliases,
functions, transient interactive state, prompts, editors, REPLs, watchers and
persistent services are outside this contract.

The child receives a separate process group. `cancel` verifies PID, process
group and start time before sending `SIGTERM`, waits for a grace period, and
then uses `SIGKILL` only if that stable identity still matches. The executable
and command fingerprints are retained as evidence; a shell may legitimately
`exec` its final command in the same process. A turn interrupt and job
cancellation are separate actions.

Execution and delivery have independent state machines. A successful command
can therefore have `execution=COMPLETED` while its delivery is `PENDING` or
`SENT_UNCONFIRMED`. Results are written with temp-file, fsync, rename and
directory-fsync steps. `recover` repairs metadata from a valid result, marks
in-flight delivery as retryable or unconfirmed, and never invents an exit code
for a missing process.

For Git working directories the start and end fingerprints include repository
root, `HEAD`, branch, status hash, untracked-file manifest hash, index diff hash
and worktree diff hash. A successful command with a changed fingerprint is
`COMPLETED_STALE`.

## Job files

Each job is stored below `jobs/JOB_ID/`:

```text
spec.json
metadata.json
delivery.json
stdout.log
stderr.log
combined.log
result.json
delivery_payload.json       # after explicit delivery
delivery_attempt.json      # CLI resume attempt evidence
supervisor.log
supervisor.lock
.metadata.lock
.delivery.lock
```

Log files and previews are bounded by `max_log_bytes`, `max_preview_bytes`,
`max_preview_lines` and `max_single_line_bytes`. Separate stdout/stderr files
do not preserve cross-stream ordering; `combined.log` contains sequence numbers
and monotonic timestamps.

## Development

The implementation uses Python's standard library and has no runtime package
dependencies.

```bash
python3 -m unittest discover -s tests -v
python3 probes/capability_probe.py --live
python3 probes/token_benchmark.py
python3 probes/v0_2_report.py
python3 probes/v0_3_report.py
```

See [IMPLEMENTATION_REPORT.md](IMPLEMENTATION_REPORT.md),
[V0.2_CONTROL_PLANE_REPORT.md](V0.2_CONTROL_PLANE_REPORT.md),
[KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md), and
[SECURITY_ANALYSIS.md](SECURITY_ANALYSIS.md) for the measured boundaries.
