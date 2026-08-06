"""DMS semantic layer — governed metrics, certified queries, value dictionaries.

A package, not a bare directory. Without ``__init__.py`` grimp cannot see these
modules at all, so ``lint-imports`` reported the C2 engine/pack boundary green
while blind to 13 crossings on the hottest path in the repo (C2-01).
"""
