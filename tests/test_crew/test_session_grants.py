"""EPIC-GRANT-01 (#202): session grant catalog store over GET/POST /crew/session-grants.

Every test asserts on the HTTP status and JSON the operator receives, then on
the store the next GET reads. A refusal must leave the store byte-identical.
Nothing here reaches the laptop: the book is a catalog, not a jail.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from CortexOS.crew.server import create_app
from CortexOS.crew.session_grants import (
    OFFICE_EXTENSIONS,
    GrantRefused,
    SessionGrantBook,
    normalise_folder,
)
from tests.test_crew.conftest import FakeLLM

PROFILE_WIN = r"C:\Users\ops"
PROFILE_POSIX = "/home/ops"
DOCS = r"C:\Users\ops\Documents"
URL = "/crew/session-grants"


@pytest.fixture()
def client(settings, crew_env, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    monkeypatch.setenv("USERPROFILE", PROFILE_WIN)
    monkeypatch.setenv("HOME", PROFILE_POSIX)
    app = create_app(settings, llm_chat=FakeLLM())
    with TestClient(app) as tc:
        store_path: Path = settings.data_dir / "session_grants.json"
        yield SimpleNamespace(http=tc, crew=app.state.crew, store_path=store_path)


def _grants(client: SimpleNamespace, session_id: str) -> list[dict]:
    resp = client.http.get(URL, params={"session_id": session_id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["session_id"] == session_id
    assert body["reach"] is False
    return body["grants"]


def _store_bytes(client: SimpleNamespace) -> bytes:
    return client.store_path.read_bytes() if client.store_path.is_file() else b""


# ---- folder -------------------------------------------------------------------


def test_folder_allow_is_session_only_by_default(client) -> None:
    resp = client.http.post(URL, json={"session_id": "s1", "kind": "folder", "path": DOCS})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["grant"]["kind"] == "folder"
    assert body["grant"]["decision"] == "allow"
    assert body["grant"]["persist"] is False
    assert body["grant"]["scope"] == "session"
    assert body["grant"]["path"] == DOCS
    assert body["count"] == 1
    assert _grants(client, "s1")[0]["path"] == DOCS
    # session-only never touches the persisted file
    assert not client.store_path.exists()
    # a fresh process sees nothing
    assert SessionGrantBook(client.store_path).grants_for("s1") == []


def test_folder_persist_survives_a_new_book(client) -> None:
    resp = client.http.post(
        URL, json={"session_id": "s1", "kind": "folder", "path": DOCS, "persist": True}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["grant"]["scope"] == "persisted"
    saved = json.loads(client.store_path.read_text(encoding="utf-8"))
    assert [r["path"] for r in saved["sessions"]["s1"]] == [DOCS]
    again = SessionGrantBook(client.store_path).grants_for("s1")
    assert [r["path"] for r in again] == [DOCS]
    assert again[0]["persist"] is True


def test_folder_cancel_records_refusal_without_granting(client) -> None:
    resp = client.http.post(
        URL, json={"session_id": "s1", "kind": "folder", "path": DOCS, "decision": "cancel"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["grant"]["decision"] == "cancel"
    rows = _grants(client, "s1")
    assert [(r["kind"], r["decision"]) for r in rows] == [("folder", "cancel")]
    # a cancelled folder does not admit office files
    resp = client.http.post(
        URL, json={"session_id": "s1", "kind": "office_file", "path": DOCS + r"\q3.xlsx"}
    )
    assert resp.status_code == 403
    assert "outside every folder" in resp.json()["detail"]
    assert len(_grants(client, "s1")) == 1


def test_later_decision_replaces_earlier_for_same_folder(client) -> None:
    client.http.post(URL, json={"session_id": "s1", "kind": "folder", "path": DOCS})
    client.http.post(
        URL, json={"session_id": "s1", "kind": "folder", "path": DOCS.lower(), "decision": "cancel"}
    )
    rows = _grants(client, "s1")
    assert len(rows) == 1
    assert rows[0]["decision"] == "cancel"


@pytest.mark.parametrize(
    "path",
    [
        r"C:\\",
        "C:/",
        "C:",
        "D:\\",
        "/",
        "//",
        PROFILE_WIN,
        PROFILE_WIN.lower() + "\\",
        "C:/Users/ops",
        PROFILE_POSIX,
        PROFILE_POSIX + "/",
        r"C:\Users\ops\Documents\..\..",
        r"C:\Users\ops\Documents\..",
        "/home/ops/docs/../..",
        r"\\fileserver\share",
        r"\\fileserver\share\\",
        r"\\fileserver",
        r"\\?\C:\Users\ops\Documents",
        "//?/C:/",
        r"\\.\PhysicalDrive0",
        "",
        "   ",
        "Documents",
        "C:Documents",
        "home/ops/docs",
    ],
)
def test_folder_refusals_leave_store_unchanged(client, path: str) -> None:
    client.http.post(URL, json={"session_id": "s1", "kind": "folder", "path": DOCS, "persist": True})
    before_file = _store_bytes(client)
    before_rows = _grants(client, "s1")
    for persist in (False, True):
        resp = client.http.post(
            URL, json={"session_id": "s1", "kind": "folder", "path": path, "persist": persist}
        )
        assert 400 <= resp.status_code < 500, (path, resp.text)
        assert resp.json()["detail"], path
    assert _grants(client, "s1") == before_rows
    assert _store_bytes(client) == before_file


def test_folder_under_profile_is_allowed_on_both_styles(client) -> None:
    for path, expect in (
        (r"C:\Users\ops\Documents\Q3", r"C:\Users\ops\Documents\Q3"),
        ("C:/Users/ops/Desktop/", r"C:\Users\ops\Desktop"),
        ("/home/ops/docs/", "/home/ops/docs"),
        (r"\\fileserver\share\finance", r"\\fileserver\share\finance"),
    ):
        resp = client.http.post(URL, json={"session_id": "s1", "kind": "folder", "path": path})
        assert resp.status_code == 200, (path, resp.text)
        assert resp.json()["grant"]["path"] == expect


def test_profile_root_is_injectable_for_tests(tmp_path: Path) -> None:
    book = SessionGrantBook(None, profile_roots=[r"D:\Profiles\me"])
    with pytest.raises(GrantRefused):
        book.record("s1", kind="folder", path=r"D:\Profiles\me")
    assert book.record("s1", kind="folder", path=r"C:\Users\me")["path"] == r"C:\Users\me"
    assert normalise_folder("/srv/data", profile_roots=[]) == "/srv/data"


# ---- window -------------------------------------------------------------------


def test_window_allow_needs_title_and_positive_pid(client) -> None:
    resp = client.http.post(
        URL, json={"session_id": "s1", "kind": "window", "title": "Book1 - Excel", "pid": 4242}
    )
    assert resp.status_code == 200, resp.text
    grant = resp.json()["grant"]
    assert grant["title"] == "Book1 - Excel"
    assert grant["pid"] == 4242
    assert grant["scope"] == "session"
    before = _grants(client, "s1")
    for body in (
        {"title": "", "pid": 4242},
        {"title": "   ", "pid": 4242},
        {"title": "Book1", "pid": 0},
        {"title": "Book1", "pid": -7},
        {"title": "Book1"},
        {"pid": 4242},
    ):
        resp = client.http.post(URL, json={"session_id": "s1", "kind": "window", **body})
        assert resp.status_code == 400, (body, resp.text)
    assert _grants(client, "s1") == before


def test_window_persist_is_refused(client) -> None:
    resp = client.http.post(
        URL,
        json={"session_id": "s1", "kind": "window", "title": "Book1", "pid": 9, "persist": True},
    )
    assert resp.status_code == 400
    assert "persist is refused" in resp.json()["detail"]
    assert _grants(client, "s1") == []
    assert not client.store_path.exists()


# ---- office file --------------------------------------------------------------


def test_office_file_under_granted_folder(client) -> None:
    client.http.post(URL, json={"session_id": "s1", "kind": "folder", "path": DOCS})
    resp = client.http.post(
        URL, json={"session_id": "s1", "kind": "office_file", "path": DOCS + r"\2026\Q3.XLSX"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["grant"]["path"] == DOCS + r"\2026\Q3.XLSX"
    kinds = [r["kind"] for r in _grants(client, "s1")]
    assert kinds == ["folder", "office_file"]


def test_office_file_under_persisted_folder_counts(client) -> None:
    client.http.post(
        URL, json={"session_id": "s1", "kind": "folder", "path": "/home/ops/docs", "persist": True}
    )
    resp = client.http.post(
        URL, json={"session_id": "s1", "kind": "office_file", "path": "/home/ops/docs/plan.docx"}
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize(
    "path",
    [
        r"C:\Users\ops\Other\Q3.xlsx",  # outside the granted folder
        r"C:\Users\ops\Documents.xlsx",  # sibling that shares a prefix
        r"C:\Users\ops\Documents\..\secret.xlsx",  # traversal out
        r"C:\Users\ops\Documents\tool.exe",  # not an Office file
        r"C:\Users\ops\Documents\notes.txt",
        r"C:\Users\ops\Documents",  # the folder itself is not a file
        r"C:\Q3.xlsx",
        "",
    ],
)
def test_office_file_refusals_leave_store_unchanged(client, path: str) -> None:
    client.http.post(URL, json={"session_id": "s1", "kind": "folder", "path": DOCS, "persist": True})
    before_rows = _grants(client, "s1")
    before_file = _store_bytes(client)
    resp = client.http.post(URL, json={"session_id": "s1", "kind": "office_file", "path": path})
    assert 400 <= resp.status_code < 500, (path, resp.text)
    assert _grants(client, "s1") == before_rows
    assert _store_bytes(client) == before_file


def test_office_file_needs_folder_from_the_same_session(client) -> None:
    client.http.post(URL, json={"session_id": "s1", "kind": "folder", "path": DOCS, "persist": True})
    resp = client.http.post(
        URL, json={"session_id": "s2", "kind": "office_file", "path": DOCS + r"\Q3.xlsx"}
    )
    assert resp.status_code == 403
    assert _grants(client, "s2") == []


def test_office_extension_allowlist_is_documented() -> None:
    assert OFFICE_EXTENSIONS == {".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt", ".csv"}


# ---- listing / isolation / shape ---------------------------------------------


def test_get_does_not_leak_across_sessions(client) -> None:
    client.http.post(URL, json={"session_id": "s1", "kind": "folder", "path": DOCS})
    client.http.post(
        URL, json={"session_id": "s1", "kind": "folder", "path": "/home/ops/x", "persist": True}
    )
    client.http.post(URL, json={"session_id": "s2", "kind": "window", "title": "Word", "pid": 3})
    s1 = _grants(client, "s1")
    s2 = _grants(client, "s2")
    assert {r["session_id"] for r in s1} == {"s1"}
    assert {r["session_id"] for r in s2} == {"s2"}
    assert sorted(r["path"] for r in s1) == sorted([DOCS, "/home/ops/x"])
    assert [r["kind"] for r in s2] == ["window"]
    assert _grants(client, "s3") == []


def test_get_without_session_id_is_400(client) -> None:
    resp = client.http.get(URL)
    assert resp.status_code == 400
    assert "session_id" in resp.json()["detail"]
    resp = client.http.get(URL, params={"session_id": "../etc"})
    assert resp.status_code == 400


def test_unknown_kind_and_decision_are_400(client) -> None:
    resp = client.http.post(URL, json={"session_id": "s1", "kind": "all_apps", "path": DOCS})
    assert resp.status_code == 400
    assert "catalog" in resp.json()["detail"]
    resp = client.http.post(
        URL, json={"session_id": "s1", "kind": "folder", "path": DOCS, "decision": "maybe"}
    )
    assert resp.status_code == 400
    assert _grants(client, "s1") == []


def test_stale_persisted_file_cannot_smuggle_windows_or_junk(tmp_path: Path) -> None:
    path = tmp_path / "session_grants.json"
    path.write_text(
        json.dumps(
            {
                "sessions": {
                    "s1": [
                        {"id": "a", "kind": "window", "decision": "allow", "identity": "w",
                         "path": "", "created_at": "x"},
                        {"id": "b", "kind": "shell", "decision": "allow", "identity": "s",
                         "path": r"C:\\", "created_at": "x"},
                        {"id": "c", "kind": "folder", "decision": "allow",
                         "identity": "folder:c:\\users\\ops\\documents", "path": DOCS,
                         "created_at": "x"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    rows = SessionGrantBook(path).grants_for("s1")
    assert [r["id"] for r in rows] == ["c"]
