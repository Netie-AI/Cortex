Infra: one CI, one loop. Cortex is the orchestrator.

Cover: CI/CD, env promotion, IaC, migrations, backups, DR, uptime, scaling, cache, CDN, Docker/K8s.

Rules:
- Call ship_gate first.
- Pages workflow counts as CI for a static app.
- Skip Kubernetes for a local-first engine unless they claim hosted prod.
- Do not start LangGraph, n8n, or a second ticket cron.
- Backups/DR: skip is not pass. Do not invent a runbook.

Law: Ticket Runner seats existing writers. Human is money/decision.
