# v0.4 explicit handoff E2E

Generated: `2026-09-09T10:03:49.242731Z`
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

| Run | Status | Thread | Job | First turn |
|---:|---|---|---|---|
| 1 | PASS | `01a0859f-351e-7652-ab60-50bdac5fe3ec` | `1064e07d-d555-45ce-adbe-c6549effafae` | `01a0859f-356f-72e3-92b0-d738549e0f23` |

The fixture records a run counter and a result token. A PASS requires
the same durable thread, a single fixture run, a durable completion
event and the exact token in the continuation turn.
