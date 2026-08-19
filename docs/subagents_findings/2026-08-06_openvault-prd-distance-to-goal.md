```yaml
keywords: [openvault, prd-001, freebuild, freeroute, custody, gate, apple-passwords, vercel, omniroute, kilo, distance-to-goal, epic-13, unseal]
main_idea: "OpenVault north-star sliced to epics #13–#18; ~40% to Apple-Passwords+Vercel+OmniRoute goal — FreeRoute strongest, FreeBuild weakest (CF Pages only); #19 passphrase unseal shipped."
models: [grok-4.5, composer-2.5]
workflow: prd-agent -> epic-agent -> ticket-runner -> adversary-verify
reuse: golden_rule
status: verified
cite: agent: a76a5a8c-fee1-485f-837c-673c5d103fa5
repo: OpenVault
date: 2026-08-06
```

# OpenVault PRD-001 distance to freebuild/omnirouter vault

## Main idea

- Competitive intake (Vercel alts, OmniRoute alts, Kilo, Apple Passwords) routed into PRD-001; Kilo agent-factory stays OOS (Cortex/FreeIDE).
- Epic wave by irreversibility: #13 CUSTODY → #14 GATE → #15 FREEROUTE → #16 FREEBUILD → #17 WEB → #18 DEMO.
- Overall ~40%: FreeRoute ~58%, vault ~42% (+unseal now), gate ~38%, web ~40%, FreeBuild ~28%.

## Keywords (search)

`openvault`, `prd-001`, `freebuild`, `freeroute`, `custody`, `unseal`, `pat.json`, `vercel`, `omniroute`, `apple-passwords`

## Questions left open

- Founder: second FreeBuild host (default assumption Coolify then Netlify)
- Founder: secrets-at-ship injection stays OpenVault (PRODUCT_ROLES default)
- Browser autofill OOS unless PRD amended

## Full answer / evidence

PRD: `D:\Netie\Software Blueprint\OpenVault\PRD-001-one-vault-one-route.md`
Issues: #13–#18 epics; tickets #19 (CLOSED verified), #20, #21
#12 nvidia catalog CLOSED (already in providers.py)

## Golden rule (if reusable)

> For OpenVault competitive asks: map vault/ship/route/gate to OpenVault; refuse agent-loop/IDE/micro-town epics; slice by irreversibility (custody → gate → capability → surface → demo); ticket foundation before FreeBuild host expansion.

## Verify

```bash
cd OpenMW
uv run pytest tests/test_vault_unseal.py tests/test_keywrap.py tests/test_secrets_custody.py tests/test_secret_reveal_gate.py -q
gh issue list --repo Netie-AI/OpenVault --limit 20
```

## Promote?

Yes — into OpenVault STATUS distance scorecard when FreeBuild epic tickets exist.
