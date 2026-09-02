"""Named skill ingest: search then save, even when the model hop is dark."""

from __future__ import annotations

from CortexOS.crew.ingest import classify_labels, extract_skill_name, ingest_named_skill


def test_extract_skill_name_from_add_impeccable() -> None:
    assert extract_skill_name("Add impeccable skill into skill storage") == "impeccable"


def test_classify_labels_uses_what_it_is_for() -> None:
    assert "design-rules" in classify_labels("A design ruleset for UI.", name="impeccable")


def test_ingest_named_skill_saves_pbakaus_from_search(tmp_path, monkeypatch) -> None:
    from CortexOS.crew import research

    monkeypatch.setattr(
        research,
        "github_search",
        lambda query, limit=8: {
            "ok": True,
            "repos": [
                {
                    "name": "impeccable",
                    "full_name": "pbakaus/impeccable",
                    "url": "https://github.com/pbakaus/impeccable",
                    "description": "Design rules",
                }
            ],
            "web": [],
        },
    )
    monkeypatch.setattr(
        research,
        "web_search",
        lambda query, max_results=6: {"ok": True, "results": []},
    )
    monkeypatch.setattr(
        research,
        "web_fetch",
        lambda url, max_chars=8000: {
            "ok": True,
            "url": url,
            "text": "Impeccable is a design skill. Contrast first.",
        },
    )
    folder = tmp_path / "skills"
    out = ingest_named_skill("Add impeccable skill into skill storage", folder)
    assert out["ok"] is True
    assert out["name"] == "impeccable"
    assert "pbakaus/impeccable" in out["source"]
    assert "design-rules" in out["labels"]
    path = folder / "impeccable.md"
    assert path.is_file()
    assert "design-rules" in path.read_text(encoding="utf-8")


def test_ingest_skips_nonpublic_search_hit_and_uses_the_public_one(tmp_path, monkeypatch) -> None:
    from CortexOS.crew import research

    fetched: list[str] = []

    def capture_fetch(url, max_chars=8000):
        fetched.append(url)
        return {
            "ok": True,
            "url": url,
            "text": "Impeccable is a design skill. Contrast first.",
        }

    monkeypatch.setattr(
        research,
        "github_search",
        lambda query, limit=8: {
            "ok": True,
            "repos": [
                {
                    "name": "impeccable",
                    "full_name": "local/impeccable",
                    "url": "http://127.0.0.1/impeccable",
                    "description": "impeccable",
                },
                {
                    "name": "impeccable",
                    "full_name": "pbakaus/impeccable",
                    "url": "https://github.com/pbakaus/impeccable",
                    "description": "Design rules",
                },
            ],
            "web": [],
        },
    )
    monkeypatch.setattr(
        research,
        "web_search",
        lambda query, max_results=6: {"ok": True, "results": []},
    )
    monkeypatch.setattr(research, "web_fetch", capture_fetch)
    folder = tmp_path / "skills"
    out = ingest_named_skill("Add impeccable skill into skill storage", folder)
    assert out["ok"] is True
    assert "pbakaus/impeccable" in out["source"]
    assert fetched
    assert all("127.0.0.1" not in url for url in fetched)
