"""BRAIN-FREEROUTE-01 (#350).

Each ``/dms/brain/*`` model handler must call ``CortexOS.crew.freeroute.complete``
and must not call a provider SDK. ``test_must_fail_unarmed_no_direct_call``
asserts a sentinel count of 0 and the named refusal ``model_route_unavailable``.
On the parent those handlers call ``anthropic`` when ``ANTHROPIC_API_KEY`` is
set, so the same tests fail there. Nothing here calls a live model.
"""
from __future__ import annotations

import ast
import importlib
import json
import logging
import sys
import types
import urllib.request
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
PROMPT = "PROMPT_TOKEN_brain350"
VALUE = "991337"
REFUSAL = "model_route_unavailable"
STUB_PROVIDER = "from-stamp"
STUB_MODEL = "from-stamp-model"

PROVIDER_HOSTS = (
    "api.anthropic.com",
    "api.openai.com",
    "generativelanguage.googleapis.com",
    "api.groq.com",
    "api.mistral.ai",
    "api.cohere.ai",
    "api.cohere.com",
    "api.together.xyz",
    "openrouter.ai",
    "api.deepseek.com",
    "api.x.ai",
    "api.fireworks.ai",
)
PROVIDER_ROOTS = frozenset(
    {"anthropic", "openai", "litellm", "cohere", "groq", "mistralai", "together"}
)
PROVIDER_PREFIXES = ("google.generativeai", "google.genai", "vertexai")

HANDLERS: tuple[dict[str, Any], ...] = (
    {
        "name": "run",
        "path": "/dms/brain/run",
        "body": {
            "intent": "generate_chart",
            "params": {"query": PROMPT, "data": {"metric": VALUE}},
        },
    },
    {
        "name": "chart",
        "path": "/dms/brain/chart",
        "body": {"query": PROMPT, "data": {"metric": VALUE}},
    },
    {
        "name": "export",
        "path": "/dms/brain/export",
        "body": {"query": PROMPT, "table": "inventory", "format": "csv", "limit": 5},
        "rows": True,
    },
    {
        "name": "email",
        "path": "/dms/brain/email",
        "body": {"request": PROMPT, "context": {"metric": VALUE}},
    },
    {
        "name": "whatsapp",
        "path": "/dms/brain/whatsapp",
        "body": {"request": PROMPT, "context": {"metric": VALUE}},
    },
    {
        "name": "analyze",
        "path": "/dms/brain/analyze",
        "body": {"period": PROMPT},
        "warehouse": True,
    },
    {
        "name": "auto-analysis",
        "path": "/dms/brain/auto-analysis",
        "body": None,
        "warehouse": True,
    },
    {
        "name": "report",
        "path": "/dms/brain/report",
        "body": {"query": PROMPT},
        "warehouse": True,
    },
    {
        "name": "suggest",
        "path": "/dms/brain/suggest",
        "body": {"use_llm": True, "trigger_text": PROMPT},
        "candidates": True,
    },
)


class Sentinel:
    def __init__(self) -> None:
        self.count = 0
        self.hits: list[str] = []

    def hit(self, where: str) -> None:
        self.count += 1
        self.hits.append(where)


def _provider_host(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == name or host.endswith("." + name) for name in PROVIDER_HOSTS)


