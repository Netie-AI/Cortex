from CortexOS.crew.connectors import catalog
from CortexOS.crew.import_chats import ingest, parse_export
from CortexOS.crew.routines import catalog as routines
from CortexOS.crew.store import CrewStore


def test_connector_catalog_names_netie_owners() -> None:
    rows = catalog(uacc_enabled=True, uacc_armed=True)
    slugs = {r["slug"] for r in rows}
    assert {"openvault", "cortex", "plane", "uacc", "gmail", "github", "cursor", "grok"} <= slugs
    grok = next(r for r in rows if r["slug"] == "grok")
    assert "OFFLOADED" in grok["layer"]
    assert grok["connected"] is False
    uacc = next(r for r in rows if r["slug"] == "uacc")
    assert uacc["connected"] is True
    cursor = next(r for r in rows if r["slug"] == "cursor")
    assert cursor["connected"] is True


def test_parse_markdown_and_rakazo_thread() -> None:
    md = "# user\nhello\n# assistant\nworld"
    turns = parse_export(md)
    assert turns == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]
    blob = '{"thread":[{"type":"user","text":"lock the offsite"},{"type":"bot","text":"holding dates"}]}'
    turns = parse_export(blob)
    assert turns[0]["role"] == "user"
    assert "offsite" in turns[0]["content"]
    assert turns[1]["role"] == "assistant"


def test_ingest_writes_visible_transcript(tmp_path) -> None:
    store = CrewStore(tmp_path / "crew.db")
    result = ingest(store, "Grok dump", "# user\nping\n# assistant\npong")
    assert result["count"] == 2
    msgs = store.list_messages(result["space"]["id"])
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[-1]["content"] == "pong"
    assert msgs[-1]["meta"]["imported"] is True


def test_search_finds_space_and_message(tmp_path) -> None:
    store = CrewStore(tmp_path / "crew.db")
    space = store.create_space("Offsite lock")
    store.add_message(space["id"], "user", "hold harbor house")
    hits = store.search("harbor")
    kinds = {h["kind"] for h in hits}
    assert "message" in kinds
    assert any("harbor" in h["snippet"] for h in hits)


def test_ingest_mail_makes_ticket_space(tmp_path) -> None:
    from CortexOS.crew.import_chats import ingest_mail

    store = CrewStore(tmp_path / "crew.db")
    raw = "From: ops@example.com\nSubject: Login broken on staging\n\nUsers cannot sign in."
    result = ingest_mail(store, raw, "issue.eml")
    assert "Login broken" in result["subject"]
    msgs = store.list_messages(result["space"]["id"])
    assert msgs[0]["role"] == "user"
    assert "cannot sign in" in msgs[0]["content"]
    assert msgs[1]["role"] == "system"
    assert "Human remains the sender" in msgs[1]["content"]


def test_parse_cursor_agent_jsonl() -> None:
    blob = (
        '{"role":"user","message":{"content":[{"type":"text","text":"lock the offsite"}]}}\n'
        '{"role":"assistant","message":{"content":[{"type":"text","text":"holding dates"}]}}\n'
    )
    turns = parse_export(blob)
    assert turns[0] == {"role": "user", "content": "lock the offsite"}
    assert turns[1]["role"] == "assistant"
    assert "holding dates" in turns[1]["content"]


def test_github_list_prs_uses_injected_runner(monkeypatch) -> None:
    from CortexOS.crew import github as github_mod

    monkeypatch.setenv("CREW_LIVE_PROBES", "1")
    monkeypatch.setenv("CREW_GH_REPOS", "acme/cortex")

    class Result:
        def __init__(self, code: int, stdout: str) -> None:
            self.returncode = code
            self.stdout = stdout
            self.stderr = ""

    def runner(argv, timeout=20):  # noqa: ANN001, ARG001
        assert "--repo" in argv and "acme/cortex" in argv
        return Result(0, '[{"number":12,"title":"crew drop","url":"https://x/12","headRefName":"feat","isDraft":false,"reviewDecision":"REVIEW_REQUIRED"}]')

    out = github_mod.list_prs(runner=runner)
    assert out["ok"] is True
    assert out["prs"][0]["number"] == 12
    assert "crew drop" in out["prs"][0]["title"]
    assert "auto-merge" in out["law"].lower() or "Do not auto-merge" in out["law"]


