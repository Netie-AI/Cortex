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


def test_github_pr_diff_uses_injected_runner(monkeypatch) -> None:
    from CortexOS.crew import github as github_mod

    monkeypatch.setenv("CREW_LIVE_PROBES", "1")

    class Result:
        def __init__(self, code: int, stdout: str) -> None:
            self.returncode = code
            self.stdout = stdout
            self.stderr = ""

    def runner(argv, timeout=20):  # noqa: ANN001, ARG001
        assert "pr" in argv and "diff" in argv
        assert "12" in argv
        return Result(0, "+hello\n")

    out = github_mod.pr_diff(number=12, repo="acme/cortex", runner=runner)
    assert out["ok"] is True
    assert "+hello" in out["diff"]


def test_github_create_pr_uses_injected_runner(monkeypatch) -> None:
    from CortexOS.crew import github as github_mod

    monkeypatch.setenv("CREW_LIVE_PROBES", "1")

    class Result:
        def __init__(self, code: int, stdout: str) -> None:
            self.returncode = code
            self.stdout = stdout
            self.stderr = ""

    def runner(argv, timeout=20):  # noqa: ANN001, ARG001
        assert "pr" in argv and "create" in argv
        assert "--title" in argv
        return Result(0, "https://github.com/acme/cortex/pull/99\n")

    out = github_mod.create_pr(title="crew drop", runner=runner)
    assert out["ok"] is True
    assert "/pull/99" in out["url"]
    assert "auto-merge" in out["law"].lower()


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


def test_inbox_without_creds_tells_operator_to_drop(monkeypatch) -> None:
    from CortexOS.crew import inbox as inbox_mod

    monkeypatch.delenv("GMAIL_IMAP_USER", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    st = inbox_mod.status()
    assert st["connected"] is False
    assert "Drop .eml" in st["detail"]
    assert "never sends" in st["detail"].lower()


def test_inbox_fetch_timeout_is_opt_in(monkeypatch) -> None:
    from CortexOS.crew import inbox as inbox_mod

    seen: list[float] = []

    class Boom:
        def __init__(self, host, timeout=1.5):  # noqa: ARG002
            seen.append(timeout)
            raise OSError("skip")

    monkeypatch.setenv("GMAIL_IMAP_USER", "a@example.test")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "secret")
    monkeypatch.setattr(inbox_mod.imaplib, "IMAP4_SSL", Boom)
    out = inbox_mod.fetch(timeout=20.0)
    assert seen == [20.0]
    assert out["connected"] is False



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


def test_github_list_prs_fail_closes_hung_gh(monkeypatch) -> None:
    import subprocess
    import time

    from CortexOS.crew import github as github_mod

    monkeypatch.setenv("CREW_LIVE_PROBES", "1")
    monkeypatch.setenv("CREW_GH_REPOS", "acme/a,acme/b")
    seen: list[float] = []

    def boom(argv, timeout=20):  # noqa: ANN001, ARG001
        seen.append(timeout)
        raise subprocess.TimeoutExpired(cmd="gh", timeout=timeout)

    t0 = time.perf_counter()
    out = github_mod.list_prs(runner=boom)
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0
    assert seen
    assert all(t == github_mod.GH_WAIT_S for t in seen)
    assert github_mod.GH_WAIT_S <= 1.5


def test_github_list_prs_scheduled_timeout_is_opt_in(monkeypatch) -> None:
    import subprocess

    from CortexOS.crew import github as github_mod

    monkeypatch.setenv("CREW_LIVE_PROBES", "1")
    monkeypatch.setenv("CREW_GH_REPOS", "acme/a")
    seen: list[float] = []

    def capture(argv, timeout=20):  # noqa: ANN001, ARG001
        seen.append(timeout)
        raise subprocess.TimeoutExpired(cmd="gh", timeout=timeout)

    out = github_mod.list_prs(runner=capture, timeout=20.0)
    assert seen == [20.0]
    assert out["ok"] is False
    assert out["ok"] is False
    assert out["prs"] == []
    assert "TimeoutExpired" in (out.get("detail") or "")


def test_github_list_org_repos_fail_closes_hung_gh_in_parallel(monkeypatch) -> None:
    import subprocess
    import time

    from CortexOS.crew import github as github_mod

    monkeypatch.setenv("CREW_LIVE_PROBES", "1")
    monkeypatch.setenv("CREW_GH_OWNERS", "acme,beta")

    def boom(argv, timeout=20):  # noqa: ANN001, ARG001
        time.sleep(0.35)
        raise subprocess.TimeoutExpired(cmd="gh", timeout=timeout)

    t0 = time.perf_counter()
    out = github_mod.list_org_repos(runner=boom)
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.6
    assert github_mod.GH_WAIT_S <= 1.5
    assert out["ok"] is False
    assert out["repos"] == []
    assert "TimeoutExpired" in (out.get("detail") or "")


def test_desk_snapshot_uses_catalog_estate(monkeypatch) -> None:
    from CortexOS.crew import desk as desk_mod

    seen: dict[str, object] = {}

    def estate_snap(*, live=None, org=None):  # noqa: ANN001, ARG001
        seen["live"] = live
        return {"n": 0, "org": "Netie-AI"}

    monkeypatch.setattr(desk_mod.github, "list_prs", lambda: {"ok": True, "prs": [], "detail": ""})
    monkeypatch.setattr(
        desk_mod.inbox, "status", lambda: {"ok": True, "connected": False, "messages": [], "detail": "unset"}
    )
    monkeypatch.setattr(desk_mod.connectors, "catalog", lambda **_k: [])
    monkeypatch.setattr(desk_mod, "cursor_key_status", lambda: {"configured": False})
    monkeypatch.setattr(desk_mod.estate, "snapshot", estate_snap)
    snap = desk_mod.snapshot()
    assert seen["live"] is False
    assert snap["ok"] is True


def test_desk_snapshot_runs_peers_in_parallel(monkeypatch) -> None:
    import time

    from CortexOS.crew import desk as desk_mod

    def slow_prs() -> dict:
        time.sleep(0.35)
        return {"ok": False, "prs": [], "detail": "unread"}

    def slow_mail() -> dict:
        time.sleep(0.35)
        return {"ok": False, "connected": False, "messages": [], "detail": "unread"}

    monkeypatch.setattr(desk_mod.github, "list_prs", slow_prs)
    monkeypatch.setattr(desk_mod.inbox, "status", slow_mail)
    monkeypatch.setattr(desk_mod.connectors, "catalog", lambda **_k: [])
    monkeypatch.setattr(desk_mod, "cursor_key_status", lambda: {})
    monkeypatch.setattr(desk_mod.estate, "snapshot", lambda **_k: {})
    t0 = time.perf_counter()
    snap = desk_mod.snapshot()
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.6
    assert snap["ok"] is True
    assert snap["prs"]["ok"] is False
    assert "auto-merge" in snap["law"].lower() or "Do not auto-merge" in snap["law"]
