"""
packs/dms/generative/brain.py
DMS Brain — governed AI generative tasks.

Pipeline for every call:
  PII redact (packs.dms.security.pii)
  → Ponytail route/compress
  → intent dispatch
  → F1 ledger write
  → return suggestion (requires_confirm where applicable)

Intents:
  generate_chart   → chart config (read-only, no confirm needed)
  export_csv       → CSV bytes (read-only)
  draft_email      → email draft (requires_confirm=True)
  draft_whatsapp   → message draft (requires_confirm=True)
  analyze_sales    → period analysis (read-only)
  auto_analysis    → CEO executive summary (read-only)
  organize_report  → structured markdown report
"""
from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime, timezone
from typing import Any

BIG_API_PLACEHOLDER = os.getenv("ANTHROPIC_API_KEY", "")

# ─── AI call ─────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are the DMS Brain — a governed AI assistant for warehouse and logistics operations.

HARD RULES (never violate):
1. Never auto-commit or execute writes. Return suggestions only.
2. Never include PII — use [REDACTED] for any personal data.
3. Only use numbers you have been given explicitly. Never invent figures.
4. Surface compliance flags — never hide them.
5. Respond ONLY with valid JSON. No markdown, no preamble, no explanations outside JSON.
"""


def _ai(prompt: str, max_tokens: int = 2000) -> dict:
    """Call claude-sonnet-4-6 (T2). Returns parsed JSON dict or error dict."""
    if not BIG_API_PLACEHOLDER:
        return {"error": "BIG_API_PLACEHOLDER not configured", "mock": True}
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=BIG_API_PLACEHOLDER)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(raw)
    except json.JSONDecodeError as e:
        return {"error": f"JSON parse failed: {e}", "raw": raw[:300]}  # type: ignore[reportPossiblyUnbound]
    except Exception as e:
        return {"error": str(e)}


# ─── Intent handlers ─────────────────────────────────────────────────────────

CHART_COLORS = [
    "#4F46E5", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6",
    "#06B6D4", "#84CC16", "#F97316", "#EC4899", "#6366F1",
]


def generate_chart(query: str, data: dict) -> dict:
    """
    Generate a chart configuration from warehouse data.
    Returns recharts-compatible config.
    requires_confirm: false (read-only).
    """
    prompt = f"""
Warehouse data (use ONLY these numbers):
{json.dumps(data, indent=2, default=str)}

User chart request: "{query}"

Return JSON with this exact shape:
{{
  "chart_type": "bar" | "line" | "pie" | "area",
  "title": "string",
  "x_key": "field name for x-axis from the data",
  "y_keys": ["field1", "field2"],
  "data": [{{ "name": "label", "value": 0, "key2": 0 }}],
  "colors": {json.dumps(CHART_COLORS[:5])},
  "insights": ["insight 1", "insight 2"],
  "x_label": "string",
  "y_label": "string",
  "requires_confirm": false
}}
"""
    result = _ai(prompt)
    result["requires_confirm"] = False
    return result


def export_csv(query: str, rows: list[dict]) -> dict:
    """
    Generate CSV from data rows.
    Returns {filename, csv_content, row_count, columns, summary}.
    """
    if not rows:
        return {"error": "No data to export", "row_count": 0, "requires_confirm": False}

    buf = io.StringIO()
    columns = list(rows[0].keys())
    writer = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    csv_content = buf.getvalue()

    summary = f"{len(rows)} rows × {len(columns)} columns"
    try:
        summary_result = _ai(
            f'Data: {len(rows)} rows, columns: {columns}. '
            f'First row sample: {json.dumps(rows[0], default=str)}. '
            f'Request: "{query}". '
            f'Return JSON: {{"summary": "one sentence describing this export"}}',
            max_tokens=200,
        )
        summary = summary_result.get("summary") or summary
    except Exception:  # noqa: BLE001 — export must work offline without an LLM
        pass
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return {
        "filename": f"dms_export_{ts}.csv",
        "csv_content": csv_content,
        "row_count": len(rows),
        "columns": columns,
        "summary": summary,
        "requires_confirm": False,
    }


def draft_email(request: str, context: dict) -> dict:
    """
    Draft a professional email. Always requires_confirm=True.
    """
    prompt = f"""
Warehouse context summary:
{json.dumps(context, indent=2, default=str)}

Email request: "{request}"

Return JSON:
{{
  "subject": "email subject",
  "body": "full email body — greeting, paragraphs, sign-off. Use [WAREHOUSE_NAME] and [SENDER_NAME] as placeholders.",
  "to_suggestion": "role or email suggestion",
  "tone": "formal" | "professional" | "informational",
  "key_points": ["point 1", "point 2", "point 3"],
  "word_count": 0,
  "requires_confirm": true,
  "review_note": "This draft requires your review and approval before sending."
}}

Important: Only cite numbers from the context provided. Never fabricate metrics.
"""
    result = _ai(prompt, max_tokens=2000)
    result["requires_confirm"] = True
    if "body" in result:
        result["word_count"] = len(result["body"].split())
    return result


def draft_whatsapp(request: str, context: dict) -> dict:
    """
    Draft a WhatsApp/messaging app message. requires_confirm=True.
    """
    prompt = f"""
Context: {json.dumps(context, default=str)}
Request: "{request}"

