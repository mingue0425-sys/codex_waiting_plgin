# v0.3 handoff and reconnect races

Generated: `2026-09-09T08:35:10.171852Z`
Overall: **PARTIAL**

| Check | Status |
|---|---|
| `event_claim_persisted` | `PASS` |
| `same_event_routes_once` | `PASS` |
| `duplicate_guard` | `PASS` |
| `ack_state` | `PASS` |
| `owned_thread_and_turn` | `PASS` |
| `partial_jsonl_reader` | `PASS` |
| `crash_detection` | `PASS` |
| `turn_response_loss_is_retryable` | `PASS` |
| `exactly_once` | `UNKNOWN` |

The local router persists an event marker before a continuation and
requires explicit duplicate permission after SENT_UNCONFIRMED. The
protocol-level response-loss and durable-thread cases are recorded
separately from the local marker. Exactly-once is not claimed.
