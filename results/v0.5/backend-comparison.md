# v0.5 backend comparison

Selected backend: **CLI_RESUME_FALLBACK**
Production path: **CLI_RESUME_FALLBACK**
Automation: **DISABLED_SECURITY_GATE**

| Backend | Status | Sandbox | Approval | Integrity |
|---|---|---|---|---|
| `THREAD_NATIVE_TERMINAL` | **UNKNOWN** | `UNKNOWN` | `UNKNOWN` | `UNKNOWN` |
| `SANDBOX_DESCENDANT_SUPERVISOR` | **UNKNOWN** | `UNKNOWN` | `UNKNOWN` | `UNKNOWN` |
| `CONTROLLER_COMMAND_EXEC` | **FAIL** | `FAIL` | `FAIL` | `PASS` |

No candidate with UNKNOWN or PARTIAL security evidence is automatically selected.

- Backend C remains a regression/reference failure and is never selected.
- A or B requires every execution, security, integrity, handoff and delivery field to be PASS.
- UNKNOWN and PARTIAL evidence select CLI_RESUME_FALLBACK.
