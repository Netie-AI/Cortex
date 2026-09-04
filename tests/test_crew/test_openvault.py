"""OpenVault ingest + routing. No live secrets, no live vault."""

from __future__ import annotations

import pytest

from CortexOS.crew import config, openvault


class _FakeResp:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


def test_upsert_skipped_when_openvault_disabled(monkeypatch) -> None:
    monkeypatch.setenv("CREW_OPENVAULT", "0")
    called: list[str] = []

    class Boom:
        def __init__(self, *a, **k):  # noqa: ANN002, ANN003
            called.append("client")

        def __enter__(self):
            return self

        def __exit__(self, *a):  # noqa: ANN002
            return False

        def post(self, *a, **k):  # noqa: ANN002, ANN003
            raise AssertionError("must not hit the vault when CREW_OPENVAULT=0")

    monkeypatch.setattr(openvault.httpx, "Client", Boom)
    out = openvault.upsert_env_key("CURSOR_API_KEY", "sk-test-not-real")
    assert out["ok"] is False
    assert out["detail"] == "CREW_OPENVAULT=0"
    assert called == []


def test_upsert_uses_keyvault_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("CREW_OPENVAULT", "1")
    seen: list[tuple[str, dict]] = []

    class Client:
        def __init__(self, *a, **k):  # noqa: ANN002, ANN003
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):  # noqa: ANN002
            return False

        def post(self, url: str, json: dict) -> _FakeResp:
            seen.append((url, json))
            return _FakeResp(
                200,
                {
                    "ok": True,
                    "results": [
                        {
                            "ok": True,
                            "action": "created",
                            "key": {"id": "k1", "provider": "custom", "label": "CURSOR_API_KEY"},
                        }
                    ],
                },
            )

    monkeypatch.setattr(openvault.httpx, "Client", Client)
    out = openvault.upsert_env_key("CURSOR_API_KEY", "sk-test-not-real")
    assert out["ok"] is True
    assert out["id"] == "k1"
    assert out["provider"] == "custom"
    assert seen[0][0].endswith("/api/keyvault/upsert")
    assert seen[0][1]["env_key"] == "CURSOR_API_KEY"
    assert "sk-test-not-real" not in str(out)


def test_ingest_cursor_from_keys_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CREW_OPENVAULT", "1")
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    (tmp_path / "data" / "crew").mkdir(parents=True)
    (tmp_path / "data" / "crew" / "keys.json").write_text(
        '{"CURSOR_API_KEY": "sk-from-file"}', encoding="utf-8"
    )
    monkeypatch.setattr(
        openvault,
        "upsert_env_key",
        lambda key, secret: {"ok": True, "id": "k2", "label": key, "chars_in": len(secret)},
    )
    out = openvault.ingest_cursor_from_files(tmp_path)
    assert out["ok"] is True
    assert out["source"] == "keys.json"
    assert out["chars"] == len("sk-from-file")
    assert "sk-from-file" not in str(out)


def test_disable_seeded_cortex_primary(monkeypatch) -> None:
    monkeypatch.setenv("CREW_OPENVAULT", "1")
    patched: list[dict] = []

    class Client:
        def __init__(self, *a, **k):  # noqa: ANN002, ANN003
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):  # noqa: ANN002
            return False

        def get(self, url: str) -> _FakeResp:
            return _FakeResp(
                200,
                {
                    "keys": [
                        {
                            "id": "9d928cef3c024eed9b375a8686e4d4f8",
                            "label": "Netie Cortex (seeded)",
                            "enabled": True,
                            "provider": "cortex",
                        }
                    ]
                },
            )

        def patch(self, url: str, json: dict) -> _FakeResp:
            patched.append({"url": url, **json})
            return _FakeResp(200, {"id": "9d928cef3c024eed9b375a8686e4d4f8", "enabled": False})

    monkeypatch.setattr(openvault.httpx, "Client", Client)
    out = openvault.disable_seeded_cortex_primary()
    assert out["ok"] is True
    assert out["detail"] == "disabled"
    assert patched[0]["enabled"] is False


def test_resolve_ov_model_prefers_grok_high_when_cursor_key(monkeypatch) -> None:
    monkeypatch.setenv("CURSOR_API_KEY", "sk-test")
    monkeypatch.delenv("CREW_OPENVAULT_MODEL", raising=False)
    monkeypatch.delenv("CREW_CURSOR_MODEL", raising=False)
    assert openvault.resolve_ov_model("openvault/auto") == "grok-4.6"
    assert openvault.resolve_ov_model("grok-4.6-fast") == "grok-4.6"
    monkeypatch.setenv("CREW_OPENVAULT_MODEL", "groq/llama")
    assert openvault.resolve_ov_model("auto") == "groq/llama"


# Deliberately not shaped like a real key. scripts/secrets_scan.py matches
# `sk-` followed by 20+ key characters, and a tracked file containing one is a
# CI failure whether or not the value is fake. The assertions below only need a
# distinctive string, so there is no reason to write a realistic secret here.
FAKE_CURSOR_KEY = "cursor-test-value-do-not-leak"


def test_cursor_key_status_never_returns_secret(monkeypatch) -> None:
    monkeypatch.setenv("CURSOR_API_KEY", FAKE_CURSOR_KEY)
    st = openvault.cursor_key_status()
    assert st["configured"] is True
    assert st["chars"] == len(FAKE_CURSOR_KEY)
    assert st["model"] == "grok-4.6"
    assert FAKE_CURSOR_KEY not in str(st)



def test_load_settings_honors_computer_control_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CORTEX_COMPUTER_CONTROL", "1")
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "crew"))
    monkeypatch.setenv("CREW_OPENVAULT", "0")
    settings = config.load_settings()
    assert settings.master_computer_control is True
    monkeypatch.delenv("CORTEX_COMPUTER_CONTROL", raising=False)
    settings = config.load_settings()
    assert settings.master_computer_control is False


def test_ingest_csv_drop_refuses_when_vault_off(monkeypatch, tmp_path) -> None:
    from CortexOS.crew.openvault import ingest_csv_drop

    monkeypatch.setenv("CREW_OPENVAULT", "0")
    csv_path = tmp_path / "vault.csv"
    csv_path.write_text("env_key,secret\nCURSOR_API_KEY,super-secret\n", encoding="utf-8")
    result = ingest_csv_drop(csv_path)
    assert result["ok"] is False
    assert "CREW_OPENVAULT" in str(result.get("detail"))
    assert "super-secret" not in str(result)


def test_require_live_refuses_when_disabled(monkeypatch) -> None:
    monkeypatch.setenv("CREW_OPENVAULT", "0")
    with pytest.raises(openvault.LLMError, match="no silent fallback"):
        openvault.require_live()
