# v0.2 busy-thread and TOCTOU probes

Generated: `2026-09-09T07:35:49Z`
Live model probe: `True`
Repetitions: `1`

| Capability | Status |
|---|---|
| `busy_thread_delivery` | **PARTIAL** |
| `idle_check_to_external_turn_race` | **UNKNOWN** |
| `automatic_completion_delivery` | **UNKNOWN** |

## Mechanisms

| Mechanism | PASS | PARTIAL | UNKNOWN |
|---|---:|---:|---:|
| `turn/start.toolOutput` | 1 | 0 | 0 |
| `turn/steer` | 1 | 0 | 0 |
| `thread/inject_items` | 1 | 0 | 0 |
| `thread/queue/add` | 1 | 0 | 0 |
| `thread/resume+turn/start` | 0 | 1 | 0 |
| `codex-exec-resume` | 0 | 0 | 1 |

## TOCTOU

- run 1: **UNKNOWN** — idle state or user active turn could not be established

A successful request is recorded as API acceptance only. It does
not establish safe completion delivery, exactly-once behavior, or
Desktop UI visibility.
