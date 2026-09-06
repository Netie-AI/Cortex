# 2026-09-05 -- OpenVault loopback chat HTTP 500

Keywords: openvault, 500, decrypt, vault_sealed, worktree, 8010, 8011, get_secret, reveal

Main idea: Live `:5000` was a worktree `openmw console --mock-health --cortex-url :8010`. `POST /v1/chat/completions` returned plain `Internal Server Error` in 0.4s with no usage row. `GET /api/keys/{id}/secret` 500d on every pooled key (Fernet blobs valid; decrypt raised). Gateway now skips `VaultCryptoError` hops (502/403, never Starlette 500). Canonical `D:\OpenVault` console is on `:5000` with `cortex_url :8011`. Vault starts sealed (passphrase-scrypt). Constructor stays `:8010`; engine `:8011`. Pointer HUD skipped below 2 GB free RAM. Set `NETIE_CORTEX_URL=http://127.0.0.1:8011`.

## Verify

```
# sealed is honest 403, not 500
curl -s -o - -w "%{http_code}" http://127.0.0.1:5000/api/vault/status
curl -s -D - http://127.0.0.1:5000/v1/chat/completions -H "Content-Type: application/json" -d "{\"model\":\"auto\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":8}"
cd D:\OpenVault\OpenMW && .venv\Scripts\python.exe -m pytest tests/test_attempt_policy.py::test_corrupt_secret_skips_to_next_hop -q
```

Does NOT prove: hops succeed after unseal (needs founder passphrase on `:3010`). Does NOT prove Cortex agent can fill PDFs (memory count was 0; do not invent PII).
