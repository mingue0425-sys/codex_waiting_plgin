# Security analysis

## v0.7 provenance and native yield boundary

The v0.7 control plane is `LUNA_ONLY`: real Codex launches explicitly request
`gpt-5.6-luna` with medium reasoning effort, approval review stays with the
user, and subagent/auto-review/fallback paths are rejected. Runtime, thread
and turn model telemetry is required before a candidate command is eligible.
The installed runtime omitted `turn_model`, so the live sample is
`MODEL_ATTESTATION=FAIL` / `EXPERIMENT_VALID=false`; the candidate command did
not start and no security result from that sample is promoted.

The v0.7 path treats App Server `processId` as a logical or opaque handle
unless independent evidence proves an OS PID identity. The required evidence
set links the command item, nonce in runtime output, atomic workspace marker,
self-reported PID and cwd. OS command and start identity are recorded as
separate fields. Missing evidence is UNKNOWN and a mismatch is FAIL.

The candidate command is requested only in a normal Codex thread. The
controller writes the fixture and performs bounded observation; it does not
execute the candidate or start a supervisor. `CLI_RESUME_FALLBACK` remains the
only production path unless sandbox, approval, integrity and lifecycle gates
all pass.

In the installed-runtime run the command item completed with exit code 0, but
the workspace marker and result file were absent. This prevents a provenance
claim and keeps both sandbox and approval parity UNKNOWN. An approval request
was not observed under the selected policy; that is recorded as
`APPROVAL_TEST=NOT_APPLICABLE`, while `APPROVAL_PARITY` remains UNKNOWN.

## v0.6 ownership boundary

The v0.6 harness never executes a candidate command from Snooze. Candidate A
and B actions are requested only through normal Codex thread turns. The
controller may observe App Server events, list experimental background
terminals and perform bounded inspection of a PID already exposed by those
events. It never calls `command/exec`, `process/spawn` or
`thread/shellCommand` for a candidate.

The runtime exposed command items and `processId` values, but it did not expose
the temporary fixture side effects or a background-terminal record. The probe
therefore cannot establish that the observed item is the same process that a
background owner would manage. Normal shell wrapper text is retained as
runtime evidence; hash equality is not treated as semantic approval proof.

The v0.6 gate treats missing command stages, approval requests, sandbox
effects, process identity, survival, completion or continuation as UNKNOWN.
The controller does not interrupt or continue an uncorrelated process. C
remains FAIL because the v0.4 controller-level path allowed a sibling write;
that negative control is never used to promote A or B.

## v0.5 native backend boundary

The v0.4 controller-level `command/exec` sibling-write result is immutable:
the v0.5 rerun remains `SANDBOX_PARITY=FAIL` and `APPROVAL_PARITY=FAIL`.
v0.5 does not repair or promote that backend.

Backend A uses only normal thread turns in the probe. Backend B is evaluated
only when a normal terminal command launches a descendant supervisor. Their
sandbox and approval results are independent fields. In the live temporary
tree the command item and processId were visible, but fixture side effects and
approval requests were not visible to the controller process. Both candidate
parity fields therefore remain **UNKNOWN**. A host subprocess inheritance
result is kept out of Codex evidence.

Approval handlers in the probes are explicit: the deny case returns `decline`,
and the allow case accepts only a matching benign fixture request. There is no
blanket allow, no global config edit, no `approval_policy=never` shortcut and
no sandbox widening. The v0.5 selector returns `CLI_RESUME_FALLBACK` unless
all security, command-integrity, ownership, handoff, survival, model-idle and
continuation gates are PASS.

Command SHA-256 records exact command equality only. It does not prove that a
user approved the semantic action. Completion delivery remains at-least-once
compatible and exactly-once is not claimed. See [V0.5_APPROVAL_INTEGRITY_REPORT.md](V0.5_APPROVAL_INTEGRITY_REPORT.md)
and [V0.5_SECURITY_RECOVERY_REPORT.md](V0.5_SECURITY_RECOVERY_REPORT.md).

