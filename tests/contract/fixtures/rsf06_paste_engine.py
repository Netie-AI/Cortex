"""R-0007 poison fixture. Parsed as AST only — never imported by production.

If RSF-06 drops the paste BAN, this file still imports langchain/langflow/n8n
and stamps product_engine on a Constructor kind.
"""

# ruff: noqa: F401

from __future__ import annotations


def _poison_paste_analogs_as_engine() -> dict[str, str]:
    import langchain
    import langflow
    import n8n

    return {
        "id": "langchain",
        "engine_role": "product_engine",
        "kind": "langchain",
        "module": "langchain",
        "also": "langflow",
        "n8n": "n8n",
    }
