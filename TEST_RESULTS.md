# Test results

The standard-library suite was run with:

```text
python3 -m unittest discover -s tests -v
```

Result: **29 tests passed** (the original 21 plus 8 v0.2 tests).

Coverage includes JobSpec canonical hashing, shell operator handling, bounded
stdout/stderr/combined logs, process identity checks, Git fingerprint changes,
successful and nonzero supervisor exits, stale project detection, handoff
threshold behavior, SIGTERM cancellation, result-write crash recovery,
delivery-write crash recovery, delivery acknowledgement and duplicate guards,
explicit CLI resume state, live-job recovery protection, and capability routing.

The v0.2 tests also cover the JSON-RPC probe client's response/notification
trace, redaction and bounded values, installed-method inventory, conservative
status aggregation, control-plane gating and the transparent hook boundary.

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
