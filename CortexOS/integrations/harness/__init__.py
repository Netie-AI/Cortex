"""EPIC-HARNESS-ENT secret hygiene (HX-01). Stdlib only at import.

* :mod:`.ov_key_envs`: a read-only mirror of the provider key env *names*
  OpenVault custodies. OpenVault owns the provider catalogue and every key;
  Cortex keeps no provider registry, base URLs or prices.
* :mod:`.secrets`: the one secret env list, child-env scrubbing, error-text
  redaction, and per-call reads of a mounted ``<ENV>_FILE``.

Anything :mod:`CortexOS.integrations.freeroute` imports must stay stdlib-only
at import time (``tests/test_freeroute_core.py`` pins it), so nothing here
imports ``CortexOS.crew``, ``packs`` or a third-party library.
"""