def test_github_list_org_repos_uses_injected_runner(monkeypatch) -> None:
    from CortexOS.crew import github as github_mod

    monkeypatch.setenv("CREW_LIVE_PROBES", "1")
    monkeypatch.setenv("CREW_GH_ORG", "Netie-AI")

    class Result:
        def __init__(self, code: int, stdout: str) -> None:
            self.returncode = code
            self.stdout = stdout
            self.stderr = ""

    def runner(argv, timeout=20):  # noqa: ANN001, ARG001
        assert "repo" in argv and "list" in argv and "Netie-AI" in argv
        return Result(
            0,
            '[{"name":"Cortex","description":"engine","isPrivate":true,'
            '"url":"https://github.com/Netie-AI/Cortex","updatedAt":"2026-08-25T00:00:00Z",'
            '"primaryLanguage":{"name":"Python"}}]',
        )

    out = github_mod.list_org_repos("Netie-AI", runner=runner)
    assert out["ok"] is True
    assert out["org"] == "Netie-AI"
    assert out["repos"][0]["name"] == "Cortex"
    assert out["repos"][0]["private"] is True
    assert "auto-merge" in out["law"].lower()


def test_github_close_issue_is_issue_close_not_merge(monkeypatch) -> None:
    from CortexOS.crew import github as github_mod

    monkeypatch.setenv("CREW_LIVE_PROBES", "1")

    class Result:
        def __init__(self) -> None:
            self.returncode = 0
            self.stdout = "Closed #99"
            self.stderr = ""

    seen: list[list[str]] = []

    def runner(argv, timeout=20):  # noqa: ANN001, ARG001
        seen.append(list(argv))
        return Result()

    empty = {
        "ok": True,
        "tickets": [],
    }
    monkeypatch.setattr(github_mod, "board_snapshot", lambda: empty)
    out = github_mod.close_issue(
        "https://github.com/Netie-AI/Cortex/issues/99",
        comment="verified",
        runner=runner,
    )
    assert out["ok"] is True
    assert out["spec"] == "Netie-AI/Cortex#99"
    assert "merge" not in out["law"].lower() or "Did not merge" in out["law"]
    assert seen[0][:4] == ["gh", "issue", "close", "99"]
    assert "--repo" in seen[0] and "Netie-AI/Cortex" in seen[0]
    assert "merge" not in seen[0]


