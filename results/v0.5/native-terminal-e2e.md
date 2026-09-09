# v0.5 normal Codex terminal lifecycle

Status: **UNKNOWN**

The fixture was requested as the exact normal terminal command `python3 fixture.py`.
The controller did not call `command/exec`, `process/spawn`, or `thread/shellCommand`.

| Evidence | Status/value |
|---|---|
| `normal_tool_execution_observed` | `False` |
| `command_event_count` | `0` |
| `approval_request_count` | `0` |
| `native_process_handle_observed` | `False` |
| `command_running_at_threshold` | `False` |
| `heartbeat_continued_after_threshold` | `False` |
| `command_completed_event` | `False` |
| `model_events_during_wait` | `0` |
| `command_integrity` | `UNKNOWN` |
| `sandbox_parity` | `UNKNOWN` |
| `approval_parity` | `UNKNOWN` |
| `handoff_10s` | `UNKNOWN` |
| `job_survival` | `UNKNOWN` |
| `completion_detection` | `UNKNOWN` |
| `auto_continuation` | `UNKNOWN` |
| `run_count` | `None` |

A normal command item and a process identifier are insufficient to claim background ownership.
The installed runtime must also prove process survival, sandbox parity, approval parity and completion ownership.
