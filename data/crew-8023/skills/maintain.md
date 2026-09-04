Maintain: keep the running system honest. Do not invent a second orchestrator.

When: outage, 500, wedged WAL, stale ticket, watchdog miss, "is it up".

Order:
1. netie_board + desk_status. Report seated vs unseated. Do not implement SEATED/HELD tickets.
2. Reproduce with the exact command from the last finding. Do not re-derive if INDEX has a HIT.
3. Smallest fix. Then the same verify command. Paste the output, not a vibe.
4. Overnight keep-alive is NetieEstate24x7 + night_watch. Crew is not a 24/7 daemon.
5. If RAM is low, skip gh/gate. Do not page-file the machine into a swarm.

Law: Ticket Runner seats existing writers. One issue per human-opened chat. Human is money/decision.

Findings: docs/subagents_findings/INDEX.md first. Write a finding when you learn a new failure class.
