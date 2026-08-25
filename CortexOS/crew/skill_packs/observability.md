Observability: identifiers and numbers. No silent swallow.

Cover: logs, tracing, perf, alerting, RUM, Sentry, cost, dependency updates, vuln scanning.

Rules:
- Call ship_gate first.
- Skip is not pass. Do not claim Sentry/RUM from a missing file.
- Cortex ActionEvent / F1 ledger: identifiers, never prompts or titles.
- A failed audit write is reported, never swallowed.
- secrets.yml / secrets_scan is vuln evidence. Dependabot is extra, not required.
