/* Cortex-shaped compiler for the Constructor skin.
   Pages: no fetch. localhost:8010: optional same-origin Cortex calls.
   Execution truth stays on Cortex dag_runner. This file only compiles, ghosts, and ranks. */

const CORTEX_KIND = {
  ingest: "DOCUMENT_REF",
  connector: "DOCUMENT_REF",
  ontology: "DOCUMENT_REF",
  insight: "DOCUMENT_REF",
  foundry: "DOCUMENT_REF",
  app: "EMIT",
  agent: "AGENT_TASK",
  hypothesize: "DOCUMENT_REF",
  improve: "DOCUMENT_REF",
  audit: "DOCUMENT_REF",
  tool_call: "TOOL_CALL",
};

const APPROACHES = [
  {
    id: "single_agent",
    name: "Single agent",
    cortex_status: "strong",
    cortex_path: "AGENT_TASK max_steps loop",
    cost: 1,
    audit: 3,
    blast: 1,
    parked: false,
    blurb: "One context, one tool loop. Default unless the graph has independent facets.",
  },
  {
    id: "generator_verifier",
    name: "Generator-verifier",
    cortex_status: "partial",
    cortex_path: "LLM_JUDGED then EMIT audit",
    cost: 2,
    audit: 5,
    blast: 1,
    parked: false,
    blurb: "Generate, then verify against explicit audit criteria. Best when wrong output is expensive.",
  },
  {
    id: "orchestrator_subagent",
    name: "Orchestrator-subagent",
    cortex_status: "strong",
    cortex_path: "compile_template -> dag_runner + AGENT_TASK",
    cost: 4,
    audit: 4,
    blast: 2,
    parked: false,
    blurb: "Lead plans, bounded subagents return distilled results. Use for ontology -> insights -> foundry -> app.",
  },
];

function cortexOrigin() {
  const host = location.hostname;
  const path = location.pathname;
  if (host === "app.netie.ai" && path.indexOf("/cortex") === 0) return true;
  if ((host === "127.0.0.1" || host === "localhost") && path.indexOf("/cortex") === 0) {
    return true;
  }
  return false;
}

function statusWeight(status) {
  if (status === "strong") return 4;
  if (status === "partial") return 2;
  return 0;
}

function scoreApproach(row) {
  return row.audit * 2 + statusWeight(row.cortex_status) - row.cost - row.blast;
}

function compileIR(state) {
  const output =
    [...state.nodes].reverse().find((n) => n.kind === "app") ||
    [...state.nodes].reverse().find((n) => n.kind === "audit") ||
    state.nodes[state.nodes.length - 1];
  return {
    version: "1.0",
    engine: "cortex",
    ghost: !!window.Constructor.ghost,
    entry_node_id: state.nodes[0].id,
    output_node_id: output.id,
    nodes: state.nodes.map((n) => {
      let kind = CORTEX_KIND[n.kind] || "DOCUMENT_REF";
      if (n.id === output.id) kind = "EMIT";
      else if (kind === "EMIT") kind = "DETERMINISTIC_RULE";
      return {
        id: n.id,
        kind: kind,
        constructor_kind: n.kind,
        note: n.note,
        requires_confirm: n.kind === "tool_call",
      };
    }),
    edges: state.edges.slice(),
  };
}

function topo(state) {
  const incoming = {};
  for (const n of state.nodes) incoming[n.id] = 0;
  for (const e of state.edges) {
    if (incoming[e.to] === undefined) incoming[e.to] = 0;
    incoming[e.to] += 1;
  }
  const q = state.nodes.filter((n) => incoming[n.id] === 0).map((n) => n.id);
  const out = [];
  while (q.length) {
    const id = q.shift();
    out.push(id);
    for (const e of state.edges.filter((edge) => edge.from === id)) {
      incoming[e.to] -= 1;
      if (incoming[e.to] === 0) q.push(e.to);
    }
  }
  for (const n of state.nodes) {
    if (!out.includes(n.id)) out.push(n.id);
  }
  return out;
}

async function ghostRun() {
  const C = window.Constructor;
  const state = C.getState();
  if (cortexOrigin()) {
    const remote = await cortexPost("/cortex/constructor/ghost", {
      nodes: state.nodes,
      edges: state.edges,
    });
    C.showAudit({ mode: "cortex-ghost", remote: remote });
    if (remote && remote.ok) {
      C.markGhostWalk((remote.nodes || []).map((n) => n.id));
      return "Cortex ghost compile ok. EMIT=" + remote.output_node_id + ". No writes.";
    }
    return "Cortex ghost blocked (" + (remote && (remote.status || remote.error) || "offline") + "). Local walk instead. " + localGhostWalk();
  }
  return localGhostWalk();
}

