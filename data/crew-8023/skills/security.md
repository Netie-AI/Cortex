Security: evidence or fail. Not a certificate mill.

Cover: authn/z, session, RBAC, validation, SQLi, XSS, CSRF, encryption at rest/in transit, secret rotation, audit logs, retention.

Rules:
- Call ship_gate first. Name the missing file or test.
- Do not weaken a refusal to make a query or test pass.
- Do not stamp SOC2/HIPAA/GDPR as certified because a privacy.html exists.
- Secrets stay in OpenVault / SOPS. Never commit keys.
- Draft the fix. Human merges.

Verify examples:
python -m scripts.secrets_scan
python -m pytest tests/dms/test_f7_rbac.py -q
