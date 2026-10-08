"""SCHEMA-RETRIEVE (#306) model path: OV FreeRoute only, scripted in CI.

The reranker is driven through the scripted OpenVault (``armed_openvault``, no
sockets) replaying the recorded C-STAMP response body, which carries the real
``served_provider`` / ``served_model``. The model may reorder granted candidates
and never adds one; FreeRoute unarmed falls back to deterministic order.
"""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
from typing import Any

import pytest

from CortexOS.schema_retrieve import DeterministicSchemaRetriever, GrantScope
from CortexOS.schema_retrieve.catalog import load_pack_catalog
from CortexOS.schema_retrieve.freeroute_rerank import METHOD, TASK, FreeRouteReranker, parse_order

ROOT = Path(__file__).resolve().parents[2]
C_STAMP = ROOT / "tests" / "dms" / "fixtures" / "c_stamp_289" / "resp_body.json"
QUESTION = "which suppliers have delayed shipments and what inventory do they hold"
PROVIDER_SDKS = ("openai", "anthropic", "litellm", "groq", "google", "cohere", "mistralai", "httpx")


def _recorded(content: str) -> dict[str, Any]:
    body = copy.deepcopy(json.loads(C_STAMP.read_text(encoding="utf-8")))
    body["choices"][0]["message"] = {"role": "assistant", "content": content}
    return body


@pytest.fixture(scope="module")
def catalog():
    return load_pack_catalog(ROOT / "packs" / "dms")


def test_freeroute_rerank_reorders_granted_candidates_and_stamps_served(
    armed_openvault, catalog
) -> None:
    armed_openvault.replies.append((200, _recorded('["inventory", "shipments"]')))
    retriever = DeterministicSchemaRetriever(catalog, reranker=FreeRouteReranker())
    grant = GrantScope("space-a", frozenset({"inventory", "shipments", "suppliers"}))
    deterministic = DeterministicSchemaRetriever(catalog).retrieve(QUESTION, grant=grant).stamp

    stamp = retriever.retrieve(QUESTION, grant=grant).stamp

    assert deterministic.served_chosen[0].table != "inventory", "control: model changed the order"
    assert stamp.served_method == METHOD
    assert [t.table for t in stamp.served_chosen][:2] == ["inventory", "shipments"]
    assert stamp.served_provider == "groq"
    assert stamp.served_model == "openai/gpt-oss-120b"
    (call,) = armed_openvault.chat_calls
    assert armed_openvault.non_openvault_calls == []
    prompt = call["body"]["messages"][1]["content"]
    assert "- suppliers(" in prompt and "- inventory(" in prompt


def test_must_fail_freeroute_rerank_never_adds_an_ungranted_table(armed_openvault, catalog) -> None:
    armed_openvault.replies.append(
        (200, _recorded('["suppliers", "payroll", "shipments", "inventory"]'))
    )
    retriever = DeterministicSchemaRetriever(catalog, reranker=FreeRouteReranker())
    grant = GrantScope("space-a", frozenset({"inventory", "transactions"}))

    stamp = retriever.retrieve(QUESTION, grant=grant).stamp

    assert stamp.served_method == METHOD
    raw = stamp.model_dump_json()
    for name in ("suppliers", "payroll", "shipments"):
        assert name not in raw, f"{name} leaked: {raw}"
    prompt = armed_openvault.chat_calls[0]["body"]["messages"][1]["content"]
    tables_part = prompt.split("Tables:\n", 1)[1]
    assert "suppliers" not in tables_part and "shipments" not in tables_part


def test_freeroute_unarmed_falls_back_to_deterministic(fake_openvault, catalog) -> None:
    grant = GrantScope("space-a", frozenset(catalog.tables))
    plain = DeterministicSchemaRetriever(catalog).retrieve(QUESTION, grant=grant).stamp
    stamp = (
        DeterministicSchemaRetriever(catalog, reranker=FreeRouteReranker())
        .retrieve(QUESTION, grant=grant)
        .stamp
    )

    assert fake_openvault.chat_calls == []
    assert stamp.served_method == "deterministic"
    assert stamp.served_reason.startswith("rerank unavailable: FreeRoute not armed")
    assert [t.table for t in stamp.served_chosen] == [t.table for t in plain.served_chosen]


def test_unparseable_reply_falls_back_to_deterministic(armed_openvault, catalog) -> None:
    armed_openvault.replies.append((200, _recorded("inventory is the best table")))
    stamp = (
        DeterministicSchemaRetriever(catalog, reranker=FreeRouteReranker())
        .retrieve(QUESTION, grant=GrantScope("s", frozenset(catalog.tables)))
        .stamp
    )
    assert stamp.served_method == "deterministic"
    assert stamp.served_reason.startswith("rerank unavailable")


def test_reranker_uses_the_freeroute_task_and_no_rows() -> None:
    calls: list[dict[str, Any]] = []

    class Done:
        ok = True
        text = '["inventory"]'
        stamp = None
        reason = ""

    def fake_complete(task: str, messages: list[dict[str, Any]], **kw: Any) -> Done:
        calls.append({"task": task, "messages": messages, **kw})
        return Done()

    catalog = load_pack_catalog(ROOT / "packs" / "dms")
    FreeRouteReranker(complete=fake_complete).rerank("how many skus", [catalog.tables["inventory"]])
    (call,) = calls
    assert call["task"] == TASK and call["temperature"] == 0.0
    assert call["accept"]('["x"]') and not call["accept"]("no list")
    assert "email" not in json.dumps(call["messages"])


@pytest.mark.parametrize(
    "text, expected",
    [
        ('["Inventory", " shipments "]', ["inventory", "shipments"]),
        ('Sure: ["a"] done', ["a"]),
        ("[1, 2]", None),
        ("[]", None),
        ("nothing", None),
    ],
)
def test_parse_order(text: str, expected: list[str] | None) -> None:
    assert parse_order(text) == expected


def test_no_provider_sdk_anywhere_in_schema_retrieve() -> None:
    names: list[str] = []
    for path in (ROOT / "CortexOS" / "schema_retrieve").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
    roots = {n.split(".")[0] for n in names}
    assert not roots & set(PROVIDER_SDKS), roots
    model_paths = {n for n in names if n.startswith("CortexOS.integrations")}
    assert model_paths <= {"CortexOS.integrations"}, model_paths
