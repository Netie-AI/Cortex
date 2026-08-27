```yaml
id: 2026-08-23_cursor_uacc-playwright-reliability
source: cursor
date: 2026-08-23
operator: cursor-grok-4.6
prompt_used: skill_distill/prompts/ASK_CURSOR.md
distill_trace: skill_distill/DISTILL.md
status: raw
```

## Raw answer

Local UACC + Playwright MCP probe on the founder laptop (Win11, 13.7 GB RAM).

UACC `get_system_info`: CPU 77%, RAM 96.2% (0.5 GB free), 1920x1200. First JPEG screenshot of Cursor Agents succeeded. Follow-up `list_processes`, `query_knowledge`, `uacc_query(mode=fast)` all MCP-timed-out.

Playwright MCP was alive on `about:blank`. Editing `~/.cursor/mcp.json` to add `--config` kicked the server into "still loading".

Root causes, not symptoms:

1. Lag = RAM + default-heavy tools. UACC pip package depends on torch/easyocr; OCR is lazy unless `UACC_OCR_HEAVY=true`, but full PNG screenshots and `uacc_query(full)` still blow the MCP timeout. Playwright default `timeouts.settle=500ms` plus `imageResponses=auto` plus `npx @playwright/mcp@latest` on every spawn.
2. Signature gone = expired handles. Playwright action refs (`[ref=eN]`) are snapshot-scoped; after lag the page mutates and the ref is dead. UACC Set-of-Mark badges only exist when `screenshot(overlay="markers")`. A bare screenshot has no signatures.
3. Typing inaccurate = the wrong input path. UACC `type_text(human_like=false)` does **not** turn off `executor.human_mimicry`; config default `UACC_HUMAN_MIMICRY=true` still runs `type_human` (25-60ms/char pyautogui). Keys drop under CPU load. Unicode already falls back to clipboard; ASCII does not. Playwright `browser_type` default is `fill()` which skips per-key handlers (React/IME).

Do not add Windows-MCP. GROK_SYNC already forbids a second PyAutoGUI stack.

## Extracted facts

| Fact | Evidence | Confidence | Promote |
|------|----------|------------|---------|
| UACC type_text ignores human_like=false; mimicry stays on from env default | observed | high | skill |
| Clipboard paste is the accurate UACC type path under load | observed | high | skill |
| Playwright refs are the signatures; they die after navigate/wait/lag | docs | high | skill |
| Playwright imageResponses omit plus snapshot.boxes plus settle 200ms is the lag fix | docs | high | skill |
| UACC overlay=markers is the screenshot signature; default screenshot has none | docs | high | skill |
| Second OS-mouse MCP is forbidden next to UACC | docs | high | none |
| Lazy-load torch in pip uacc is still a site-packages change | inferred | med | parking |

## Action YAML

```yaml
action: uacc_playwright_reliability
route:
  web: playwright-mcp
  os: uacc
playwright:
  config: D:\Netie\Internal\Agents\playwright-mcp.config.json
  imageResponses: omit
  snapshot_boxes: true
  settle_ms: 200
  type: slowly-when-react
uacc:
  safe_mode: true
  human_mimicry: false
  ocr_heavy: false
  type: clipboard_write + ctrl+v
  screenshot: jpeg-region-or-markers
do_not: [windows-mcp, computer-control-mcp, vision-cap, uacc_query-full, type_text-under-load]
```

## Netie implications

- Build now: mcp.json env + playwright --config, skill `uacc-playwright`, Internal/Agents/UACC_PLAYWRIGHT.md, KB finding.
- Park: patching pip uacc site-packages to lazy-import torch (upstream). Condition: fork uacc or upstream PR.
- Tests: Playwright login-form fill after MCP reload; UACC screenshot JPEG does not timeout.

## Citations

- distill: skill_distill/captures/2026-08-23_cursor_uacc-playwright-reliability.md
