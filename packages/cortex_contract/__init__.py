"""Stable contract surface between Cortex engine and DMS app."""

import sys

from .answer import (
    AbstainReason,
    Answer,
    AskRequest,
    Badge,
    ContributingSource,
    DrillthroughRequest,
    DrillthroughResponse,
    Provenance,
)
from .errors import ContractError, UnauthorizedError, ValidationError
from .execution import (
    EngineSubmitter,
    Manifest,
    PoolSpec,
    QueryResult,
    SubmitRequest,
    canonical_manifest_bytes,
)
from .ledger import ChainVerification, LedgerEntry, LedgerWriter
from .proposal import Diff, GateResult, Proposal, ProposalVersion
from .tools import ToolCall, ToolClass, ToolResult, ToolRuntime, ToolSpec
from .version import CONTRACT_VERSION

# --- CONTRACT-01: one file, one module identity ------------------------------
# `packages/` is importable as a namespace package from the repo root, so
# `import packages.cortex_contract` loads *this same file* under a second name
# and builds a second `Manifest` class. `canonical_manifest_bytes` branches on
# `isinstance(manifest, Manifest)`: under two identities that check returns
# False and the manifest is canonicalised through the Mapping branch meant for
# raw dicts. The two branches agree only while every field is a string, and DMS
# signs those bytes while Cortex verifies them - so the first non-string field
# turns a serialisation bug into what looks like a signature failure.
#
# Refusing at name binding rather than forbidding the spelling in tracked source
# closes the class: a notebook, an untracked script, a `python -c` or a debugger
# cannot reach the second identity either.
if __name__ != "cortex_contract":
    for _stale in [n for n in sys.modules if n == __name__ or n.startswith(__name__ + ".")]:
        del sys.modules[_stale]
    raise ImportError(
        f"CONTRACT-01: this package must be imported as 'cortex_contract', not as "
        f"'{__name__}'. Both names resolve to the same file but produce different "
        f"classes, and canonical_manifest_bytes branches on isinstance - so the "
        f"wrong spelling silently changes the bytes DMS signs and Cortex verifies. "
        f"Use 'from cortex_contract.execution import Manifest'."
    )

# The published surface DMS pins against. Adding a name here is a contract
# minor; removing or renaming one is a contract major. See docs/RELEASING.md.
__all__ = [
    "CONTRACT_VERSION",
    "AbstainReason",
    "Answer",
    "AskRequest",
    "Badge",
    "ContributingSource",
    "DrillthroughRequest",
    "DrillthroughResponse",
    "Provenance",
    "ContractError",
    "UnauthorizedError",
    "ValidationError",
    "EngineSubmitter",
    "Manifest",
    "PoolSpec",
    "QueryResult",
    "SubmitRequest",
    "canonical_manifest_bytes",
    "ChainVerification",
    "LedgerEntry",
    "LedgerWriter",
    "Diff",
    "GateResult",
    "Proposal",
    "ProposalVersion",
    "ToolCall",
    "ToolClass",
    "ToolResult",
    "ToolRuntime",
    "ToolSpec",
]
