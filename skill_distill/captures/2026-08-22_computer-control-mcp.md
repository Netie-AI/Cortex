---
id: 2026-08-22_computer-control-mcp
source: cursor
date: 2026-08-22
operator: cloud-agent
prompt_used: skill_distill/prompts/ASK_CURSOR.md
distill_trace: skill_distill/DISTILL.md
status: normalized
---

# Computer-control MCP is a sidecar, not an in-process driver

User asked Cortex to confirm Computer Control MCP servers (mcp.so
computer-control via PyAutoGUI/RapidOCR/ONNX, mcpmarket, CursorTouch
Windows-MCP), `pip install uacc`, and to drive a Constructor-style agent
desk that orchestrates every repo.

## Raw answer

Observed on this Cloud Agent (Linux, 2026-08-22):

- `uacc` exists on PyPI (Universal AI Computer Control). Dependencies include
  pyautogui, pynput, pywinauto, easyocr/torch. `pywinauto` is Windows-only.
  Installing it on this Linux VM is the wrong fix.
- computer-control-mcp is AB498/computer-control-mcp (the mcp.so / mcpmarket
  listing). Same class of driver: mouse, keyboard, OCR.
- CursorTouch/Windows-MCP is a Windows desktop MCP. This host is not Windows;
  it cannot attach.
- Cortex already has architecture preset `computer_control` mapping to
  ontology actions, not a live input driver.
- Pointer / Netie Clicks is the Act/computer-control *client* role.
- Third-party MCP *clients* stay P16. First-party probe + gated invoke is OK.
- Constructor-style sidebar (Verify, DevOps, UX, Pointer, Ticket, PR, PRD,
  SEO, Constructor) is an operator desk over `dispatch()`, not a second
  orchestrator. Money Gainer / Outlier sign-in is not an engine feature.

## Extracted facts

| Fact | Evidence | Confidence | Promote |
|------|----------|------------|---------|
| Computer control MCP servers are catalogued; Cortex probes, does not auto-install | observed | high | rule |
| Default OFF; CORTEX_COMPUTER_CONTROL=1 arms; EXECUTE=1 still needs a sidecar | observed | high | rule |
| Windows-MCP cannot run on Linux cloud agents | observed | high | parking |
| In-process UACC/PyAutoGUI execute is not wired; uacc-mcp sidecar is the path | inferred | high | parking |
| Constructor Agent UI is GET /api/connectors over the existing dispatch port | observed | high | rule |
| Scraping sibling repos / Outlier is out of scope | docs | high | parking |

## Action YAML

```yaml
build_now:
  - CortexOS/connectors/computer_control.py probe + fail-closed invoke
  - Constructor roster + GET /api/connectors Slack-like desk
  - MCP tools computer_control.status (read-only) and computer_control.invoke (gated)
  - discovery refs cortex_mcp.json (uacc, computer-control-mcp, Windows-MCP)
park:
  - pip install uacc on Linux cloud VMs
  - in-process mouse/keyboard
  - Windows-MCP attach from Linux
  - Money Gainer / Outlier scraping
tests:
  - probe default not armed
  - invoke without flag fails
  - find_mcp hits uacc/windows-mcp/computer-control-mcp
  - UI contains Constructor Agent
```

## Netie implications

- Build now: probe + Constructor desk + catalog. Keep fail-closed.
- Park (condition): a Windows sidecar running `uacc-mcp` or Windows-MCP when a desktop exists.
- Tests required: default-off probe, HTTP 403 on invoke, discovery hits.

## Citations

- distill: skill_distill/captures/2026-08-22_computer-control-mcp.md
- distill: skill_distill/captures/2026-08-22_cursor_orchestration-outside-editor.md
