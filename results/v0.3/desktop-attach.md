# v0.3 Desktop attach probe

Generated: `2026-09-09T08:42:09.773382Z`
Status: **FAIL**

Desktop owns an App Server child, but the observed transport is stdio and no supported external attach/reconnect endpoint is exposed.

The probe used public process/help observation only. It did not inject,
open or duplicate another process's descriptors, read memory or mutate a
thread. Standard descriptor output is reduced to type counts.

Desktop processes observed: `15`
Codex descendants in the Desktop tree: `3`
App Server children in the Desktop tree: `3`

| Public surface | Return code | Attach terms observed |
|---|---:|---|
| `app_server_help` | 0 | socket, stdio, endpoint, connect |
| `app_server_daemon_help` | 0 | none |
| `app_server_version` | 0 | none |

A `FAIL` result closes the Desktop attach investigation for v0.3 and
selects the Snooze-owned App Server path. It does not imply that durable
thread history, a live runtime and a Desktop UI are the same object.
