export function formatTimestamp(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString();
  } catch {
    return String(iso);
  }
}

export function shortAuditId(id) {
  if (!id) return "—";
  const s = String(id);
  return s.length > 12 ? `${s.slice(0, 8)}…` : s;
}

export function aggregateChangelog(entries = []) {
  const byAction = {};
  for (const e of entries) {
    const key = e.action || e.type || "unknown";
    byAction[key] = (byAction[key] || 0) + 1;
  }
  return byAction;
}

export function buildMessyHighlights(profile) {
  if (!profile?.detected_issues?.length) return [];
  return profile.detected_issues.map((issue) => ({
    type: issue.issue_type,
    field: issue.col,
    count: issue.row_count,
  }));
}
