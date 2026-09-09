# v0.4 explicit handoff E2E

Generated: `2026-09-09T09:20:02.625919Z`
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
| `final_contains_stale_status` | `True` |

| Run | Status | Thread | Job | First turn |
|---:|---|---|---|---|
| 1 | PASS | `01a08577-02ee-7ad0-8520-d3dbda1a738f` | `40c10443-4a7d-4869-ab36-024ff2cbc011` | `01a08577-0377-7761-a5fb-268a3187d1b3` |

The fixture records a run counter and a result token. A PASS requires
the same durable thread, a single fixture run, a durable completion
event and the exact token in the continuation turn.
