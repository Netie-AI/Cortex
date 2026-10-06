"""Engine-owned agent plane: generative insights, FreeRoute, OpenVault arming,
LLM calls, GitHub estate, ship gate and the inbox.

Relocated from ``CortexOS.crew`` (CX-3-pre #310) because engine code reaches
these modules; the crew server keeps importing them through re-export shims at
the old ``CortexOS.crew.*`` paths until CX-3 removes it. Crew UI assets and
skill packs stay under ``CortexOS/crew/`` until then.

This package must never import ``CortexOS.crew`` or ``packs.*``.
"""
