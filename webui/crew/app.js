/* Cortex Crew UI — vanilla JS, no build step, talks to /crew/* + SSE. */
"use strict";

const $ = (sel) => document.querySelector(sel);
const state = {
  spaces: [],
  agents: [],
  activeSpace: null,
  channel: "activity", // 'activity' | agent id
  messages: [],
  streams: new Map(), // stream_id -> {agentId, text, el}
  activity: new Map(), // agent_id -> {state, depth}
  activeRun: null,
  editingAgent: null,
  usage: { runs: 0, prompt_tokens: 0, completion_tokens: 0 },
};

const api = {
  async get(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error(`${path} -> ${r.status}`);
    return r.json();
  },
  async send(path, body, method = "POST") {
    const r = await fetch(path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`${path} -> ${r.status}`);
    return r.json();
  },
};

/* ---------- tiny markdown (escape first, then decorate) ---------- */

function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function md(text) {
  const src = esc(text || "");
  const out = [];
  const lines = src.split("\n");
  let inCode = false, code = [], list = null;
  const flushList = () => { if (list) { out.push(`<${list}>${listItems.join("")}</${list}>`); list = null; } };
  let listItems = [];
  const inline = (s) =>
    s
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/(^|\W)\*([^*\n]+)\*(?=\W|$)/g, "$1<i>$2</i>")
      .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  for (const line of lines) {
    if (line.startsWith("```")) {
      if (inCode) { out.push(`<pre><code>${code.join("\n")}</code></pre>`); code = []; }
      inCode = !inCode;
      continue;
    }
    if (inCode) { code.push(line); continue; }
    const bullet = line.match(/^\s*[-*]\s+(.*)/);
    const num = line.match(/^\s*\d+[.)]\s+(.*)/);
    if (bullet || num) {
      const want = bullet ? "ul" : "ol";
      if (list !== want) { flushList(); list = want; listItems = []; }
      listItems.push(`<li>${inline((bullet || num)[1])}</li>`);
      continue;
    }
    flushList();
    const h = line.match(/^(#{1,4})\s+(.*)/);
    if (h) { out.push(`<b class="md-h">${inline(h[2])}</b>`); continue; }
    if (line.trim() === "") { out.push(""); continue; }
    out.push(`<p>${inline(line)}</p>`);
  }
  if (inCode) out.push(`<pre><code>${code.join("\n")}</code></pre>`);
  flushList();
  return out.join("");
}

/* ---------- lookups ---------- */

const agentById = (id) => state.agents.find((a) => a.id === id);
const agentName = (id) => (agentById(id) || { name: "?" }).name;
const agentEmoji = (id) => (agentById(id) || { emoji: "❔" }).emoji;
const agentColor = (id) => (agentById(id) || { color: "#26303d" }).color;
const spaceAgents = () =>
  state.agents.filter((a) => a.space_id === state.activeSpace && !a.deleted);

