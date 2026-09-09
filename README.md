# Codex Snooze

## v0.7 native yield and provenance proof

v0.7 separates App Server logical process/session handles from OS PIDs and
requires independent nonce, marker, self-reported PID and cwd evidence before
an ownership transition is eligible. The installed Codex runtime is
authoritative; current upstream source is recorded only as a comparison.

All v0.7 live probes are Luna-only: `gpt-5.6-luna` with
`model_reasoning_effort="medium"`. The App Server and direct CLI launchers pin
those values explicitly. A no-tool attestation turn must report the requested,
runtime, thread and turn model before a candidate command can start. The
installed runtime reported the Luna runtime and thread values but omitted the
turn model field, so `LUNA_MODEL_ATTESTATION=FAIL` and
`EXPERIMENT_VALID=false`; no candidate fixture command was started. See
[V0.7_MODEL_POLICY_REPORT.md](V0.7_MODEL_POLICY_REPORT.md) and
[results/v0.7/model-attestation.json](results/v0.7/model-attestation.json).

The v0.7 artifacts are [results/v0.7/capabilities.md](results/v0.7/capabilities.md),
[V0.7_RUNTIME_SOURCE_TRACE.md](V0.7_RUNTIME_SOURCE_TRACE.md),
[V0.7_PROCESS_PROVENANCE_REPORT.md](V0.7_PROCESS_PROVENANCE_REPORT.md), and
[V0.7_NATIVE_YIELD_REPORT.md](V0.7_NATIVE_YIELD_REPORT.md).

The normal-thread candidate is selected only when provenance, native yield,
turn survival, correlated completion/exit code, zero model wait-family calls,
and same-thread continuation all pass. The v0.4 controller command/exec,
Desktop attach, and automatic pretool interception paths remain failed and
are not production candidates.

The installed-runtime live result is:

```text
PROCESS_ID_KIND = UNKNOWN
NATIVE_EXECUTION_PROVENANCE = UNKNOWN
NATIVE_YIELD = UNKNOWN
BACKGROUND_TERMINAL_REGISTRY = UNKNOWN
THREAD_NATIVE_TERMINAL = UNKNOWN
LUNA_AVAILABLE = UNKNOWN
LUNA_MODEL_ATTESTATION = FAIL
LUNA_ONLY_ENFORCEMENT = PASS
NON_LUNA_MODEL_CALLS = 0
DESCENDANT_SUPERVISOR = NOT_RUN
NATIVE_SANDBOX_PARITY = UNKNOWN
NATIVE_APPROVAL_PARITY = UNKNOWN
SAFE_AUTOMATIC_HANDOFF = NOT_SUPPORTED
PRODUCTION_PATH = CLI_RESUME_FALLBACK
```

Because the Luna attestation failed before the candidate turn, live native
gates are treated as invalid/unknown in the v0.7 aggregate; they are not
handoff, sandbox, approval, latency or benchmark evidence. The controller
never falls back to another model, enables auto-review, or delegates to a
subagent.

The current attestation gate prevented both candidate fixtures from starting.
Earlier pre-policy observations are retained only as historical context and
are excluded from v0.7 evidence; they cannot establish namespace, provenance,
native yield or sandbox parity under the Luna-only policy.

## v0.6 native ownership proof

v0.6 independently tests whether a normal Codex thread execution can transfer
background ownership to Snooze without command re-execution. The live probes
observed normal `commandExecution` items and `processId` values for both
candidate prompts, but the temporary fixture side effects were absent and
`thread/backgroundTerminals/list` remained empty. Process correlation,
sandbox parity, approval parity, process survival and continuation therefore
remain UNKNOWN.

```text
THREAD_NATIVE_TERMINAL = UNKNOWN
DESCENDANT_SUPERVISOR = UNKNOWN
CONTROLLER_COMMAND_EXEC = FAIL

NATIVE_SANDBOX_PARITY = UNKNOWN
NATIVE_APPROVAL_PARITY = UNKNOWN
COMMAND_INTEGRITY = UNKNOWN
PROCESS_IDENTITY = UNKNOWN

10S_HANDOFF = UNKNOWN
JOB_SURVIVAL = UNKNOWN
MODEL_IDLE_DURING_WAIT = UNKNOWN
COMPLETION_DETECTION = UNKNOWN
AUTO_CONTINUATION = UNKNOWN

SAFE_AUTOMATIC_HANDOFF = NOT_SUPPORTED
PRODUCTION_PATH = CLI_RESUME_FALLBACK
FALLBACK_PATH = CLI_RESUME_FALLBACK
```

The v0.6 controller never calls `command/exec`, `process/spawn` or
`thread/shellCommand` for A/B. C remains a negative control only. Evidence is
in [results/v0.6/capabilities.md](results/v0.6/capabilities.md), with details
in [V0.6_NATIVE_OWNERSHIP_REPORT.md](V0.6_NATIVE_OWNERSHIP_REPORT.md),
[V0.6_SECURITY_PARITY_REPORT.md](V0.6_SECURITY_PARITY_REPORT.md), and
[V0.6_HANDOFF_E2E_REPORT.md](V0.6_HANDOFF_E2E_REPORT.md).

Codex Snooze keeps an explicitly selected, non-interactive terminal command
under a detached supervisor. The supervisor owns the child process, waits for
the real operating-system exit status, stores bounded logs and a durable result,
and leaves a completion event in an outbox for explicit delivery.

## v0.5 current gate

v0.5 keeps execution backends separate. The controller does not execute the
candidate native command; it only records protocol events, waits for measured
completion and selects a backend from evidence.

```text
Stable supervisor = PASS
Snooze-owned App Server = PASS
Controller command/exec backend = FAIL (reference only)
Thread-native terminal backend = UNKNOWN
Descendant-supervisor backend = UNKNOWN

Explicit handoff = UNKNOWN for native path
10s handoff = UNKNOWN
Model idle during wait = UNKNOWN
Auto continuation = UNKNOWN

Sandbox parity = UNKNOWN
Approval parity = UNKNOWN
Command integrity = UNKNOWN

Production backend = CLI_RESUME_FALLBACK
Fallback backend = CLI_RESUME_FALLBACK
Desktop attach = FAIL
Automatic PreToolUse = FAIL
```

The installed `codex-cli 0.153.4` schema contains normal
`commandExecution` lifecycle events and experimental background-terminal
methods, but the live disposable fixture did not establish a shared local
process/filesystem identity or approval request. The v0.4 controller-level
`command/exec` sibling-write failure remains an immutable regression fact.
The complete v0.5 evidence is in [results/v0.5/capabilities.md](results/v0.5/capabilities.md),
with architecture, security, inheritance, approval, E2E, comparison and
benchmark reports in the `V0.5_*.md` files.

## v0.4 current gate

The v0.4 explicit handoff chain passes for a thread created and owned by
Snooze:

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

The owned App Server probe supplied a workspace-only `command/exec` policy but
the installed path still allowed a temporary sibling write. Automatic
production handoff is therefore disabled by the security gate. The generated
aggregate is [results/v0.4/capabilities.md](results/v0.4/capabilities.md), with
the architecture and evidence in [V0.4_HANDOFF_ARCHITECTURE.md](V0.4_HANDOFF_ARCHITECTURE.md),
[V0.4_EXPLICIT_HANDOFF_REPORT.md](V0.4_EXPLICIT_HANDOFF_REPORT.md),
[V0.4_CONTINUATION_REPORT.md](V0.4_CONTINUATION_REPORT.md), and
[V0.4_SECURITY_PARITY_REPORT.md](V0.4_SECURITY_PARITY_REPORT.md).

## Historical v0.2 gate

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
