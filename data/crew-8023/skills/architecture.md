Architecture: additive is cheap. Subtractive is a major.

Cover: indexes, pooling, API versioning, rate limits, queues, background jobs, webhooks, idempotency, third-party integrations.

Rules:
- Call ship_gate first.
- Cortex contract: add = minor; remove/rename/retype = major. Bump version.py and pyproject.toml together. Regenerate OpenAPI. Never hand-edit contract/*.json.
- Do not change canonical_manifest_bytes. DMS signs those bytes.
- duckdb only under CortexOS/execution/.
- Idempotency and webhook retries need a test, not a comment.