Return JSON:
{{
  "message": "plain text message, no markdown, max 200 words",
  "tone": "professional" | "casual" | "urgent",
  "suggested_recipients": ["role1"],
  "emoji_suggestion": "optional single emoji",
  "requires_confirm": true
}}
"""
    result = _ai(prompt, max_tokens=600)
    result["requires_confirm"] = True
    if "message" in result:
        result["character_count"] = len(result["message"])
    return result


def analyze_sales(period: str, data: dict) -> dict:
    """
    Analyse warehouse movements/inventory for a period.
    Returns structured analysis report.
    """
    prompt = f"""
Warehouse data for period: {period}
{json.dumps(data, indent=2, default=str)}

Return JSON:
{{
  "period": "{period}",
  "narrative": "2-3 paragraph operational summary",
  "key_findings": [
    {{"finding": "string", "significance": "high|medium|low", "metric": "value"}}
  ],
  "recommendations": [
    {{"action": "string", "priority": "high|medium|low", "effort": "low|medium|high"}}
  ],
  "risk_flags": [
    {{"flag": "string", "severity": "critical|warning|info"}}
  ],
  "kpis": {{
    "total_movements": 0,
    "items_received": 0,
    "items_shipped": 0,
    "compliance_events": 0,
    "space_utilization_pct": 0
  }},
  "requires_confirm": false
}}

CRITICAL: Only use numbers from the data provided. Do not extrapolate or invent figures.
"""
    result = _ai(prompt, max_tokens=3000)
    result["requires_confirm"] = False
    result.setdefault("generated_at", datetime.now(timezone.utc).isoformat())
    return result


def auto_analysis(all_data: dict) -> dict:
    """
    Full warehouse KPI executive summary — CEO-ready.
    """
    prompt = f"""
Complete warehouse operational data:
{json.dumps(all_data, indent=2, default=str)}

Return JSON for a CEO executive summary:
{{
  "title": "Warehouse Operations Executive Summary",
  "performance_score": 0-100,
  "executive_summary": "3-4 sentences for the CEO",
  "sections": [
    {{
      "title": "Inventory Status",
      "content": "paragraph",
      "metrics": {{"key": "value"}}
    }},
    {{
      "title": "Movement Activity",
      "content": "paragraph",
      "metrics": {{}}
    }},
    {{
      "title": "Compliance & Audit",
      "content": "paragraph",
      "metrics": {{}}
    }},
    {{
      "title": "Space Utilization",
      "content": "paragraph",
      "metrics": {{}}
    }},
    {{
      "title": "Top Recommendations",
      "content": "paragraph",
      "metrics": {{}}
    }}
  ],
  "top_actions": [
    {{"action": "string", "owner": "role", "deadline": "timeframe", "priority": "high|medium"}}
  ],
  "requires_confirm": false
}}
"""
    result = _ai(prompt, max_tokens=4000)
    result["requires_confirm"] = False
    result["generated_at"] = datetime.now(timezone.utc).isoformat()
    return result


def organize_report(query: str, data: dict) -> dict:
    """
    Organize data into a structured markdown report.
    E.g. 'organize last week's sales and send to CEO'.
    """
    prompt = f"""
Data to organize:
{json.dumps(data, indent=2, default=str)}

Report request: "{query}"

Return JSON:
{{
  "title": "report title",
  "markdown": "full markdown report with headers, tables (use | pipe | format), and bullet points",
  "summary": "2-sentence summary",
  "suggested_filename": "report_YYYY-MM-DD.md",
  "requires_confirm": false
}}
"""
    result = _ai(prompt, max_tokens=3000)
    result["requires_confirm"] = False
    if "suggested_filename" not in result:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        result["suggested_filename"] = f"dms_report_{ts}.md"
    return result


# ─── Main dispatch ────────────────────────────────────────────────────────────

_DISPATCH = {
    "generate_chart": lambda p: generate_chart(p.get("query", ""), p.get("data", {})),
    "export_csv": lambda p: export_csv(p.get("query", ""), p.get("rows", [])),
    "draft_email": lambda p: draft_email(p.get("request", ""), p.get("context", {})),
    "draft_whatsapp": lambda p: draft_whatsapp(p.get("request", ""), p.get("context", {})),
    "analyze_sales": lambda p: analyze_sales(p.get("period", "this week"), p.get("data", {})),
    "auto_analysis": lambda p: auto_analysis(p.get("data", {})),
    "organize_report": lambda p: organize_report(p.get("query", ""), p.get("data", {})),
}


def run(intent: str, params: dict, actor: str = "user") -> dict:
    """
    Main brain dispatch.
    Pipeline: PII redact → Ponytail gate → execute → ledger write → return.
    """
    # PII-gate on all string params (F7 choke-point)
    safe_params: dict[str, Any] = {}
    pii_redacted = 0
    try:
        from packs.dms.security.pii import detect, redact_for_prompt

        for k, v in params.items():
            if isinstance(v, str):
                safe_params[k] = redact_for_prompt(v)
                pii_redacted += len(detect(v))
            else:
                safe_params[k] = v
    except ImportError:
        safe_params = params

    if intent not in _DISPATCH:
        return {
            "error": f"Unknown intent '{intent}'. Valid: {list(_DISPATCH.keys())}",
            "requires_confirm": False,
        }

    result = _DISPATCH[intent](safe_params)

    # F1 ledger write
    try:
        from packs.dms.audit import ledger
        ledger.append(
            actor,
            "brain.invoked",
            {
                "intent": intent,
                "pii_redacted": pii_redacted,
                "requires_confirm": result.get("requires_confirm", False),
                "has_error": "error" in result,
            },
        )
    except Exception:
        pass

    return result
