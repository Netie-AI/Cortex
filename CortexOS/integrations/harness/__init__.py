"""EPIC-HARNESS-ENT: one enterprise-safe model harness for every provider key.

HX-01 lands the stdlib-only foundation the rest of the epic builds on:

* :mod:`.registry`: the one provider table, and exact-prefix model resolution.
* :mod:`.secrets`: the one secret env list, child-env scrubbing, error-text
  redaction, and per-call key reads from env or a mounted ``<ENV>_FILE``.

Anything :mod:`CortexOS.integrations.freeroute` imports must stay stdlib-only
at import time (``tests/test_freeroute_core.py`` pins it), so nothing here
imports ``CortexOS.crew``, ``packs`` or a third-party library at module level.
See ``docs/strategy/PRD_EPIC_HARNESS_ENT.md``.
"""