The trusted execution record is `spec.json` plus its canonical SHA-256 digest.
The supervisor re-reads and hashes it immediately before launching the child;
metadata must contain the same digest. A changed spec or writable-by-group-or-
other spec file produces a terminal infrastructure error and no command run.

The store root, jobs directory and each job directory are created with mode
700. JSON and log artifacts use mode 600 where the persistence path controls
the mode. Result and state writes use a temp file, file fsync, rename and
directory fsync. Metadata and delivery read-modify-write operations use
per-job advisory locks.

The supervisor inherits the caller's environment for the child so the launcher
does not silently replace shell configuration. Environment values are never
written to the job record. Logs and resume prompts contain output produced by
the command, so callers should avoid placing credentials in command output.

Cancellation never trusts a PID alone. It compares PID, process group and
process start time before signalling. The executable and command fingerprints
remain recorded as evidence, while cancellation allows a shell to `exec` its
final command in place. The child has a new session, which limits cancellation
to the supervisor-owned group in the normal case.

The wrapper cannot prove that a Codex sandbox or approval decision has the same
meaning after a command is detached or resumed. For that reason there is no
automatic PreToolUse rewrite, no dangerous bypass flag, no external daemon that
re-executes the command, and no automatic App Server completion injection.
`codex exec resume` is an explicit operator action and uses neither
`--dangerously-bypass-approvals-and-sandbox` nor a rewritten command. The
completion prompt contains a result path and bounded preview instead of the
full log or credentials.

Fault injection is opt-in through `SNOOZE_FAULT_POINT` and
`SNOOZE_FAULT_TARGET`; it terminates the current test process with `os._exit`.
Those variables should never be enabled in a production environment.

The v0.2 security probes exercised command tampering and environment-value
non-persistence locally, both PASS. Model-backed workspace boundary behavior
did not complete and remains UNKNOWN. Approval meaning, network boundary and
general sandbox preservation were not promoted from assumptions. The optional
hook therefore remains an inert `PASS_THROUGH`; the v0.2 aggregate marks
automatic PreToolUse rewrite/interception FAIL and keeps it disabled.

## v0.4 handoff boundary

The explicit dynamic handoff tool is bound to a Snooze-owned App Server and
passes `workspaceWrite`, `writableRoots` and `networkAccess=false` through the
server's `command/exec` request. The latest boundary fixture nevertheless
wrote both an inside file and a temporary sibling file. No approval request was
observed for the sibling write. The measured result is therefore
`SANDBOX_PARITY=FAIL` and `APPROVAL_PARITY=FAIL`; automatic handoff remains
disabled by `DISABLED_SECURITY_GATE`.

The implementation does not repair this by running a local subprocess in the
production path, by installing a blanket approval handler or by enabling a
dangerous bypass. The local fallback is opt-in for isolated tests only. The
normal comparison did not exercise its fixture in the latest run, so the
owned-side violation is the decisive evidence. Details are in
[V0.4_SECURITY_PARITY_REPORT.md](V0.4_SECURITY_PARITY_REPORT.md).

## v0.3 App Server boundary

`AppServerProcess` owns only the subprocess it starts and treats stdout and
stderr as separate channels. Its default response to an App Server approval or
permission request is an explicit error requiring a caller decision. No
Desktop process is attached, no fd is stolen and no credential is extracted.

The thread registry records `owner=codex-snooze` and an App Server instance id,
so a durable Desktop thread is not silently treated as an owned live thread.
The completion router persists event ids and result hashes before continuation;
`SENT_UNCONFIRMED` requires explicit duplicate permission and exactly-once is
not claimed.

The experimental `process/spawn` backend is feature-gated and was tested only
with a temporary Python marker. The v0.3 differential model probe did not
execute its workspace/sibling fixture in either normal or owned mode, leaving
`SNOOZE_APP_SERVER_SANDBOX_PARITY=UNKNOWN` and
`SNOOZE_APP_SERVER_APPROVAL_PARITY=UNKNOWN`. The native backend is therefore
not the default execution route.
