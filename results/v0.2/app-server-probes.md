# v0.2 App Server probes

Generated: `2026-09-09T07:57:20Z`
Codex: `codex-cli 0.153.4`
Live model calls requested: `True`

The runtime probe used only disposable threads for mutations. A listed
user thread was inspected at most with read-only `thread/read`.

| Method or field | Exists | Experimental | Runtime | Request | Response |
|---|---|---|---|---|---|
| `thread/start` | True | True | PASS | `ThreadStartParams` | `ThreadStartResponse` |
| `thread/read` | True | False | PASS | `ThreadReadParams` | `ThreadReadResponse` |
| `thread/list` | True | False | PASS | `ThreadListParams` | `ThreadListResponse` |
| `thread/resume` | True | True | PASS | `ThreadResumeParams` | `ThreadResumeResponse` |
| `thread/status (notification)` | True | False | not_attempted | `notification` | `ThreadStatusChangedNotification` |
| `turn/start` | True | True | PASS | `TurnStartParams` | `TurnStartResponse` |
| `turn/interrupt` | True | False | not_attempted | `TurnInterruptParams` | `TurnInterruptResponse` |
| `turn/steer` | True | False | not_attempted | `TurnSteerParams` | `TurnSteerResponse` |
| `turn/start.toolOutput` | True | True | not_attempted | `TurnStartParams` | `TurnStartResponse` |
| `thread/inject_items` | True | False | not_attempted | `ThreadInjectItemsParams` | `ThreadInjectItemsResponse` |
| `thread/backgroundTerminals/list` | True | True | PASS | `ThreadBackgroundTerminalsListParams` | `ThreadBackgroundTerminalsListResponse` |
| `thread/queue/list` | True | True | PASS | `ThreadQueueListParams` | `ThreadQueueListResponse` |
| `thread/queue/add` | True | True | not_attempted | `ThreadQueueAddParams` | `ThreadQueueAddResponse` |

## Runtime operations

- `initialize` target=`` error=`None` status=``
- `thread/list` target=`` error=`None` status=``
- `thread/read` target=`listed_non_active_thread` error=`None` status=``
- `thread/start` target=`` error=`None` status=``
- `thread/read` target=`disposable_thread` error=`None` status=``
- `thread/backgroundTerminals/list` target=`` error=`None` status=``
- `thread/queue/list` target=`` error=`None` status=``
- `thread/resume` target=`disposable_thread_before_rollout` error=`{"code":-32600,"message":"no rollout found for thread id 01a0852b-c076-7c71-a56a-36638aad1729"}` status=``
- `turn/start` target=`disposable_thread` error=`None` status=``
- `turn/completed` target=`` error=`None` status=`completed`
- `thread/resume` target=`disposable_thread_after_rollout` error=`None` status=``

## Capability summary

- `app_server_initialize`: **PASS**
- `thread_list`: **PASS**
- `thread_start_read_background_queue_list`: **PASS**
- `thread_resume_before_rollout`: **PASS**
- `turn_start_interrupt_steer`: **UNKNOWN**
- `busy_thread_delivery`: **UNKNOWN**

Full request/response/notification trace and schema summaries are in
`app-server-probes.json`. Active-turn interrupt, steer and delivery
experiments are recorded by their dedicated v0.2 result files.
