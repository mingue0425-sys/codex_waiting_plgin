# v0.4 explicit handoff E2E

Generated: `2026-09-09T09:19:16.232762Z`
Status: **PASS**
Runs: `1/1`

All explicit handoff, turn-close, OS-only wait, completion and same-thread continuation conditions passed.

| Condition | All runs |
|---|---|
| `explicit_handoff` | `True` |
| `ten_second_handoff` | `True` |
| `job_survives_turn_end` | `True` |
| `no_model_activity_during_wait` | `True` |
| `same_thread_continuation` | `True` |
| `final_contains_result_token` | `True` |
| `rerun_guard` | `True` |
| `final_contains_failure_status` | `True` |

| Run | Status | Thread | Job | First turn |
|---:|---|---|---|---|
| 1 | PASS | `01a08576-5e52-7c21-9dc1-2d648367ab1b` | `ab270a00-6799-42b5-9830-dcdafcda3e01` | `01a08576-5edf-78a2-adc3-b6cea509bed4` |

The fixture records a run counter and a result token. A PASS requires
the same durable thread, a single fixture run, a durable completion
event and the exact token in the continuation turn.
