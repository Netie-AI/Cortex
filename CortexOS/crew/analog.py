"""Analog-surface clone: tokens and layout DNA, not a dump of their assets."""

from __future__ import annotations

import re
from typing import Any

_URL = re.compile(r"https?://[^\s<>'\"]+", re.I)
_HEX = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")
_FONT = re.compile(r"font-family\s*:\s*([^;}{]+)", re.I)


def analog_ask(text: str) -> bool:
    low = (text or "").lower()
    cues = (
        "clone http",
        "clone https",
        "clone www",
        "clone a site",
        "clone the site",
        "clone this site",
        "make a website",
        "build a website",
        "make a site",
        "landing page",
        "3d website",
        "3d site",
    )
    return any(c in low for c in cues)


def extract_url(text: str) -> str:
    hit = _URL.search(text or "")
    if not hit:
        return ""
    return hit.group(0).rstrip(").,]")


def analog_clone(
    query: str,
    ws: Any,
    *,
    skills_dir: Any = None,
    open_browser: bool = True,
) -> dict[str, Any]:
    """Fetch analog, search free clone/design tools, write index.html in the space."""
    from CortexOS.crew import research
    from CortexOS.crew.workspace import WorkspaceError

    url = extract_url(query)
    if not url:
        return {"ok": False, "error": "need a public http(s) analog URL"}
    blocked = research.refuse_nonpublic_url(url)
    if blocked:
        return {"ok": False, "error": blocked, "url": url}

    page = research.web_fetch(url, max_chars=16000)
    if not page.get("ok"):
        return {
            "ok": False,
            "error": str(page.get("error") or "analog fetch failed")[:200],
            "url": url,
        }
    text = str(page.get("text") or "")
    title = str(page.get("title") or "")[:80]
    blob = f"{title}\n{text}"
    colors = _colors(blob)
    font = _font(blob)
    tools = research.github_search("clone-website goclone", limit=5)
    design = research.web_search("github topics design-md gsap threejs cdn", max_results=5)
    skill_note = _skill_note(skills_dir)
    html = _html(url, title, colors, font, skill_note)
    notes = _notes(url, title, colors, font, tools, design, skill_note, bool(page.get("ok")))
    try:
        ws.write("index.html", html)
        ws.write("ANALOG.md", notes)
    except WorkspaceError as exc:
        return {"ok": False, "error": str(exc), "url": url}
    opened = False
    if open_browser:
        try:
            opened = _open_local(ws.resolve("index.html"))
        except (OSError, ValueError, AttributeError):
            opened = False
    return {
        "ok": True,
        "url": url,
        "files": ["index.html", "ANALOG.md"],
        "colors": colors,
        "opened": opened,
        "ask": "Login/Meshy/Hunyuan/OpenVault: ask the operator. Do not open a new paid account.",
        "law": "Tokens and layout DNA only. Not their images or brand as ours.",
    }


def _colors(text: str) -> list[str]:
    seen: list[str] = []
    for raw in _HEX.findall(text or ""):
        hexv = raw.lower()
        if hexv in {"#fff", "#ffffff", "#000", "#000000"}:
            continue
        if hexv not in seen:
            seen.append(hexv)
        if len(seen) >= 4:
            break
    return seen or ["#0b0d10", "#e8e4d9", "#c4a574"]


def _font(text: str) -> str:
    hit = _FONT.search(text or "")
    if not hit:
        return "Georgia, 'Times New Roman', serif"
    fam = hit.group(1).strip().strip("\"'")
    return fam[:80] if fam else "Georgia, 'Times New Roman', serif"


def _skill_note(skills_dir: Any) -> str:
    if skills_dir is None:
        return ""
    path = skills_dir / "impeccable.md"
    try:
        if path.is_file():
            return "impeccable (design-rules) is on the roster; apply contrast and type hierarchy."
    except OSError:
        return ""
    return ""


