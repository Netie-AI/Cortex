from __future__ import annotations

from CortexOS.crew.board import list_skills, snapshot


def test_snapshot_reads_claims_and_names_the_law(tmp_path, monkeypatch) -> None:
    claims = tmp_path / "CLAIMS.json"
    claims.write_text(
        '{"tickets":[{"ticket":"ANS-01","role":"SEATED","may_write":true,"owner_pr":"x#48"}]}',
        encoding="utf-8",
    )
    runtime = tmp_path / "RUNTIME.md"
    runtime.write_text("# GATE PASS\nTicket Runner seats existing writers.\n", encoding="utf-8")
    monkeypatch.setenv("CREW_CLAIMS", str(claims))
    monkeypatch.setenv("CREW_RUNTIME", str(runtime))
    board = snapshot()
    assert board["seated"] == 1
    assert board["unseated"] == 0
    assert board["tickets"][0]["ticket"] == "ANS-01"
    assert "cloud agent" in board["law"]
    assert "GATE PASS" in board["runtime_head"]


def test_list_skills_reads_markdown(tmp_path) -> None:
    folder = tmp_path / "skills"
    folder.mkdir()
    (folder / "brief.md").write_text("Goal: Monday check.\n", encoding="utf-8")
    rows = list_skills(folder)
    assert rows == [{"title": "brief", "path": "brief.md", "head": "Goal: Monday check."}]


def test_default_tone_is_ascii_grok_bot(tmp_path) -> None:
    from CortexOS.crew.board import DEFAULT_TONE, ensure_default_tone, read_skill

    path = ensure_default_tone(tmp_path / "skills")
    assert path.name == "tone.md"
    body = read_skill(tmp_path / "skills", "tone")
    assert "ASCII" in body
    assert "cloud swarm" in body
    assert DEFAULT_TONE in body


def test_skill_packs_seed_without_overwrite(tmp_path) -> None:
    from CortexOS.crew.board import ensure_skill_packs, read_skill

    folder = tmp_path / "skills"
    written = ensure_skill_packs(folder)
    names = {p.name for p in written}
    assert "outreach.md" in names
    assert "chat-human.md" in names
    assert "ship.md" in names
    assert "security.md" in names
    assert "ontology-foundry.md" in names
    assert "skill-route.md" in names
    assert "build.md" in names
    assert (folder / "tone.md").exists()
    body = read_skill(folder, "outreach")
    assert "Jian Hong" in body
    (folder / "outreach.md").write_text("LOCAL OVERRIDE\n", encoding="utf-8")
    ensure_skill_packs(folder)
    assert read_skill(folder, "outreach") == "LOCAL OVERRIDE\n"


def test_read_skill_falls_back_to_shipped_packs(tmp_path) -> None:
    from CortexOS.crew.board import read_skill

    empty = tmp_path / "missing-skills"
    body = read_skill(empty, "decide")
    assert "Human is money" in body


def test_ontology_foundry_pack_is_netie_and_routes_before_build() -> None:
    from CortexOS.crew.board import PACKS_DIR, read_skill

    foundry = read_skill(PACKS_DIR, "ontology-foundry")
    route = read_skill(PACKS_DIR, "skill-route")
    build = read_skill(PACKS_DIR, "build")
    assert "Palantir-shaped" in foundry
    assert "PARKING_LOT P1" in foundry
    assert "Approvals stay manual" in foundry
    assert "Do not auto-approve" in foundry
    assert ":8020" in foundry
    assert "LangGraph" in foundry
    assert "mybot" not in foundry.lower()
    assert "myclaude-code" not in foundry.lower()
    assert "guaca" not in foundry.lower()
    assert "You are a helpful assistant" not in foundry
    assert "ontology-foundry then build" in route
    foundry_at = build.lower().find("ontology-foundry")
    this_pack_at = build.lower().find("then this pack")
    assert foundry_at != -1 and this_pack_at != -1 and foundry_at < this_pack_at
