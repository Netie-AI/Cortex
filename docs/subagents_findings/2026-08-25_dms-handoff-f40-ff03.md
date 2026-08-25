# F40 refused badge, FF-03 L2 violations, VQ-01 certified synonyms

- Date: 2026-08-25
- Keywords: F40, refused, Badge.SESSION, FF-03, SqlGateAbstain, VQ-01, certified synonyms, EPIC-015, ledger.verify
- Main idea: Engine `route=refused` must be an abstain signal (never SESSION). L2 must return gate `violations` on the result/`str(exc)`. Certified L0 may match declared synonyms and BETA→SKU-BETA. Append fails closed if id+hash is not on the chain via `list_entries` (no get-entry port). EPIC-015 RAG-02/03 are shipped; RAG-01 remainder is live Qdrant demo.
- Path: this file