def install_sentinel(monkeypatch: pytest.MonkeyPatch) -> Sentinel:
    """Trip on anthropic, provider-host httpx, any urlopen, and litellm."""
    sentinel = Sentinel()

    class _Messages:
        def create(self, *args: Any, **kwargs: Any) -> Any:
            sentinel.hit("anthropic.messages.create")
            block = types.SimpleNamespace(text='{"summary":"sentinel"}')
            return types.SimpleNamespace(content=[block])

    class _Anthropic:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            sentinel.hit("anthropic.Anthropic")
            self.messages = _Messages()

    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.Anthropic = _Anthropic  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)

    real_send = httpx.Client.send

    def _client_send(self: httpx.Client, request: httpx.Request, *args: Any, **kwargs: Any) -> Any:
        if _provider_host(request.url.host or ""):
            sentinel.hit("httpx.Client.send:" + (request.url.host or ""))
            raise RuntimeError("httpx provider sentinel")
        return real_send(self, request, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", _client_send)

    real_async_send = httpx.AsyncClient.send

    async def _async_send(
        self: httpx.AsyncClient, request: httpx.Request, *args: Any, **kwargs: Any
    ) -> Any:
        if _provider_host(request.url.host or ""):
            sentinel.hit("httpx.AsyncClient.send:" + (request.url.host or ""))
            raise RuntimeError("httpx provider sentinel")
        return await real_async_send(self, request, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "send", _async_send)

    def _urlopen(url: object, *args: Any, **kwargs: Any) -> Any:
        sentinel.hit("urllib.request.urlopen:" + str(getattr(url, "full_url", url)))
        raise RuntimeError("urlopen sentinel")

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)

    def _completion(*args: Any, **kwargs: Any) -> Any:
        sentinel.hit("litellm.completion")
        raise RuntimeError("litellm sentinel")

    loaded = sys.modules.get("litellm")
    if loaded is not None:
        monkeypatch.setattr(loaded, "completion", _completion, raising=False)
        monkeypatch.setattr(loaded, "acompletion", _completion, raising=False)
    else:
        fake_litellm = types.ModuleType("litellm")
        fake_litellm.completion = _completion  # type: ignore[attr-defined]
        fake_litellm.acompletion = _completion  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "litellm", fake_litellm)
    return sentinel


def _candidates(_state: dict) -> list[dict]:
    return [
        {
            "task_id": f"t{i}",
            "title": PROMPT,
            "description": VALUE,
            "action": "audit",
            "targets": [],
            "priority": "low",
            "source": "rule:probe",
            "confidence": 0.4 + i / 100,
        }
        for i in range(4)
    ]


def _prepare(monkeypatch: pytest.MonkeyPatch, spec: dict[str, Any]) -> None:
    if spec.get("rows"):
        monkeypatch.setattr(
            "CortexOS.api.brain_routes._get_db_data",
            lambda table, limit=5000: [{"sku": "S1", "qty": VALUE}],
        )
    if spec.get("warehouse"):
        monkeypatch.setattr(
            "CortexOS.api.brain_routes._get_warehouse_context",
            lambda: {"metric": VALUE, "note": PROMPT},
        )
    if spec.get("candidates"):
        # The package attribute ``suggest`` is the function, not the module.
        monkeypatch.setattr(
            sys.modules["packs.dms.tasks.suggest"],
            "_rule_candidates",
            _candidates,
        )


def _post(client: TestClient, spec: dict[str, Any]) -> Any:
    if spec["body"] is None:
        return client.post(spec["path"])
    return client.post(spec["path"], json=spec["body"])


def _refusal_of(spec: dict[str, Any], body: dict) -> tuple[Any, Any, Any]:
    if spec["name"] == "suggest":
        row = (body.get("suggestions") or [{}])[0]
        return row.get("refusal"), row.get("llm_used"), row
    return body.get("refusal"), body.get("llm_used"), body


