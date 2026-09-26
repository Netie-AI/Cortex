"""HX-01: crew key saves never reach an unsafe vault and never lose a key (PRD R1.9).

Stubbed only: the vault client is recorded, never reached.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from CortexOS.crew import keys, openvault

SECRET = "sk-hx01-must-never-leave-123456"


@pytest.fixture(autouse=True)
def hermetic(monkeypatch):
    for name in ("CREW_OPENVAULT_URL", "OPENVAULT_BASE_URL", "OPENVAULT_URL", "CREW_VAULT_LAST_ERROR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CREW_OPENVAULT", "1")
    for name in keys.KNOWN:
        monkeypatch.delenv(name, raising=False)
    yield
    for name in keys.KNOWN:
        os.environ.pop(name, None)


@pytest.fixture()
def http(monkeypatch) -> list[str]:
    """Every request the crew vault client would make."""
    sent: list[str] = []

    class Client:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *a: Any) -> bool:
            return False

        def post(self, url: str, **kw: Any) -> Any:
            sent.append(url)
            raise openvault.httpx.ConnectError("blocked in test")

        get = patch = post

    monkeypatch.setattr(openvault.httpx, "Client", Client)
    return sent


def _stored(data_dir: Path) -> dict[str, str]:
    return json.loads((data_dir / "keys.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("url", ["http://vault.example.com", "http://10.1.2.3:5000", "ftp://127.0.0.1"])
def test_save_refuses_a_non_https_non_loopback_vault_with_zero_requests(monkeypatch, tmp_path, http, url) -> None:
    monkeypatch.setenv("CREW_OPENVAULT_URL", url)
    out = keys.save(tmp_path, {"OPENAI_API_KEY": SECRET})
    assert http == [], "the key was sent to a vault that is neither https nor loopback"
    err = os.environ["CREW_VAULT_LAST_ERROR"]
    assert err == keys.VAULT_REFUSED and SECRET not in err
    assert SECRET not in json.dumps(out)
    assert _stored(tmp_path)["OPENAI_API_KEY"] == SECRET  # kept local, not lost


@pytest.mark.parametrize("url", ["https://vault.example.com", "http://127.0.0.1:5000", "http://localhost:5000"])
def test_save_upserts_to_an_https_or_loopback_vault(monkeypatch, tmp_path, url) -> None:
    monkeypatch.setenv("CREW_OPENVAULT_URL", url)
    calls: list[str] = []
    monkeypatch.setattr(openvault, "upsert_env_key", lambda k, s: calls.append(k) or {"ok": True})
    keys.save(tmp_path, {"GROQ_API_KEY": SECRET})
    assert calls == ["GROQ_API_KEY"]
    assert "GROQ_API_KEY" not in _stored(tmp_path)  # the vault serves groq back


def test_nvidia_key_stays_local_when_the_vault_cannot_serve_it_back(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CREW_OPENVAULT_URL", "http://127.0.0.1:5000")
    monkeypatch.setattr(openvault, "upsert_env_key", lambda k, s: {"ok": True})
    keys.save(tmp_path, {"NVIDIA_API_KEY": SECRET})
    assert _stored(tmp_path)["NVIDIA_API_KEY"] == SECRET
    assert keys.load_saved(tmp_path)["NVIDIA_API_KEY"] == SECRET


def test_vault_error_detail_never_echoes_the_key(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CREW_OPENVAULT_URL", "http://127.0.0.1:5000")
    monkeypatch.setattr(openvault, "upsert_env_key", lambda k, s: {"ok": False, "detail": f"HTTP 400: bad {s}"})
    keys.save(tmp_path, {"OPENAI_API_KEY": SECRET})
    assert SECRET not in os.environ["CREW_VAULT_LAST_ERROR"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_keys_json_is_0600_even_when_it_existed_0644(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CREW_OPENVAULT", "0")
    path = tmp_path / "keys.json"
    path.write_text("{}", encoding="utf-8")
    os.chmod(path, 0o644)
    keys.save(tmp_path, {"CREW_MODEL": "x/y"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not (tmp_path / "keys.json.tmp").exists()
