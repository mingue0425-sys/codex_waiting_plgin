# v0.2 security probes

Generated: `2026-09-09T07:51:37Z`
Live model probe: `True`

| Capability | Status |
|---|---|
| `actual_command_integrity` | **PASS** |
| `environment_value_persistence` | **PASS** |
| `sandbox_preserved` | **UNKNOWN** |
| `approval_preserved` | **UNKNOWN** |
| `network_boundary` | **UNKNOWN** |
| `auto_pretool_rewrite` | **FAIL** |
| `auto_pretool_interception` | **FAIL** |

## Cases

| Case | Status |
|---|---|
| `jobspec_command_integrity` | **PASS** |
| `environment_values_not_persisted` | **PASS** |
| `workspace_write_boundary` | **UNKNOWN** |

automatic wrapper rewrite remains disabled; approval meaning and general sandbox preservation are not proven

The live fixture attempts workspace write/read and sibling read/write using only temporary paths.
Approval was not exercised with a dangerous command; it remains UNKNOWN.