function fmtTime(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/* ---------- rail ---------- */

function renderRail() {
  const tabs = $("#space-tabs");
  tabs.innerHTML = "";
  for (const sp of state.spaces) {
    const b = document.createElement("button");
    b.className = "space-tab";
    b.textContent = sp.name;
    b.dataset.active = String(sp.id === state.activeSpace);
    b.onclick = () => { state.activeSpace = sp.id; selectChannel("activity"); renderRail(); };
    tabs.appendChild(b);
  }
  const list = $("#agent-list");
  list.innerHTML = "";
  for (const a of spaceAgents()) {
    const row = document.createElement("button");
    row.className = "rail-row";
    row.dataset.active = String(state.channel === a.id);
    row.dataset.paused = String(!!a.paused);
    const act = state.activity.get(a.id) || {};
    const meta =
      act.state === "thinking" ? "typing…" :
      act.state === "queued" ? `${act.depth || 2} queued` :
      a.paused ? "paused" : "";
    row.innerHTML =
      `<span class="avatar" style="--agent-color:${a.color}">${a.emoji}</span>` +
      `<span class="row-name">${esc(a.name)}</span>` +
      `<span class="row-meta" data-state="${act.state || ""}">${meta}</span>`;
    row.onclick = () => selectChannel(a.id);
    list.appendChild(row);
  }
  $("#activity-row").dataset.active = String(state.channel === "activity");
}

/* ---------- transcript ---------- */

async function selectChannel(channel) {
  state.channel = channel;
  state.streams.clear();
  const header = { name: "Activity", sub: "everything the crew says, live", emoji: "📡", color: "" };
  if (channel !== "activity") {
    const a = agentById(channel);
    if (a) {
      header.name = a.name;
      header.sub = (a.system_prompt || "").split("\n")[0].slice(0, 90) || "agent";
      header.emoji = a.emoji;
      header.color = a.color;
    }
  }
  $("#pane-name").textContent = header.name;
  $("#pane-sub").textContent = header.sub;
  const av = $("#pane-avatar");
  av.textContent = header.emoji;
  av.style.setProperty("--agent-color", header.color || "var(--rail-edge)");
  $("#btn-edit-agent").hidden = channel === "activity";
  $("#composer-bar").style.display = channel === "activity" ? "none" : "";
  renderRail();
  if (channel === "activity") {
    const data = await api.get(`/crew/flow?limit=400${state.activeSpace ? `&space_id=${state.activeSpace}` : ""}`);
    state.messages = data.messages;
    renderFlow();
  } else {
    const data = await api.get(`/crew/agents/${channel}/messages?limit=200`);
    state.messages = data.messages;
    renderTranscript();
  }
}

function msgBelongsToChannel(m) {
  if (state.channel === "activity") return !state.activeSpace || m.space_id === state.activeSpace;
  return m.channel_id === state.channel || (m.from_id === state.channel && m.kind === "a2a");
}

function renderTranscript() {
  const t = $("#transcript");
  t.innerHTML = "";
  for (const m of state.messages) t.appendChild(messageEl(m));
  t.scrollTop = t.scrollHeight;
}

function messageEl(m) {
  if (m.kind === "notice") {
    const d = document.createElement("div");
    d.className = "notice-row";
    d.textContent = m.content;
    return d;
  }
  if (m.kind === "tool") {
    const d = document.createElement("div");
    d.className = "tool-chip";
    d.dataset.ok = String(!!(m.meta && m.meta.ok));
    d.textContent = m.content;
    return d;
  }
  if (m.kind === "a2a" && state.channel !== "activity") {
    // peer traffic shows as a wire chip inside a transcript
    const d = document.createElement("div");
    d.className = "wire-chip";
    d.innerHTML =
      `<span class="avatar sm" style="--agent-color:${agentColor(m.from_id)}">${agentEmoji(m.from_id)}</span>` +
      `<span class="arrow">→</span>` +
      `<span class="avatar sm" style="--agent-color:${agentColor(m.to_id)}">${agentEmoji(m.to_id)}</span>` +
      `<span class="excerpt">${esc(m.content).slice(0, 160)}</span>` +
      (m.hop ? `<span class="hop">hop ${m.hop}</span>` : "");
    d.title = m.content;
    return d;
  }
  const el = document.createElement("article");
  el.className = "msg";
  const operator = m.from_kind === "user";
  el.dataset.operator = String(operator);
  const who = operator ? "You" : agentName(m.from_id);
  const face = operator ? "🧑‍✈️" : agentEmoji(m.from_id);
  const color = operator ? "var(--accent)" : agentColor(m.from_id);
  el.innerHTML =
    `<span class="avatar" style="--agent-color:${color}">${face}</span>` +
    `<div class="bubble"><div class="who"><b>${esc(who)}</b>` +
    `<span class="at">${fmtTime(m.created_at)}</span></div>` +
    `<div class="body">${md(m.content)}</div></div>`;
  return el;
}

function renderFlow() {
  const t = $("#transcript");
  t.innerHTML = "";
  const runs = new Map();
  for (const m of state.messages) {
    const key = m.run_id || "unrun";
    if (!runs.has(key)) runs.set(key, []);
    runs.get(key).push(m);
  }
  const blocks = [...runs.entries()].reverse();
  for (const [runId, msgs] of blocks) {
    const block = document.createElement("div");
    block.className = "run-block";
    const faces = [...new Set(msgs.map((m) => m.from_id).filter(Boolean))]
      .slice(0, 6)
      .map((id) => `<span class="avatar sm" style="--agent-color:${agentColor(id)}">${agentEmoji(id)}</span>`)
      .join("");
    const opener = msgs[0];
    const head = document.createElement("div");
    head.className = "run-head";
    head.innerHTML =
      `<span class="faces">${faces || '<span class="avatar sm sys">🧑‍✈️</span>'}</span>` +
      `<span class="excerpt">${esc((opener || {}).content || "").slice(0, 80)}</span>` +
      `<span style="margin-left:auto">${msgs.length} msg · ${fmtTime((opener || { created_at: 0 }).created_at)}</span>`;
    const body = document.createElement("div");
    body.className = "run-body";
    for (const m of msgs) {
      const row = document.createElement("div");
      row.className = "flow-row";
      const from = m.from_kind === "user" ? "You" : agentName(m.from_id);
      const to = m.to_kind === "user" ? "You" : m.to_kind === "system" ? "tools" : agentName(m.to_id);
      row.innerHTML =
        `<span class="route"><b>${esc(from)}</b> → <b>${esc(to)}</b>` +
        (m.hop ? ` <span class="hop">h${m.hop}</span>` : "") + `</span>` +
        `<span class="excerpt" title="${esc(m.content)}">${esc(m.content).slice(0, 200)}</span>`;
      body.appendChild(row);
    }
    head.onclick = () => { block.dataset.open = block.dataset.open === "true" ? "false" : "true"; };
    block.appendChild(head);
    block.appendChild(body);
    if (blocks.length === 1 || runId === state.activeRun) block.dataset.open = "true";
    t.appendChild(block);
  }
  t.scrollTop = t.scrollHeight;
}

/* ---------- live events ---------- */

function connectEvents() {
  const es = new EventSource("/crew/events");
  es.onmessage = (ev) => {
    let e;
    try { e = JSON.parse(ev.data); } catch { return; }
    handleEvent(e);
  };
  es.onerror = () => {
    es.close();
    setTimeout(connectEvents, 2000);
  };
}

function handleEvent(e) {
  switch (e.type) {
    case "message_appended": {
      const m = e.message;
      if (msgBelongsToChannel(m)) {
        state.messages.push(m);
        if (state.channel === "activity") renderFlow();
        else {
          const t = $("#transcript");
          const follow = t.scrollHeight - t.scrollTop - t.clientHeight < 80;
          t.appendChild(messageEl(m));
          if (follow) t.scrollTop = t.scrollHeight;
        }
      }
      break;
    }
    case "stream_started": {
      if (e.channel_id !== state.channel) break;
      const el = document.createElement("article");
      el.className = "msg streaming";
      el.innerHTML =
        `<span class="avatar" style="--agent-color:${agentColor(e.agent_id)}">${agentEmoji(e.agent_id)}</span>` +
        `<div class="bubble"><div class="who"><b>${esc(agentName(e.agent_id))}</b></div>` +
        `<div class="body"></div></div>`;
      $("#transcript").appendChild(el);
      state.streams.set(e.stream_id, { agentId: e.agent_id, text: "", el });
      state.activeRun = e.run_id;
      $("#btn-stop-run").hidden = false;
      break;
    }
    case "stream_delta": {
      const s = state.streams.get(e.stream_id);
      if (!s) break;
      s.text += e.text;
      s.el.querySelector(".body").innerHTML = md(s.text);
      const t = $("#transcript");
      if (t.scrollHeight - t.scrollTop - t.clientHeight < 120) t.scrollTop = t.scrollHeight;
      break;
    }
    case "stream_ended": {
      const s = state.streams.get(e.stream_id);
      if (s) { s.el.remove(); state.streams.delete(e.stream_id); }
      break;
    }
    case "activity_changed": {
      state.activity.set(e.agent_id, { state: e.state, depth: e.depth });
      renderRail();
      break;
    }
    case "tokens_used": {
      state.usage.prompt_tokens += e.prompt_tokens || 0;
      state.usage.completion_tokens += e.completion_tokens || 0;
      renderUsage();
      break;
    }
    case "run_settled": {
      if (e.run_id === state.activeRun) { state.activeRun = null; $("#btn-stop-run").hidden = true; }
      state.usage.runs += 1;
      renderUsage();
      break;
    }
    case "agents_changed":
      refreshState();
      break;
  }
}

/* ---------- inspector ---------- */

function renderUsage() {
  $("#ins-runs").textContent = state.usage.runs;
  $("#ins-ptok").textContent = state.usage.prompt_tokens.toLocaleString();
  $("#ins-ctok").textContent = state.usage.completion_tokens.toLocaleString();
}

async function renderInspector() {
  try {
    const cfg = await api.get("/crew/config");
    $("#ins-model").textContent = cfg.model;
    $("#ins-base").textContent = cfg.base_url.replace(/^https?:\/\//, "");
    const st = $("#ins-llm-status");
    if (!cfg.reachable) {
      st.textContent = "unreachable";
      st.dataset.ok = "false";
    } else if (cfg.models.length === 0) {
      st.textContent = "up — no models pulled";
      st.dataset.ok = "false";
    } else if (!cfg.models.includes(cfg.model)) {
      st.textContent = `up — '${cfg.model}' not pulled`;
      st.dataset.ok = "false";
    } else {
      st.textContent = "reachable";
      st.dataset.ok = "true";
    }
    const mcp = $("#mcp-card");
    mcp.innerHTML = "";
    for (const [name, s] of Object.entries(cfg.mcp)) {
      const d = document.createElement("div");
      d.className = "mcp-server";
      const status = !s.installed ? "not installed" : s.connected ? "connected" : "installed";
      d.innerHTML =
        `<div class="kv"><span>${esc(name)}</span><b data-ok="${s.connected}">${status}</b></div>` +
        (s.tools.length ? `<div class="tools">${s.tools.length} tools: ${esc(s.tools.slice(0, 6).join(", "))}…</div>` : "") +
        (s.blocked && s.blocked.length ? `<div class="blocked">blocked by policy: ${esc(s.blocked.join(", "))}</div>` : "") +
        (s.error ? `<div class="blocked">${esc(s.error)}</div>` : "");
      mcp.appendChild(d);
    }
    const dl = $("#model-options");
    dl.innerHTML = cfg.models.map((m) => `<option>${esc(m)}</option>`).join("");
  } catch {
    $("#ins-llm-status").textContent = "engine unreachable";
  }
}

/* ---------- composer + actions ---------- */

async function sendMessage() {
  const box = $("#composer");
  const text = box.value.trim();
  if (!text || state.channel === "activity") return;
  box.value = "";
  box.style.height = "auto";
  try {
    await api.send("/crew/messages", { agent_id: state.channel, text });
  } catch (err) {
    alert("send failed: " + err.message);
  }
}

function openAgentDialog(agent) {
  state.editingAgent = agent || null;
  const form = $("#agent-form");
  form.reset();
  $("#agent-form-title").textContent = agent ? `Edit ${agent.name}` : "New agent";
  if (agent) {
    form.name.value = agent.name;
    form.emoji.value = agent.emoji;
    form.color.value = agent.color;
    form.system_prompt.value = agent.system_prompt;
    form.model.value = agent.model || "";
    form.computer_enabled.checked = !!agent.computer_enabled;
  }
  $("#dlg-agent").showModal();
}

async function saveAgentDialog() {
  const form = $("#agent-form");
  const draft = {
    name: form.name.value.trim(),
    emoji: form.emoji.value,
    color: form.color.value,
    system_prompt: form.system_prompt.value,
    model: form.model.value.trim() || null,
    computer_enabled: form.computer_enabled.checked,
  };
  if (!draft.name) return;
  if (state.editingAgent) {
    await api.send(`/crew/agents/${state.editingAgent.id}`, draft, "PATCH");
  } else {
    await api.send("/crew/agents", { ...draft, space_id: state.activeSpace });
  }
  await refreshState();
}

async function openSettings() {
  const cfg = await api.get("/crew/config");
  const form = $("#settings-form");
  form.base_url.value = cfg.base_url;
  form.model.value = cfg.model;
  form.computer_server.value = cfg.computer_server;
  const notes = Object.entries(cfg.mcp)
    .map(([n, s]) => `${n}: ${s.installed ? (s.connected ? "connected" : "installed") : "not installed"}`)
    .join(" · ");
  $("#settings-mcp-note").textContent = notes;
  $("#dlg-settings").showModal();
}

/* ---------- bootstrap ---------- */

async function refreshState() {
  const data = await api.get("/crew/state");
  state.spaces = data.spaces;
  state.agents = data.agents;
  state.usage = data.usage;
  if (!state.activeSpace && state.spaces.length) state.activeSpace = state.spaces[0].id;
  $("#empty-state").hidden = !(state.spaces.length === 0 || spaceAgents().length === 0);
  renderRail();
  renderUsage();
}

async function boot() {
  await refreshState();
  if (state.spaces.length === 0) {
    const sp = await api.send("/crew/spaces", { name: "HQ" });
    state.activeSpace = sp.id;
    await refreshState();
  }
  connectEvents();
  renderInspector();
  setInterval(renderInspector, 30000);
  selectChannel("activity");

  $("#btn-send").onclick = sendMessage;
  const composer = $("#composer");
  composer.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });
  composer.addEventListener("input", () => {
    composer.style.height = "auto";
    composer.style.height = Math.min(composer.scrollHeight, 160) + "px";
  });
  $("#activity-row").onclick = () => selectChannel("activity");
  $("#btn-new-agent").onclick = () => openAgentDialog(null);
  $("#btn-edit-agent").onclick = () => {
    const a = agentById(state.channel);
    if (a) openAgentDialog(a);
  };
  $("#btn-new-space").onclick = async () => {
    const name = prompt("Space name");
    if (!name) return;
    const sp = await api.send("/crew/spaces", { name });
    state.activeSpace = sp.id;
    await refreshState();
    selectChannel("activity");
  };
  $("#btn-settings").onclick = openSettings;
  $("#btn-stop-run").onclick = async () => {
    if (state.activeRun) await api.send(`/crew/runs/${state.activeRun}/stop`);
  };
  $("#btn-starter").onclick = async () => {
    if (!state.activeSpace) {
      const sp = await api.send("/crew/spaces", { name: "HQ" });
      state.activeSpace = sp.id;
    }
    await api.send(`/crew/spaces/${state.activeSpace}/starter`);
    await refreshState();
    $("#empty-state").hidden = true;
  };
  $("#agent-form").addEventListener("submit", (e) => {
    if (e.submitter && e.submitter.value === "ok") saveAgentDialog();
  });
  $("#settings-form").addEventListener("submit", async (e) => {
    if (!e.submitter || e.submitter.value !== "ok") return;
    const form = $("#settings-form");
    await api.send("/crew/config", {
      base_url: form.base_url.value.trim(),
      model: form.model.value.trim(),
      computer_server: form.computer_server.value,
    });
    renderInspector();
  });
}

boot();
