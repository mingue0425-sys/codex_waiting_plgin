# v0.4 explicit handoff E2E

Generated: `2026-09-09T10:09:57.165311Z`
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
| 1 | PASS | `01a085a4-985d-76d3-980f-b331ce07b260` | `836a6f92-202d-4d0e-b286-e108215d4f97` | `01a085a4-98a9-7cd3-9e70-6a7c53e395ad` |

The fixture records a run counter and a result token. A PASS requires
the same durable thread, a single fixture run, a durable completion
event and the exact token in the continuation turn.
