"""Locked FreeRoute route store still serves the answer.

Arms FreeRoute in-process (scripted OpenVault, no live model, no network) with
``CORTEX_FREEROUTE_LEARN`` unset so ``pick`` / ``_write_row`` reach the route
store. A second connection holds ``BEGIN EXCLUSIVE`` on that file. The
learning-only row write fails after the model has answered. ``complete()``
returns that answer, and each of the nine handlers serves it (``llm_used``
true, no ``model_route_unavailable``).

The ``_connect`` count is the proof the store ran. An unarmed call never
enters ``_connect``, so a served body alone would not pass this test.
"""
from __future__ import annotations

import importlib
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.dms.test_brain_freeroute import (
    _BRAIN_STUB_TEXT,
    _SUGGEST_STUB_TEXT,
    HANDLERS,
    _post,
    _prepare,
    _refusal_of,
    install_sentinel,
)


@pytest.fixture()
def brain_client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> TestClient:
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    import netie.config

    netie.config._cached_config = None
    # ``packs.dms.tasks`` binds the suggest function, so the submodule is
    # loaded by name before the candidate patch looks it up.
    importlib.import_module("packs.dms.tasks.suggest")
    importlib.import_module("packs.dms.generative.brain")
    from packs.dms.security.rate_limit import reset_limiter

    reset_limiter(10_000)
    from CortexOS.api.app import create_app

    return TestClient(create_app(), headers={"X-API-Key": "dms-demo-admin-key"})


@pytest.fixture()
def locked_store(armed_openvault, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, int]]:
    """Route-store file locked exclusive. Transport stays the scripted OpenVault."""
    assert os.environ.get("CORTEX_FREEROUTE_LEARN") is None
    from CortexOS.integrations import freeroute as fr

    fr.reset()
    assert fr.arming(fresh=True).armed is True
    path = fr.store_path()
    fr._init_file(path)
    holder = sqlite3.connect(str(path), timeout=1.0)
    holder.execute("BEGIN EXCLUSIVE")
    real_connect = sqlite3.connect

    def _fast_connect(target, *args, **kwargs):
        kwargs["timeout"] = 0.05
        return real_connect(target, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", _fast_connect)
    hits = {"n": 0}
    real = fr._connect

    @contextmanager
    def _counting(*, write: bool) -> Iterator[Any]:
        hits["n"] += 1
        with real(write=write) as con:
            yield con

    monkeypatch.setattr(fr, "_connect", _counting)
    try:
        yield hits
    finally:
        holder.rollback()
        holder.close()


@pytest.mark.parametrize("spec", HANDLERS, ids=[h["name"] for h in HANDLERS])
def test_locked_store_serves(
    spec: dict[str, Any],
    brain_client: TestClient,
    locked_store: dict[str, int],
    armed_openvault,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = _SUGGEST_STUB_TEXT if spec["name"] == "suggest" else _BRAIN_STUB_TEXT
    armed_openvault.replies.clear()
    armed_openvault.reply(text)
    sentinel = install_sentinel(monkeypatch)
    _prepare(monkeypatch, spec)
    before = locked_store["n"]
    chats_before = len(armed_openvault.chat_calls)
    response = _post(brain_client, spec)
    assert locked_store["n"] > before
    assert len(armed_openvault.chat_calls) > chats_before
    assert sentinel.count == 0, sentinel.hits
    assert response.status_code == 200, response.text
    refusal, llm_used, row = _refusal_of(spec, response.json())
    assert refusal is None
    assert llm_used is True
    assert row.get("refusal") is None
