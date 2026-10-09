/**
 * Visible copy for a refused brain-route AI step.
 *
 * Reads `refusal` and `llm_used` only. Question text, titles, and summaries
 * are not inputs. tests/dms/test_brain_refusal_visible.py deletes the marked
 * block and asserts a stubbed refusal stops hiding the success line.
 */

const REFUSAL_HEADLINE = "Model route unavailable: AI step skipped";
const EXPORT_FALLBACK = "Deterministic CSV kept; AI summary skipped.";

const SUCCESS_DATA_TYPE = {
  chart: "chart",
  export: "csv",
  email: "email",
  whatsapp: "whatsapp",
  analyze: "analysis",
  "auto-analysis": "analysis",
  report: "report",
  suggest: "suggestions",
};

function codeOn(record) {
  if (!record || typeof record !== "object" || Array.isArray(record)) return null;
  if (typeof record.refusal === "string" && record.refusal.length > 0) return record.refusal;
  if (record.llm_used === false) return "model_route_unavailable";
  return null;
}

function suggestionItems(payload) {
  if (Array.isArray(payload)) return payload;
  if (payload && typeof payload === "object" && Array.isArray(payload.suggestions)) {
    return payload.suggestions;
  }
  return null;
}

function refusalCode(payload) {
  const own = codeOn(payload);
  if (own) return own;
  const items = suggestionItems(payload);
  if (!items) return null;
  for (let i = 0; i < items.length; i += 1) {
    const code = codeOn(items[i]);
    if (code) return code;
  }
  return null;
}

function namedRefusal(code, kind, payload) {
  const headline = code === "model_route_unavailable" ? REFUSAL_HEADLINE : "AI step skipped";
  let text = `${headline} (${code})`;
  if (
    kind === "export" &&
    payload &&
    typeof payload.csv_content === "string" &&
    payload.csv_content.length > 0
  ) {
    text = `${text} ${EXPORT_FALLBACK}`;
  }
  return text;
}

function suggestionItemLines(payload) {
  const items = suggestionItems(payload) || [];
  return items.map((item) => {
    const code = codeOn(item);
    return code ? namedRefusal(code, "suggest", item) : null;
  });
}

function applyRefusalGuard(view) {
  // REFUSAL_VISIBLE_GUARD_START
  const code = refusalCode(view.payload);
  if (code) {
    return {
      ...view,
      text: namedRefusal(code, view.kind, view.payload),
      refused: true,
      code,
      dataType:
        view.kind === "export" ? "csv" : view.kind === "suggest" ? "suggestions" : "refusal",
      itemLines: suggestionItemLines(view.payload),
    };
  }
  // REFUSAL_VISIBLE_GUARD_END
  return view;
}

function presentBrain({ kind, payload, successText }) {
  const items = suggestionItems(payload);
  const view = {
    kind,
    payload,
    successText,
    text: successText,
    refused: false,
    code: null,
    data: kind === "suggest" ? items || [] : payload,
    dataType: SUCCESS_DATA_TYPE[kind] || kind,
    itemLines: [],
  };
  return applyRefusalGuard(view);
}

function suggestionRefusalLine(item) {
  const view = applyRefusalGuard({
    kind: "suggest",
    payload: item,
    successText: "",
    text: "",
    refused: false,
    code: null,
    data: item,
    dataType: "suggestions",
    itemLines: [],
  });
  return view.refused ? view.text : null;
}

module.exports = {
  EXPORT_FALLBACK,
  REFUSAL_HEADLINE,
  presentBrain,
  suggestionRefusalLine,
};
