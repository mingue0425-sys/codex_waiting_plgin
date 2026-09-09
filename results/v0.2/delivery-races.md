# v0.2 delivery crash and concurrency races

Generated: `2026-09-09T07:46:53Z`
Repetitions: `10`
Aggregate: **PASS**

| Case | Runs | PASS | PARTIAL | UNKNOWN | FAIL |
|---|---:|---:|---:|---:|---:|
| `J1_duplicate_event_and_ack_guard` | 10 | 10 | 0 | 0 | 0 |
| `J2_delivery_controller_crash_after_rename` | 10 | 10 | 0 | 0 | 0 |
| `J2_delivery_controller_crash_before_rename` | 10 | 10 | 0 | 0 | 0 |
| `J3_ack_write_crash_after_rename` | 10 | 10 | 0 | 0 | 0 |
| `J4_controller_restart_explicit_duplicate_retry` | 10 | 10 | 0 | 0 | 0 |
| `J5_concurrent_delivery_claim` | 10 | 10 | 0 | 0 | 0 |

No real Codex thread was used; Codex message and turn counts are
therefore `NOT_ATTEMPTED`. The durable state tests do not establish
exactly-once delivery.
