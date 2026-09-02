# kind=user resume, identity stamp, HITL floors already proven

- Date: 2026-09-03
- Keywords: crew, kind=user, resume, identity, add_message, HITL, approvals, recover_stranded
- Main idea: Engine Crew already proves queued kind=user resume, meta.space/role/name stamp, and HITL floors. No crew code this pass. Live :8020 still the fork.

## Proofs (83 passed)

- `tests/test_crew/test_queue_wire.py` `test_queued_user_turn_resumes_after_restart`
- `tests/test_crew/test_store.py` identity stamp on add_message
- `tests/test_crew/test_config_policy.py` + `test_approvals.py` HITL cannot-loosen; `test_runtime.py` mutating confirm + takeover; `test_detect.py` skip verify without criteria

## COPY / DISTILL / SKIP / ALREADY

| Verdict | Path |
|---|---|
| ALREADY | `E:\Cortex\CortexOS\crew\queue.py` `wakes.py` `stall.py` mailbox cursors |
| ALREADY | identity stamp in `store.add_message`; HITL in `policy.py` `approvals.py` |
| DISTILL | gastown checkpoint.go molecule (pattern only; no root LICENSE) |
| SKIP | Beads, LangGraph, Guaca, analog COPY, Control POST wakes, grow `E:\Cortex-crew` |

## Still true

Control stays plane 4. Do not start or kill :8020.
