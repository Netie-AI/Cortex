"""Re-export shim: moved to :mod:`CortexOS.agentplane.engine_bridge` (CX-3-pre #310).

Aliases this name to the real module, so module state and monkeypatches stay
shared. CX-3 deletes this shim with the crew server.
"""

import sys

from CortexOS.agentplane import engine_bridge as _moved

sys.modules[__name__] = _moved