function localGhostWalk() {
  const C = window.Constructor;
  const state = C.getState();
  const order = topo(state);
  const log = [];
  for (const id of order) {
    const node = state.nodes.find((n) => n.id === id);
    const write = !C.ghost && (node.kind === "tool_call" || node.kind === "app");
    log.push({
      id: node.id,
      kind: node.kind,
      cortex: CORTEX_KIND[node.kind],
      ghost: C.ghost || !write,
      write: !!write,
      would: node.note,
      action_type: node.kind === "tool_call" ? "export_pptx" : "agent.checked",
    });
  }
  C.markGhostWalk(order);
  C.showAudit({
    mode: C.ghost ? "ghost" : "live-local",
    engine: cortexOrigin() ? "cortex-origin" : "pages-sketch",
    steps: log,
  });
  return (
    (C.ghost ? "Ghost run (no writes). " : "Local walk. Tool/app nodes would write. ") +
    log.length +
    " steps. Audit panel has the ledger."
  );
}

async function rankApproaches() {
  if (cortexOrigin()) {
    const remote = await cortexPost("/cortex/constructor/recommend", {
      nodes: window.Constructor.getState().nodes,
      edges: window.Constructor.getState().edges,
    });
    if (remote && remote.ok && Array.isArray(remote.approaches)) {
      const recId = remote.recommendation && remote.recommendation.pattern;
      const box = document.getElementById("approaches");
      box.innerHTML = remote.approaches
        .map((row) => {
          const win = row.id === recId;
          return (
            '<article class="approach' +
            (win ? " winner" : "") +
            '"><div class="eyebrow">' +
            (win ? "WINNER" : "ALT") +
            " / " +
            String(row.cortex_status || "").toUpperCase() +
            "</div><h3>" +
            row.name +
            "</h3><p>" +
            row.blurb +
            '</p><p class="hint">' +
            row.cortex_path +
            "</p></article>"
          );
        })
        .join("");
      const ranked = remote.approaches.map((row) =>
        Object.assign({}, row, { score: row.id === recId ? 99 : 0 })
      );
      ranked.sort((a, b) => b.score - a.score);
      window.Constructor.lastRanking = ranked;
      return ranked;
    }
  }
  const kinds = new Set(window.Constructor.getState().nodes.map((n) => n.kind));
  const foundry = ["ontology", "insight", "foundry", "app"].every((k) => kinds.has(k));
  const verify = kinds.has("hypothesize") && kinds.has("audit") && !foundry;
  const ranked = APPROACHES.map((row) => {
    let score = scoreApproach(row);
    if (foundry && row.id === "orchestrator_subagent") score += 20;
    if (verify && row.id === "generator_verifier") score += 20;
    return Object.assign({}, row, { score: score });
  }).sort((a, b) => b.score - a.score);
  const box = document.getElementById("approaches");
  box.innerHTML = ranked
    .map((row, i) => {
      return (
        '<article class="approach' +
        (i === 0 ? " winner" : "") +
        '"><div class="eyebrow">' +
        (i === 0 ? "WINNER" : "ALT") +
        " / " +
        row.cortex_status.toUpperCase() +
        '</div><h3>' +
        row.name +
        "</h3><p>" +
        row.blurb +
        '</p><p class="hint">score ' +
        row.score +
        " · " +
        row.cortex_path +
        "</p></article>"
      );
    })
    .join("");
  window.Constructor.lastRanking = ranked;
  return ranked;
}

function applyWinner(id) {
  const C = window.Constructor;
  const state = C.getState();
  if (id === "generator_verifier") {
    C.ensureKinds(["hypothesize", "audit"]);
  } else if (id === "orchestrator_subagent") {
    C.loadFoundryPath();
  } else {
    C.ensureKinds(["agent", "audit"]);
  }
  state.approach = id;
  C.save();
  C.render();
}