def _tool_lines(blob: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for row in (blob.get("repos") or [])[:4]:
        if isinstance(row, dict) and row.get("url"):
            out.append(f"- {row.get('full_name') or row.get('name')}: {row.get('url')}")
    for row in (blob.get("web") or blob.get("results") or [])[:4]:
        if isinstance(row, dict) and row.get("url"):
            out.append(f"- {row.get('title') or row.get('url')}: {row.get('url')}")
    return out


def _notes(
    url: str,
    title: str,
    colors: list[str],
    font: str,
    tools: dict[str, Any],
    design: dict[str, Any],
    skill_note: str,
    fetched: bool,
) -> str:
    lines = [
        f"Analog of {url}",
        f"Title seen: {title or '(none)'}",
        f"Fetched: {fetched}",
        f"Source title kept as analog-of, not as our brand.",
        f"Colors: {', '.join(colors)}",
        f"Type: {font}",
        skill_note or "No impeccable skill on disk.",
        "",
        "Clone/design search (free first):",
        *(_tool_lines(tools) or ["- (no github hits)"]),
        *(_tool_lines(design) or []),
        "",
        "Did not copy their images, logo, or copy as ours.",
        "GSAP from cdnjs (free). No Meshy/Hunyuan until the operator logs in.",
        "Need verification: OpenVault/Meshy password stays with the operator.",
    ]
    return "\n".join(lines) + "\n"


def _esc(value: str) -> str:
    return (
        (value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _open_local(path: Any) -> bool:
    import threading
    import webbrowser

    try:
        uri = path.resolve().as_uri()
    except (OSError, ValueError):
        return False

    def _go() -> None:
        try:
            webbrowser.open(uri)
        except (OSError, ValueError):
            return

    threading.Thread(target=_go, daemon=True).start()
    return True


def _html(url: str, title: str, colors: list[str], font: str, skill_note: str) -> str:
    bg = _esc(colors[0])
    fg = _esc(colors[1] if len(colors) > 1 else "#f4f0e6")
    accent = _esc(colors[2] if len(colors) > 2 else "#c4a574")
    host = _esc(url.split("/")[2] if "://" in url else url)
    seen = _esc(title or host)
    typeface = _esc(font)
    note = _esc(skill_note)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Analog of {host}</title>
  <meta name="analog-of" content="{seen}" />
  <script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js"></script>
  <style>
    :root {{ --bg: {bg}; --fg: {fg}; --accent: {accent}; }}
    * {{ box-sizing: border-box; }}
    html, body {{ margin: 0; min-height: 100%; background: var(--bg); color: var(--fg);
      font-family: {typeface}; }}
    .stage {{ min-height: 100vh; display: grid; place-items: center; padding: 8vh 6vw;
      background: radial-gradient(1200px 600px at 70% 20%, color-mix(in srgb, var(--accent) 22%, transparent), transparent); }}
    h1 {{ font-size: clamp(3rem, 9vw, 8rem); line-height: 0.9; font-weight: 500; margin: 0; }}
    p {{ max-width: 36rem; font-size: 1.05rem; opacity: 0.86; }}
    a {{ color: var(--accent); }}
    .mark {{ letter-spacing: 0.28em; text-transform: uppercase; font-size: 0.72rem; color: var(--accent); }}
    button {{ background: transparent; color: var(--fg); border: 1px solid var(--accent);
      padding: 0.7rem 1.2rem; cursor: pointer; }}
    button:hover {{ background: var(--accent); color: var(--bg); }}
  </style>
</head>
<body>
  <main class="stage">
    <div>
      <div class="mark">analog</div>
      <h1 id="hero">Quiet volume.</h1>
      <p>Recreation of public layout DNA from {host}. Not their brand, images, or copy.
      {note}</p>
      <p>Motion via free GSAP CDN. 3D/Meshy waits on operator login.</p>
      <button type="button" id="go">Hover me</button>
    </div>
  </main>
  <script>
    if (window.gsap) {{
      gsap.from("#hero", {{ y: 40, opacity: 0, duration: 1.1, ease: "power3.out" }});
    }}
    document.getElementById("go").addEventListener("mouseenter", function (e) {{
      if (window.gsap) gsap.to(e.currentTarget, {{ scale: 1.04, duration: 0.2 }});
    }});
  </script>
</body>
</html>
"""
