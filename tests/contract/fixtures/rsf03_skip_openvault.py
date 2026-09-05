"""R-0007 poison fixture. Parsed as AST only — never imported by production.

If RSF-03 drops the OpenVault leave-machine call, skips the gate, or promotes a
study tree to product_engine, test_rsf_boundary_ban fails because this file still
contains the forbidden patterns.
"""

from __future__ import annotations


def _poison_skip_openvault(url: str) -> bytes:
    import urllib.request

    return urllib.request.urlopen(url).read()  # noqa: S310


def _poison_promote_study_tree() -> dict[str, str]:
    return {"engine_role": "product_engine", "id": "myn8n"}