async function cortexPost(path, body) {
  if (!cortexOrigin()) return null;
  const keyEl = document.getElementById("cortex-key");
  const key = keyEl && keyEl.value ? keyEl.value.trim() : "";
  const headers = { "Content-Type": "application/json" };
  if (key) headers["X-API-Key"] = key;
  try {
    const res = await fetch(path, {
      method: "POST",
      headers: headers,
      credentials: "same-origin",
      body: JSON.stringify(body || {}),
    });
    if (!res.ok) return { ok: false, status: res.status };
    return await res.json();
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

async function liveOrGhost() {
  const C = window.Constructor;
  const ir = compileIR(C.getState());
  if (C.ghost || !cortexOrigin()) {
    return ghostRun();
  }
  const remote = await cortexPost("/cortex/constructor/run", {
    nodes: C.getState().nodes,
    edges: C.getState().edges,
  });
  C.showAudit({ mode: "cortex-run", ir: ir, remote: remote });
  if (!remote || remote.ok === false) {
    return "Cortex run blocked (" + (remote && (remote.status || remote.error) || "offline") + "). Fell back to ghost. " + ghostRun();
  }
  return "Cortex accepted the DAG. Audit panel has the engine reply.";
}

function chatSay(role, text) {
  const log = document.getElementById("chat-log");
  const el = document.createElement("div");
  el.className = "bubble " + role;
  el.textContent = text;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
}

async function handleChat(raw) {
  const text = raw.trim();
  const t = text.toLowerCase();
  const C = window.Constructor;
  if (!t) return "Say what to build.";
  if (t === "help") {
    return "foundry path. ghost on/off. ghost run. propose 3. maximize. run. add <kind>. wire <id> to <id>.";
  }
  if (/ghost off/.test(t)) {
    C.setGhost(false);
    return "Ghost off. Live walk may write on tool_call/app nodes. Cortex writes still need a key.";
  }
  if (/ghost on|ghost mode/.test(t)) {
    C.setGhost(true);
    return "Ghost on. Dry-run only. Action types are ledgered as would-call, not executed.";
  }
  if (/^ghost( run)?$/.test(t) || /dry.?run/.test(t)) {
    C.setGhost(true);
    return await ghostRun();
  }
  if (/propose|bakeoff|approach/.test(t)) {
    const ranked = await rankApproaches();
    return (
      "Ranked 3 Cortex patterns. Winner: " +
      ranked[0].name +
      " (score " +
      ranked[0].score +
      "). Parked patterns (teams/bus) stay out."
    );
  }
  if (/maximi[sz]e|pick winner|best/.test(t)) {
    const ranked = await rankApproaches();
    applyWinner(ranked[0].id);
    return "Applied " + ranked[0].name + ". Graph compiled toward " + ranked[0].cortex_path + ".";
  }
  if (/foundry|ontology path|create app/.test(t)) {
    C.loadFoundryPath();
    C.setGhost(true);
    const ranked = await rankApproaches();
    return (
      "Loaded connector -> ontology -> insight -> foundry -> app. Ghost on. Winner: " +
      ranked[0].name +
      "."
    );
  }
  const add = t.match(/^add ([a-z_]+)$/);
  if (add) {
    if (!C.KINDS[add[1]]) return "Unknown kind. " + Object.keys(C.KINDS).join(", ");
    C.addNode(add[1]);
    return "Added " + add[1] + " (" + CORTEX_KIND[add[1]] + ").";
  }
  const wire = t.match(/^wire (\S+) to (\S+)$/);
  if (wire) {
    const ok = C.wire(wire[1], wire[2]);
    return ok ? "Wired " + wire[1] + " -> " + wire[2] : "Need two existing node ids.";
  }
  if (/^run$/.test(t) || /live run|execute/.test(t)) {
    return await liveOrGhost();
  }
  C.loadFoundryPath();
  C.setGhost(true);
  const ranked = await rankApproaches();
  return (
    "Built the foundry path and ghost-ranked 3 Cortex approaches. Winner: " +
    ranked[0].name +
    ". Next: maximize / run / add agent."
  );
}

function bindChat() {
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = input.value;
    if (!text.trim()) return;
    chatSay("user", text);
    input.value = "";
    const reply = await handleChat(text);
    chatSay("assistant", reply);
  });
  document.getElementById("ghost-toggle").addEventListener("click", () => {
    const C = window.Constructor;
    C.setGhost(!C.ghost);
    chatSay("assistant", C.ghost ? "Ghost on." : "Ghost off.");
  });
  document.getElementById("propose").addEventListener("click", async () => {
    chatSay("assistant", await handleChat("propose 3"));
  });
  document.getElementById("maximize").addEventListener("click", async () => {
    chatSay("assistant", await handleChat("maximize"));
  });
  const runBtn = document.getElementById("run-graph");
  if (runBtn) {
    runBtn.addEventListener("click", async () => {
      chatSay("assistant", await handleChat("run"));
    });
  }
  chatSay(
    "assistant",
    "Stranger path is already on the canvas: connector -> ontology -> insight -> foundry -> app. Ghost is on. Try: ghost run, then propose 3, then maximize. Pages never fetch. Live run needs Cortex + OpenVault key."
  );
  rankApproaches();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", bindChat);
} else {
  bindChat();
}
