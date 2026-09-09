# v0.2 interrupt and handoff races

Generated: `2026-09-09T07:38:07Z`
Live model probe: `True`
Repetitions per race point: `1`
Aggregate: **UNKNOWN**

| Race point | Runs | PASS | PARTIAL | UNKNOWN |
|---|---:|---:|---:|---:|
| `child_start` | 1 | 0 | 0 | 1 |
| `threshold_before` | 1 | 0 | 0 | 1 |
| `threshold_after` | 1 | 0 | 0 | 1 |
| `command_end` | 1 | 0 | 0 | 1 |
| `simultaneous` | 1 | 0 | 0 | 1 |

## Invariants

- `turn_interrupted`: PASS only when turn/completed.status=interrupted
- `supervisor_survival`: PASS only when durable result with completion event exists
- `actual_command_continues`: PASS only when deterministic marker reaches CHILD_COMPLETE
- `exit_code_authoritative`: PASS only when result.exit_code=0

Full request/response/notification traces are embedded in `interrupt-tests.json`.
