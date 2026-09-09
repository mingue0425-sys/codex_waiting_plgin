# v0.5 sandbox and approval parity

Status: **UNKNOWN**

Backend: `THREAD_NATIVE_TERMINAL`
Sandbox parity: **UNKNOWN**
Approval parity: **UNKNOWN**
Fixture-exercised cases: `0`

| Case | Tool observed | Fixture exercised | Sandbox | Approval |
|---|---:|---:|---|---|
| `inside-read/decline` | True | False | `UNKNOWN` | `UNKNOWN` |
| `inside-write/decline` | True | False | `UNKNOWN` | `UNKNOWN` |
| `outside-read/decline` | True | False | `UNKNOWN` | `UNKNOWN` |
| `outside-write/decline` | True | False | `UNKNOWN` | `UNKNOWN` |
| `outside-write/allow` | True | False | `UNKNOWN` | `UNKNOWN` |

The controller never sent `command/exec`; the v0.4 controller path is retained as a separate FAIL reference.
