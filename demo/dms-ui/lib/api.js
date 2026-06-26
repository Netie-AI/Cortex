const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export class ApiOfflineError extends Error {
  constructor(message = "API offline") {
    super(message);
    this.name = "ApiOfflineError";
  }
}

async function request(path, options = {}) {
  const url = `${API_BASE}${path}`;
  let res;
  try {
    res = await fetch(url, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
    });
  } catch {
    throw new ApiOfflineError();
  }
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) {
    return res.json();
  }
  return res;
}

export async function checkHealth() {
  return request("/health");
}

export async function postQuery(question) {
  return request("/dms/query", {
    method: "POST",
    body: JSON.stringify({ question }),
  });
}

export async function fetchTables() {
  return request("/dms/tables");
}

export async function fetchTablePreview(table, from = 0, to = 100, cols = "") {
  const params = new URLSearchParams({ table, from: String(from), to: String(to) });
  if (cols) params.set("cols", cols);
  return request(`/dms/table-preview?${params}`);
}

export async function fetchAudit() {
  return request("/dms/audit");
}

export async function proposeEdits(changes, approvedBy = "demo_steward") {
  return request("/dms/propose-edit", {
    method: "POST",
    body: JSON.stringify({ changes, approved_by: approvedBy }),
  });
}

export async function fetchData(variant, limit = 50) {
  return request(`/dms/data/${variant}?limit=${limit}`);
}

export async function analyseEntry(rawText) {
  return request("/dms/analyse-entry", {
    method: "POST",
    body: JSON.stringify({ raw_text: rawText }),
  });
}

export async function addEntry(proposed, approvedBy = "demo_steward") {
  return request("/dms/add-entry", {
    method: "POST",
    body: JSON.stringify({ proposed, approved_by: approvedBy }),
  });
}

export async function createChatThread({ customer_label, external_ref, actor = "demo_operator" } = {}) {
  return request("/dms/threads", {
    method: "POST",
    body: JSON.stringify({
      customer_label,
      external_ref,
      actor,
    }),
  });
}

export async function fetchThreadMessages(threadId) {
  return request(`/dms/threads/${threadId}/messages`);
}

export async function sendThreadMessage(threadId, { sender, body, direction = "inbound", actor = "demo_operator" }) {
  return request(`/dms/threads/${threadId}/messages`, {
    method: "POST",
    body: JSON.stringify({ sender, body, direction, actor }),
  });
}

export async function fetchWarehouseTree(tenantId = "default") {
  return request(`/dms/warehouse/locations/tree?tenant_id=${tenantId}`);
}

export async function fetchLocationSpace(locationId, tenantId = "default") {
  return request(`/dms/locations/${locationId}/space?tenant_id=${tenantId}`);
}

export function warehouseQrLabelUrl(locationId, tenantId = "default") {
  return `${API_BASE}/dms/warehouse/locations/${locationId}/qr-label?tenant_id=${tenantId}`;
}

export async function intakeItem(payload) {
  return request("/dms/items/intake", { method: "POST", body: JSON.stringify(payload) });
}

export async function scanMove(payload) {
  return request("/dms/movements/scan", { method: "POST", body: JSON.stringify(payload) });
}

export async function confirmItemDims(itemId, payload) {
  return request(`/dms/items/${itemId}/confirm-dims`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function checkTaskGate({ eventId, taskId, filledTemplate, actor = "user" }) {
  return request("/dms/tasks/gate/check", {
    method: "POST",
    body: JSON.stringify({
      event_id: eventId,
      task_id: taskId,
      filled_template: filledTemplate,
      actor,
    }),
  });
}

export async function chooseTask({ messageId, threadId, taskId, filledTemplate, intent, actor = "user", accepted = true }) {
  return request("/dms/tasks/choose", {
    method: "POST",
    body: JSON.stringify({
      message_id: messageId,
      thread_id: threadId,
      task_id: taskId,
      filled_template: filledTemplate,
      intent,
      actor,
      accepted,
    }),
  });
}

export async function acknowledgeGate({ eventId, actor = "steward" }) {
  return request("/dms/tasks/gate/acknowledge", {
    method: "POST",
    body: JSON.stringify({ event_id: eventId, actor }),
  });
}

export async function fetchTaskSuggestions({ useLlm = false, triggerText } = {}) {
  return request("/dms/brain/suggest", {
    method: "POST",
    body: JSON.stringify({
      use_llm: useLlm,
      trigger_text: triggerText || undefined,
    }),
  });
}

export async function fetchSkills() {
  return request("/dms/skills");
}

export async function deactivateSkill(skillId, actor = "demo_steward") {
  return request(`/dms/skills/${skillId}/deactivate`, {
    method: "POST",
    body: JSON.stringify({ actor }),
  });
}
