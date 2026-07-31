# Netie KB — installed 2026-07-31

**Repo:** `D:\Netie-KB` (git initialized; add remote and push when ready)

## Paste Cursor User Rules

Open in Explorer or any editor:

`D:\Netie-KB\generated\USER_RULES_PASTE.md`

Destination: Cursor → Settings → Rules → **User Rules**

Always-apply rule synced to: `C:\Users\OoiJianHong\.cursor\rules\netie-kb.mdc`

## Claude Code globals

Synced to: `C:\Users\OoiJianHong\.claude\CLAUDE.md` (generated — edit `D:\Netie-KB\rules\`)

## Junctions

- `C:\Users\OoiJianHong\.claude\kb` → `D:\Netie-KB`
- `C:\Users\OoiJianHong\.cursor\kb` → `D:\Netie-KB`

## Commands

```bash
cd D:\Netie-KB
python scripts/kb.py search "manifest escape"
python scripts/kb.py validate
python scripts/kb.py index && python scripts/kb.py render
python scripts/sync_agents.py
```

## Corpus

| Kind | Count |
|------|-------|
| Rules R-0001..R-0011 | 11 |
| Workflows W-0001..W-0004 | 4 |
| Attacks A-0001..A-0004 | 4 |

Block D pointers appended to `D:\Cortex\CLAUDE.md` and `D:\DMS\CLAUDE.md`.

KB-4 (mine `~\.claude\tasks\`) — not run yet.
