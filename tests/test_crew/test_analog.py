"""Analog clone: tokens/layout DNA in the space jail, even when the model hop is dark."""

from __future__ import annotations

from CortexOS.crew.analog import analog_ask, analog_clone, extract_url
from CortexOS.crew.workspace import workspace_for


def test_analog_ask_clone_url_without_the_word_design() -> None:
    assert analog_ask("clone https://www.igloo.inc/")
    assert analog_ask("make a website")
    assert not analog_ask("Add impeccable skill into skill storage")


def test_extract_url_from_clone_ask() -> None:
    assert extract_url("clone https://www.igloo.inc/") == "https://www.igloo.inc/"


def test_analog_clone_writes_tokens_not_their_assets(tmp_path, monkeypatch) -> None:
    from CortexOS.crew import analog as analog_mod
    from CortexOS.crew import research

    monkeypatch.setattr(
        research,
        "web_fetch",
        lambda url, max_chars=16000: {
            "ok": True,
            "url": url,
            "title": "Igloo",
            "text": (
                "font-family: Georgia, serif; color: #1a1714; background: #0b0d10;"
                "<script>alert(1)</script><iframe src='https://igloo.inc/cdn'></iframe>"
            ),
        },
    )
    monkeypatch.setattr(
        research,
        "github_search",
        lambda query, limit=5: {
            "ok": True,
            "repos": [
                {
                    "name": "goclone",
                    "full_name": "imthaghost/goclone",
                    "url": "https://github.com/imthaghost/goclone",
                }
            ],
            "web": [],
        },
    )
    monkeypatch.setattr(
        research,
        "web_search",
        lambda query, max_results=5: {
            "ok": True,
            "results": [{"title": "GSAP CDN", "url": "https://cdnjs.com/libraries/gsap"}],
        },
    )
    monkeypatch.setattr(analog_mod, "_open_local", lambda _p: False)

    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "impeccable.md").write_text("design-rules", encoding="utf-8")
    ws = workspace_for(tmp_path, "space1")
    out = analog_clone(
        "clone https://www.igloo.inc/",
        ws,
        skills_dir=skills,
        open_browser=False,
    )
    assert out["ok"] is True
    assert "text" not in out
    assert "html" not in out
    html = (tmp_path / "spaces" / "space1" / "ws" / "index.html").read_text(encoding="utf-8")
    notes = (tmp_path / "spaces" / "space1" / "ws" / "ANALOG.md").read_text(encoding="utf-8")
    assert "Analog of www.igloo.inc" in html
    assert "cdnjs.cloudflare.com/ajax/libs/gsap" in html
    assert "igloo.inc/cdn" not in html
    assert "<iframe" not in html
    assert "#1a1714" in html or "#0b0d10" in html
    assert "impeccable" in notes
    assert "imthaghost/goclone" in notes
    assert "Did not copy their images" in notes
    assert "<script>alert(1)</script>" not in notes
    assert "<script>alert(1)</script>" not in html


def test_analog_clone_refuses_loopback_and_does_not_write(tmp_path, monkeypatch) -> None:
    from CortexOS.crew import analog as analog_mod
    from CortexOS.crew import research

    def boom(*_a, **_k):
        raise AssertionError("loopback must not be fetched")

    monkeypatch.setattr(research, "web_fetch", boom)
    monkeypatch.setattr(analog_mod, "_open_local", lambda _p: True)
    ws = workspace_for(tmp_path, "space1")
    out = analog_clone(
        "clone http://127.0.0.1:5000/api/keys",
        ws,
        open_browser=True,
    )
    assert out["ok"] is False
    assert "not a public analog" in out["error"]
    assert not (tmp_path / "spaces" / "space1" / "ws" / "index.html").exists()