def _info_text(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(
        rec.getMessage() for rec in caplog.records if rec.levelno >= logging.INFO
    )


@pytest.fixture()
def brain_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    # Parent reads this at import. Head ignores it. Non-empty, not a real key.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "present-for-parent-proof")
    import netie.config

    netie.config._cached_config = None
    brain = importlib.import_module("packs.dms.generative.brain")
    suggest_mod = importlib.import_module("packs.dms.tasks.suggest")
    # Parent reads ANTHROPIC_API_KEY at import. Head does not.
    importlib.reload(suggest_mod)
    importlib.reload(brain)
    from packs.dms.security.rate_limit import reset_limiter

    reset_limiter(10_000)
    from CortexOS.api.app import create_app

    return TestClient(create_app(), headers={"X-API-Key": "dms-demo-admin-key"})


@pytest.mark.parametrize("spec", HANDLERS, ids=[h["name"] for h in HANDLERS])
def test_must_fail_unarmed_no_direct_call(
    spec: dict[str, Any],
    brain_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unarmed FreeRoute: named refusal, sentinel 0, prompt not logged at INFO.

    Parent fails this: with ANTHROPIC_API_KEY set the handler calls anthropic
    and the sentinel count is not 0.
    """
    sentinel = install_sentinel(monkeypatch)
    _prepare(monkeypatch, spec)
    with caplog.at_level(logging.INFO):
        response = _post(brain_client, spec)
    assert sentinel.count == 0, sentinel.hits
    assert response.status_code == 200, response.text
    refusal, llm_used, row = _refusal_of(spec, response.json())
    assert refusal == REFUSAL
    assert llm_used is False
    assert row.get("served_provider") is None
    assert row.get("served_model") is None
    if spec["name"] != "suggest":
        assert "chart_type" not in row
        assert "narrative" not in row
        assert "body" not in row or spec["name"] == "export"
    logged = _info_text(caplog)
    assert PROMPT not in logged
    assert VALUE not in logged


def _stub_complete(calls: list[dict[str, Any]], text: str):
    async def _complete(messages: Any = None, **kwargs: Any) -> dict[str, Any]:
        calls.append({"messages": messages, "kwargs": kwargs})
        return {
            "ok": True,
            "text": text,
            "model": "requested-looking",
            "served_provider": "decoy-top-level",
            "served_model": "decoy-top-level",
            "stamp": {
                "served_provider": STUB_PROVIDER,
                "served_model": STUB_MODEL,
                "served": "requested-looking",
            },
        }

    return _complete


_BRAIN_STUB_TEXT = json.dumps(
    {
        "chart_type": "bar",
        "title": "Units",
        "data": [{"name": "a", "value": 1}],
        "subject": "Hello",
        "body": "Body text here",
        "message": "Short note",
        "narrative": "Stable.",
        "performance_score": 50,
        "markdown": "# Report",
        "summary": "One sentence.",
        "suggested_filename": "report.md",
        "served_provider": "from-model-text",
        "served_model": "from-model-text",
    }
)
_SUGGEST_STUB_TEXT = json.dumps(
    [
        {
            "task_id": "t0",
            "title": "T0",
            "description": "d",
            "action": "audit",
            "targets": [],
            "priority": "low",
            "source": "rule:probe",
            "confidence": 0.5,
            "rationale": "because",
            "served_provider": "from-model-text",
            "served_model": "from-model-text",
        }
    ]
)


@pytest.mark.parametrize("spec", HANDLERS, ids=[h["name"] for h in HANDLERS])
def test_freeroute_stub_one_call_served_from_stamp(
    spec: dict[str, Any],
    brain_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = install_sentinel(monkeypatch)
    _prepare(monkeypatch, spec)
    calls: list[dict[str, Any]] = []
    text = _SUGGEST_STUB_TEXT if spec["name"] == "suggest" else _BRAIN_STUB_TEXT
    monkeypatch.setattr(
        "CortexOS.crew.freeroute.complete",
        _stub_complete(calls, text),
    )
    response = _post(brain_client, spec)
    assert response.status_code == 200, response.text
    assert sentinel.count == 0, sentinel.hits
    assert len(calls) == 1
    assert calls[0]["kwargs"].get("purpose") == "think"
    _refusal, llm_used, row = _refusal_of(spec, response.json())
    assert llm_used is True
    assert row.get("served_provider") == STUB_PROVIDER
    assert row.get("served_model") == STUB_MODEL
    assert row.get("refusal") is None


@pytest.mark.parametrize("spec", HANDLERS, ids=[h["name"] for h in HANDLERS])
def test_guard_removed_direct_call_is_counted(
    spec: dict[str, Any],
    brain_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A handler that calls anthropic directly must trip the sentinel.

    This is the in-process guard-removed proof: the must-fail assertion
    (count == 0) does not hold once the FreeRoute call is replaced.
    """
    sentinel = install_sentinel(monkeypatch)
    _prepare(monkeypatch, spec)

    def _direct_ai(prompt: str, max_tokens: int = 2000) -> dict:
        import anthropic

        anthropic.Anthropic().messages.create(
            model="x",
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return {
            "chart_type": "bar",
            "title": "t",
            "data": [],
            "body": "b",
            "message": "m",
            "narrative": "n",
            "performance_score": 1,
            "markdown": "# r",
            "summary": "s",
            "llm_used": True,
            "served_provider": "should-not-matter",
            "served_model": "should-not-matter",
        }

    def _direct_rank(candidates: list[dict], context: str) -> tuple[list[dict], dict]:
        import anthropic

        anthropic.Anthropic().messages.create(
            model="x",
            max_tokens=8,
            messages=[{"role": "user", "content": context}],
        )
        return candidates, {
            "llm_used": True,
            "refusal": None,
            "served_provider": "should-not-matter",
            "served_model": "should-not-matter",
        }

    if spec["name"] == "suggest":
        monkeypatch.setattr(
            sys.modules["packs.dms.tasks.suggest"],
            "_llm_rank_and_explain",
            _direct_rank,
        )
    else:
        monkeypatch.setattr("packs.dms.generative.brain._ai", _direct_ai)
    response = _post(brain_client, spec)
    assert response.status_code == 200, response.text
    assert sentinel.count > 0, spec["name"]


def test_refusal_not_keyed_on_question_or_table(
    brain_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = install_sentinel(monkeypatch)
    monkeypatch.setattr(
        "CortexOS.api.brain_routes._get_db_data",
        lambda table, limit=5000: [{"sku": "S1", "qty": 1}],
    )
    chart_a = brain_client.post(
        "/dms/brain/chart",
        json={"query": "alpha request", "data": {"metric": "1"}},
    )
    chart_b = brain_client.post(
        "/dms/brain/chart",
        json={"query": "beta request", "data": {"metric": "2"}},
    )
    export_a = brain_client.post(
        "/dms/brain/export",
        json={"query": "alpha", "table": "inventory", "format": "csv"},
    )
    export_b = brain_client.post(
        "/dms/brain/export",
        json={"query": "beta", "table": "shipments", "format": "csv"},
    )
    assert sentinel.count == 0, sentinel.hits
    refusals = [
        chart_a.json()["refusal"],
        chart_b.json()["refusal"],
        export_a.json()["refusal"],
        export_b.json()["refusal"],
    ]
    assert refusals == [REFUSAL, REFUSAL, REFUSAL, REFUSAL]


def _is_provider_module(name: str) -> bool:
    root = name.split(".", 1)[0]
    if root in PROVIDER_ROOTS:
        return True
    return any(name == prefix or name.startswith(prefix + ".") for prefix in PROVIDER_PREFIXES)


def provider_imports(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        found.extend(name for name in names if _is_provider_module(name))
    return found


def test_provider_import_walker_flags_function_level_sdk() -> None:
    tree = ast.parse("def f():\n    import anthropic\n    from litellm import completion\n")
    assert provider_imports(tree) == ["anthropic", "litellm"]


def test_no_provider_sdk_import_under_packs_dms_or_api() -> None:
    offenders: list[str] = []
    for base in (ROOT / "packs" / "dms", ROOT / "CortexOS" / "api"):
        for path in sorted(base.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for mod in provider_imports(tree):
                offenders.append(f"{path.relative_to(ROOT).as_posix()}:{mod}")
    assert offenders == []


def test_brain_modules_do_not_read_provider_env() -> None:
    for rel in (
        "packs/dms/generative/brain.py",
        "packs/dms/generative/model_route.py",
        "packs/dms/tasks/suggest.py",
    ):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "ANTHROPIC_API_KEY" not in text
        assert "import anthropic" not in text
        assert "api.anthropic.com" not in text
        assert "litellm" not in text