def test_github_close_issue_refuses_seated_claim(tmp_path, monkeypatch) -> None:
    from CortexOS.crew import github as github_mod

    claims = tmp_path / "CLAIMS.json"
    claims.write_text(
        '{"tickets":[{"ticket":"Netie-AI/Cortex#128","owner_pr":"Netie-AI/Cortex#128","role":"SEATED"}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("CREW_CLAIMS", str(claims))
    called = {"n": 0}

    def runner(argv, timeout=20):  # noqa: ANN001, ARG001
        called["n"] += 1
        raise AssertionError("gh must not run for a SEATED ticket")

    out = github_mod.close_issue("Netie-AI/Cortex#128", runner=runner)
    assert out["ok"] is False
    assert "SEATED" in out["detail"]
    assert called["n"] == 0


def test_github_list_open_issues_marks_seated(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace

    from CortexOS.crew import github as github_mod

    claims = tmp_path / "CLAIMS.json"
    claims.write_text(
        '{"tickets":[{"ticket":"Netie-AI/Cortex#128","owner_pr":"Netie-AI/Cortex#128","role":"SEATED"}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("CREW_CLAIMS", str(claims))
    monkeypatch.setenv("CREW_LIVE_PROBES", "1")
    monkeypatch.setenv("CREW_GH_REPOS", "Netie-AI/Cortex")
    monkeypatch.setenv("CREW_GH_WAIT_S", "1.5")

    def runner(argv, timeout=1.5):  # noqa: ANN001, ARG001
        assert timeout <= 8.0
        assert argv[:3] == ["gh", "issue", "list"]
        payload = (
            '[{"number":128,"title":"seated","url":'
            '"https://github.com/Netie-AI/Cortex/issues/128"},'
            '{"number":160,"title":"assign","url":'
            '"https://github.com/Netie-AI/Cortex/issues/160"}]'
        )
        return SimpleNamespace(returncode=0, stdout=payload, stderr="")

    body = github_mod.list_open_issues(runner=runner)
    assert body["ok"] is True
    by_spec = {row["spec"]: row for row in body["issues"]}
    assert by_spec["Netie-AI/Cortex#128"]["seated"] is True
    assert by_spec["Netie-AI/Cortex#160"]["ready"] is True
    assert "does not assign" in body["law"].lower() or "Control does not assign" in body["law"]


def test_github_show_issue_is_read_only_and_marks_seated(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace

    from CortexOS.crew import github as github_mod

    claims = tmp_path / "CLAIMS.json"
    claims.write_text(
        '{"tickets":[{"ticket":"Netie-AI/Cortex#128","owner_pr":"Netie-AI/Cortex#128","role":"SEATED"}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("CREW_CLAIMS", str(claims))
    monkeypatch.setenv("CREW_LIVE_PROBES", "1")
    monkeypatch.setenv("CREW_GH_WAIT_S", "1.5")

    def runner(argv, timeout=1.5):  # noqa: ANN001, ARG001
        assert timeout <= 8.0
        assert argv[:3] == ["gh", "issue", "view"]
        assert "--json" in argv and "title,body,state" in argv
        payload = '{"title":"seated work","body":"Do C7-06.","state":"OPEN"}'
        return SimpleNamespace(returncode=0, stdout=payload, stderr="")

    seated = github_mod.show_issue("Netie-AI/Cortex#128", runner=runner)
    assert seated["ok"] is True
    assert seated["seated"] is True
    assert seated["ready"] is False
    assert "Do C7-06." in seated["body"]
    assert "SEATED" in seated["detail"]
    assert "assignees" in seated["law"].lower()

    def runner_ready(argv, timeout=1.5):  # noqa: ANN001, ARG001
        payload = '{"title":"assign slice","body":"Bind then execute.","state":"OPEN"}'
        return SimpleNamespace(returncode=0, stdout=payload, stderr="")

    ready = github_mod.show_issue("Netie-AI/Cortex#160", runner=runner_ready)
    assert ready["ok"] is True
    assert ready["ready"] is True
    assert ready["seated"] is False
    assert "Bind then execute." in ready["body"]
    monkeypatch.setenv("CREW_LIVE_PROBES", "0")
    dark = github_mod.show_issue("Netie-AI/Cortex#160", runner=runner_ready)
    assert dark["ok"] is False
    assert dark["detail"] == "CREW_LIVE_PROBES=0"


def test_inbox_without_creds_tells_operator_to_drop(monkeypatch) -> None:
    from CortexOS.crew import inbox as inbox_mod

    monkeypatch.delenv("GMAIL_IMAP_USER", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    st = inbox_mod.status()
    assert st["connected"] is False
    assert "Drop .eml" in st["detail"]
    assert "never sends" in st["detail"].lower()



def test_routines_keep_human_as_money_authority() -> None:
    names = {r["name"] for r in routines()}
    assert "NetieEstate24x7" in names
    assert "PR check" in names
    money = next(r for r in routines() if r["name"] == "Money / Decision")
    assert "Human is money" in money["instruction"]
    estate = next(r for r in routines() if r["name"] == "NetieEstate24x7")
    assert "Grok Bot" in estate["instruction"]


def test_discover_exports_skips_appdata(tmp_path) -> None:
    from CortexOS.crew.import_chats import discover_exports, ingest_readable

    drops = tmp_path / "drops"
    drops.mkdir()
    (drops / "chat.md").write_text("# user\nhi\n# assistant\nhey\nGoal:\nKeep it short.\n", encoding="utf-8")
    appdata = tmp_path / "AppData" / "Roaming" / "Grok"
    appdata.mkdir(parents=True)
    (appdata / "secret.json").write_text('{"messages":[{"role":"user","content":"nope"}]}', encoding="utf-8")
    hits = discover_exports([drops, appdata])
    assert any(h["name"] == "chat.md" for h in hits)
    assert not any("secret" in h["path"] for h in hits)
    store = CrewStore(tmp_path / "crew.db")
    skills = tmp_path / "skills"
    result = ingest_readable(store, [drops, appdata], skills_dir=skills)
    assert result["appdata_blocked"] is True
    assert result["files"] >= 1
    assert result["spaces"]
    msgs = store.list_messages(result["spaces"][0]["id"])
    assert any("hey" in m["content"] for m in msgs)
    assert (skills / "chat.md").exists()
