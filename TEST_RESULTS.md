# Test results

## v0.6 validation

The v0.6 unit additions cover strict process correlation, normal runtime
wrapper classification, exact command/cwd integrity, unknown-on-missing
evidence and the A/B-only selector. The live A/B probes each completed one
normal-thread run: normal command items were observed, while fixture side
effects, background-terminal records, approval requests and process identity
correlation were absent. The requested 30-run p100 handoff sample was not
claimed.

The v0.6 ownership race suite passes **100/100** with zero dual owners and
zero unexpected errors. The crash matrix records durable CAS boundary passes
and keeps live native reconnect/survival UNKNOWN. C remains the immutable
v0.4 security FAIL reference. See [results/v0.6/](results/v0.6/) and the three
[v0.6 reports](V0.6_NATIVE_OWNERSHIP_REPORT.md).

## v0.5 validation

The current standard-library suite contains **49 tests**. It covers the v0.1
through v0.4 behavior plus exact command hashing, approval/hash separation,
security-gated backend selection and durable single-owner compare-and-swap
transitions. `compileall` also passes.

The v0.5 live artifacts report schema inventory **PASS**, normal terminal
execution evidence **PARTIAL/UNKNOWN by fixture**, native sandbox and approval
parity **UNKNOWN**, descendant inheritance **UNKNOWN**, the controller
`command/exec` reference **FAIL**, and the production selector
`CLI_RESUME_FALLBACK`. The ownership race suite passes **100/100** and does
not claim exactly-once delivery. The benchmark records model and token
telemetry where exposed but makes no token-savings claim.

Artifacts are under [results/v0.5/](results/v0.5/). The seven detailed reports
are [V0.5_NATIVE_TERMINAL_ARCHITECTURE.md](V0.5_NATIVE_TERMINAL_ARCHITECTURE.md),
[V0.5_SECURITY_RECOVERY_REPORT.md](V0.5_SECURITY_RECOVERY_REPORT.md),
[V0.5_SANDBOX_INHERITANCE_REPORT.md](V0.5_SANDBOX_INHERITANCE_REPORT.md),
[V0.5_APPROVAL_INTEGRITY_REPORT.md](V0.5_APPROVAL_INTEGRITY_REPORT.md),
[V0.5_NATIVE_HANDOFF_E2E.md](V0.5_NATIVE_HANDOFF_E2E.md),
[V0.5_BACKEND_COMPARISON.md](V0.5_BACKEND_COMPARISON.md), and
[V0.5_AGENT_BENCHMARK.md](V0.5_AGENT_BENCHMARK.md).

## v0.4 validation

The v0.4 standard-library suite contains **41 tests** and covers the prior
supervisor/control-plane behavior plus structured marker parsing, nested
`command/exec` policy forwarding, approval-request rejection, short-command
transparency, detached nonzero exit propagation and model-activity filtering.

The live evidence passed the primary explicit ten-second handoff, failure and
stale propagation, 20 deterministic repetitions, 100 continuation races,
threshold semantics, zsh/bash shell cases, App Server crash recovery,
controller crash recovery and the directional agent benchmark. The security
parity probe is intentionally recorded as **FAIL**: the owned command path
allowed a sibling write under the supplied workspace policy and automatic
approval behavior was not observed. Native backend differential status is
**PARTIAL** and token savings are **UNKNOWN / not claimed**.

Generated v0.4 evidence is under [results/v0.4/](results/v0.4/). The detailed
reports are [V0.4_EXPLICIT_HANDOFF_REPORT.md](V0.4_EXPLICIT_HANDOFF_REPORT.md),
[V0.4_CONTINUATION_REPORT.md](V0.4_CONTINUATION_REPORT.md),
[V0.4_SECURITY_PARITY_REPORT.md](V0.4_SECURITY_PARITY_REPORT.md), and
[V0.4_AGENT_BENCHMARK.md](V0.4_AGENT_BENCHMARK.md).

The standard-library suite was run with:

```text
python3 -m unittest discover -s tests -v
```

Historical v0.3 checkpoint result: **36 tests passed** (the original 21, 9
v0.2 tests and 6 v0.3 tests). v0.4 added five tests for 41; v0.5 adds eight
tests for the current 49-test suite above.

Coverage includes JobSpec canonical hashing, shell operator handling, bounded
stdout/stderr/combined logs, process identity checks, Git fingerprint changes,
successful and nonzero supervisor exits, stale project detection, handoff
threshold behavior, SIGTERM cancellation, result-write crash recovery,
delivery-write crash recovery, delivery acknowledgement and duplicate guards,
explicit CLI resume state, live-job recovery protection, and capability routing.

The v0.2 tests also cover the JSON-RPC probe client's response/notification
trace, redaction and bounded values, installed-method inventory, conservative
status aggregation, control-plane gating and the transparent hook boundary.

The v0.3 tests cover the owned App Server partial-line reader, lifecycle,
default server-request rejection, response-loss timeout, durable thread
registry, completion-router duplicate guard, crash state and experimental
backend gating.

The live capability command was also run:

```text
python3 probes/capability_probe.py --live
```

It produced `results/capabilities.json` and `results/capabilities.md` with
overall status **PARTIAL**. Static CLI/App Server discovery, CLI history
resume and App Server interrupt passed. One real Snooze turn-interrupt survival
run passed; a repeat turn completed without invoking the requested terminal
tool, so the latest generated E2E capability is UNKNOWN. Desktop identity and
policy restoration remain UNKNOWN by design.

The local benchmark was run with:

```text
python3 probes/token_benchmark.py
```

It measured foreground return and eventual completion wall time for the same
sleep-and-marker workload. Codex token, model-turn and tool-call telemetry was
unavailable, so the generated JSON records those fields as `null`. The latest
timings were baseline `0.432929 s`, Snooze foreground `0.205271 s` and Snooze
completion `0.633875 s`; these are local timings and do not establish token
savings.

Fault tests use abrupt subprocess termination at the result rename and delivery
payload rename boundaries. `recover` reconstructed metadata from the valid
result and moved an in-flight delivery to `SENT_UNCONFIRMED` when the payload
was durable.

The v0.2 deterministic delivery races passed 60/60 cases across 10 repetitions.
The disposable Git stale-state probe passed 20/20 cases across 5 repetitions
for tracked-file, untracked-file, branch and HEAD mutations. The live
interrupt probe attempted all five race points once; all five were UNKNOWN
because the model did not invoke the requested terminal command. The live
security probe retained PASS for command integrity and environment-value
non-persistence, while sandbox/approval/network remained UNKNOWN.

The v0.3 live evidence includes Desktop attach **FAIL**, owned App Server
schema/process fixture **PASS**, durable thread reconnect **PASS**, native
`process/spawn` fixture **PASS**, model-backed handoff **UNKNOWN**, sandbox and
approval parity **UNKNOWN**, and token telemetry **PASS** with no token-savings
claim. The deterministic v0.3 handoff race checks pass locally and retain
exactly-once as UNKNOWN.
