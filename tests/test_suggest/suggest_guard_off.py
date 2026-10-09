"""pytest plugin: remove one SUGGEST guard, named by ``SUGGEST_GUARD_OFF``.

Loaded only by test_suggest_guard_mutations.py in a subprocess, to prove each
must-fail test fails without the guard it protects.
"""

from __future__ import annotations

import os
from typing import Any

PROVIDERS = ("anthropic", "openai", "litellm", "httpx", "requests", "aiohttp", "google", "cohere", "mistralai", "groq")


def _guard() -> str:
    return os.environ.get("SUGGEST_GUARD_OFF", "")


def pytest_configure(config: Any) -> None:
    from CortexOS.suggest import ask, followups

    guard = _guard()
    if guard == "grounding":
        followups.refuse = lambda *a, **k: None  # type: ignore[assignment]
    elif guard == "abstain":
        ask.not_answered = lambda data: None  # type: ignore[assignment]
    elif guard != "provider_contract":
        raise RuntimeError(f"unknown SUGGEST_GUARD_OFF={guard!r}")


def pytest_collection_modifyitems(session: Any, config: Any, items: list[Any]) -> None:
    if _guard() != "provider_contract":
        return
    for item in items:
        module = getattr(item, "module", None)
        if module is None or not module.__name__.endswith("test_suggest_import_contract"):
            continue
        real = module._contract

        def weakened(real: Any = real) -> dict[str, str]:
            section = dict(real())
            kept = [m for m in section["forbidden_modules"].split() if m not in PROVIDERS]
            section["forbidden_modules"] = "\n" + "\n".join(kept)
            return section

        module._contract = weakened