def test_analog_clone_refuses_cgnat_metadata_and_does_not_write(tmp_path, monkeypatch) -> None:
    from CortexOS.crew import analog as analog_mod
    from CortexOS.crew import research

    def boom(*_a, **_k):
        raise AssertionError("shared/CGNAT must not be fetched")

    monkeypatch.setattr(research, "web_fetch", boom)
    monkeypatch.setattr(analog_mod, "_open_local", lambda _p: True)
    ws = workspace_for(tmp_path, "space1")
    out = analog_clone(
        "clone http://100.100.100.200/latest/meta-data/",
        ws,
        open_browser=True,
    )
    assert out["ok"] is False
    assert "not a public analog" in out["error"]
    assert not (tmp_path / "spaces" / "space1" / "ws" / "index.html").exists()


def test_analog_clone_refuses_metadata_hostname_and_mdns(tmp_path, monkeypatch) -> None:
    from CortexOS.crew import analog as analog_mod
    from CortexOS.crew import research

    def boom(*_a, **_k):
        raise AssertionError("metadata and mDNS must not be fetched")

    monkeypatch.setattr(research, "web_fetch", boom)
    monkeypatch.setattr(analog_mod, "_open_local", lambda _p: True)
    ws = workspace_for(tmp_path, "space1")
    for url in ("http://instance-data/", "http://printer.local/"):
        out = analog_clone(f"clone {url}", ws, open_browser=True)
        assert out["ok"] is False, url
        assert "not a public analog" in out["error"], url
    assert not (tmp_path / "spaces" / "space1" / "ws" / "index.html").exists()


def test_analog_clone_refuses_abbrev_loopback_and_does_not_write(tmp_path, monkeypatch) -> None:
    from CortexOS.crew import analog as analog_mod
    from CortexOS.crew import research

    def boom(*_a, **_k):
        raise AssertionError("abbrev loopback must not be fetched")

    monkeypatch.setattr(research, "web_fetch", boom)
    monkeypatch.setattr(analog_mod, "_open_local", lambda _p: True)
    ws = workspace_for(tmp_path, "space1")
    out = analog_clone("clone http://127.1/", ws, open_browser=True)
    assert out["ok"] is False
    assert "not a public analog" in out["error"]
    assert not (tmp_path / "spaces" / "space1" / "ws" / "index.html").exists()


def test_analog_clone_refuses_ipv6_embedded_loopback_and_does_not_write(
    tmp_path, monkeypatch
) -> None:
    from CortexOS.crew import analog as analog_mod
    from CortexOS.crew import research

    def boom(*_a, **_k):
        raise AssertionError("ipv6-embedded loopback must not be fetched")

    monkeypatch.setattr(research, "web_fetch", boom)
    monkeypatch.setattr(analog_mod, "_open_local", lambda _p: True)
    ws = workspace_for(tmp_path, "space1")
    for url in (
        "http://[::127.0.0.1]/",
        "http://[::ffff:0:127.0.0.1]/",
        "http://[64:ff9b::127.0.0.1]/",
        "http://localhost6/",
    ):
        out = analog_clone(f"clone {url}", ws, open_browser=True)
        assert out["ok"] is False, url
        assert "not a public analog" in out["error"], url
    assert not (tmp_path / "spaces" / "space1" / "ws" / "index.html").exists()


def test_analog_clone_does_not_write_when_fetch_fails(tmp_path, monkeypatch) -> None:
    from CortexOS.crew import analog as analog_mod
    from CortexOS.crew import research

    monkeypatch.setattr(
        research,
        "web_fetch",
        lambda url, max_chars=16000: {"ok": False, "error": "timed out", "url": url},
    )
    monkeypatch.setattr(analog_mod, "_open_local", lambda _p: True)
    ws = workspace_for(tmp_path, "space1")
    out = analog_clone("clone https://www.igloo.inc/", ws, open_browser=True)
    assert out["ok"] is False
    assert "timed out" in out["error"]
    assert not (tmp_path / "spaces" / "space1" / "ws" / "index.html").exists()
