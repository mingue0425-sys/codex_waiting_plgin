# Security analysis

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
