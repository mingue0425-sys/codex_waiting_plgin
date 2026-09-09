# Optional plugin boundary

The hook boundary is intentionally inert in v0.2. `pre_tool_use` passes the
original tool request through and records why automatic interception is disabled.

Use the explicit CLI instead:

```text
codex-snooze submit --handoff-after 10 --command '...'
```

An automatic rewrite can be enabled only after capability evidence proves the
same Desktop thread, execution state and sandbox/approval meaning.

The current aggregate is `APP_SERVER_PARTIAL`; this is protocol evidence and
does not satisfy the hook gate. See [../V0.2_SECURITY_PROBES.md](../V0.2_SECURITY_PROBES.md).
