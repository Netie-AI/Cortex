# Ticket runner 2026-08-28 -- LOOP close + SEC-01

Keywords: LOOP-01, LOOP-02, EPIC-016, SEC-01, META-01, SPACE-01, EVAL-01, enforce_manifest, PathNotAllowed, R-0003

Main idea: Different-run verified and closed Cortex LOOP-01/02 and EPIC-016. Implemented SEC-01 so POST /dms/query SQL always goes through enforce_manifest (mint/thread at query_service; ungoverned else branch deleted). META-01 catalog intent already in tree and verified. SPACE-01 and EVAL-01 parked with unlock conditions.

Do not close SEC-01 in the same run that implemented it (R-0003).
