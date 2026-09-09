# v0.4 continuation and handoff races

Status: **PASS**

Iterations: `100/100`

Continuation turns created: `100`

Duplicate attempts rejected: `100`

The router rejects concurrent CLAIMED/STARTING/SENDING claims. The completion protocol still remains at-least-once and exactly-once is not claimed.
