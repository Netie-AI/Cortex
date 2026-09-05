"""R-0007 poison fixture. Parsed as AST only — never imported by production.

If the Constructor-engine BAN list is emptied, test_constructor_engine_ban
fails because this file still contains these imports.
"""

# ruff: noqa: F401


def _poison_constructor_engine_import() -> None:
    import gencfsm
    import langchain
    import langflow
    import n8n
    from langchain_core import agents as _langchain_core_agents
