/* Cortex-shaped compiler for the Constructor skin.
   Pages: no fetch. Local /cortex on the engine port (docs :8010; this laptop also :8011).
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
  enhance: "DOCUMENT_REF",
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

function cortexLocalHint() {
  return "http://127.0.0.1:8010/cortex or http://127.0.0.1:8011/cortex";
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
        object_type: n.object_type || null,
        data_point: n.data_point || null,
        data_type: n.data_type || null,
        action_type: n.action_type || null,
        fetch_from: n.fetch_from || null,
        tier: n.tier || "T0",
        stream: !!n.stream,
        note: n.note,
        requires_confirm: n.kind === "tool_call",
        provenance: (window.Constructor && window.Constructor.provenanceStamp)
          ? window.Constructor.provenanceStamp(n)
          : "AMBIGUOUS",
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

function recordLastRun(mode, summary, extra) {
  const C = window.Constructor;
  if (!C) return summary;
  extra = extra || {};
  C.lastRun = {
    mode: mode,
    summary: summary,
    ledger_id: extra.ledger_id || extra.audit_id || "",
    at: Date.now(),
  };
  if (typeof C.showDecision === "function") {
    const node = C.selected && C.selected();
    if (node) C.showDecision({ node: node, response: summary });
  }
  return summary;
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
      return recordLastRun(
        "ghost",
        "Cortex ghost compile ok. EMIT=" + remote.output_node_id + ". No writes.",
        remote
      );
    }
    return recordLastRun(
      "ghost",
      "Cortex ghost blocked (" + (remote && (remote.status || remote.error) || "offline") + "). Local walk instead. " + localGhostWalk(),
      remote
    );
  }
  return recordLastRun("ghost", localGhostWalk(), {});
}

let automateTimer = null;
let automateTicks = 0;
let automateBusy = false;

async function automateTick() {
  const C = window.Constructor;
  if (!C || !C.automate || automateBusy) return;
  automateBusy = true;
  const live = cortexOrigin();
  if (!live) C.setGhost(true);
  automateTicks += 1;
  let msg = "";
  try {
    msg = live ? await liveOrGhost(true) : await ghostRun();
  } catch (err) {
    msg = String((err && err.message) || err);
  }
  const power = document.getElementById("power");
  if (power) {
    power.textContent =
      "Automate #" +
      automateTicks +
      (live ? " (live run_dag). " : " (15s ghost). ") +
      String(msg).slice(0, 140);
  }
  automateBusy = false;
  if (C.automate) automateTimer = setTimeout(automateTick, 15000);
}

function setAutomate(on) {
  const C = window.Constructor;
  const btn = document.getElementById("automate");
  if (automateTimer) {
    clearTimeout(automateTimer);
    automateTimer = null;
  }
  C.automate = !!on;
  if (btn) {
    btn.textContent = on ? "Automate on" : "Automate off";
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  }
  if (!on) return;
  if (!cortexOrigin()) C.setGhost(true);
  automateTick();
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
      action_type: node.action_type || (node.kind === "tool_call" ? "export_pptx" : "agent.checked"),
      object_type: node.object_type || null,
      data_point: node.data_point || null,
      fetch_from: node.fetch_from || null,
    });
  }
  C.markGhostWalk(order);
  const task = C.intendedTask ? C.intendedTask(state) : { ok: true, missing: [], hops: [] };
  C.showAudit({
    mode: C.ghost ? "ghost" : "live-local",
    engine: cortexOrigin() ? "cortex-origin" : "pages-sketch",
    compile: "local_template",
    intended_task: task,
    steps: log,
  });
  const taskLine = task.ok
    ? " Intended task PASS. Mock rows reach emit."
    : " Intended task FAIL: " + (task.missing || []).join("; ") + ". Redo compile.";
  return (
    (C.ghost ? "Ghost run (no writes). " : "Local walk. Tool/app nodes would write. ") +
    log.length +
    " steps. Local template, not an LLM." +
    taskLine
  );
}

function approachHtml(row, win) {
  return (
    '<article class="approach' +
    (win ? " winner" : "") +
    '"><h3>' +
    row.name +
    "</h3><p class=\"hint\">" +
    (row.score != null ? "score " + row.score : row.cortex_status || "") +
    "</p></article>"
  );
}

async function rankApproaches() {
  const box = document.getElementById("approaches");
  const sum = document.getElementById("approaches-summary");
  function paint(ranked, recId) {
    if (!box) return ranked;
    box.innerHTML = ranked
      .map(function (row, i) {
        const win = recId ? row.id === recId : i === 0;
        return approachHtml(row, win);
      })
      .join("");
    if (sum && ranked[0]) sum.textContent = ranked[0].name;
    return ranked;
  }
  if (cortexOrigin()) {
    const remote = await cortexPost("/cortex/constructor/recommend", {
      nodes: window.Constructor.getState().nodes,
      edges: window.Constructor.getState().edges,
    });
    if (remote && remote.ok && Array.isArray(remote.approaches)) {
      const recId = remote.recommendation && remote.recommendation.pattern;
      const ranked = remote.approaches.map((row) =>
        Object.assign({}, row, { score: row.id === recId ? 99 : 0 })
      );
      ranked.sort((a, b) => b.score - a.score);
      window.Constructor.lastRanking = ranked;
      return paint(ranked, recId);
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
  window.Constructor.lastRanking = ranked;
  return paint(ranked);
}

function applyWinner(id) {
  const C = window.Constructor;
  const state = C.getState();
  if (id === "generator_verifier") {
    C.ensureKinds(["hypothesize", "audit"]);
  } else if (id === "orchestrator_subagent") {
    C.loadCompilePath();
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
    const text = await res.text();
    let parsed = null;
    try {
      parsed = text ? JSON.parse(text) : null;
    } catch {
      parsed = null;
    }
    if (!res.ok) {
      return {
        ok: false,
        status: res.status,
        detail: (parsed && (parsed.detail || parsed.error)) || text.slice(0, 240),
      };
    }
    return parsed;
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

async function cortexGet(path) {
  if (!cortexOrigin()) return null;
  const keyEl = document.getElementById("cortex-key");
  const key = keyEl && keyEl.value ? keyEl.value.trim() : "";
  const headers = {};
  if (key) headers["X-API-Key"] = key;
  try {
    const res = await fetch(path, { method: "GET", headers: headers, credentials: "same-origin" });
    if (!res.ok) return { ok: false, status: res.status };
    return await res.json();
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

function setOvStatus(text) {
  const el = document.getElementById("ov-status");
  if (!el) return;
  el.hidden = !text;
  el.textContent = text;
}

async function bindSession(key) {
  if (!cortexOrigin() || !key) return { ok: false, error: "not cortex origin" };
  const remote = await cortexPost("/cortex/session", { key: key });
  if (remote && remote.ok === false) return remote;
  return { ok: true };
}

async function issueOpenVaultKey() {
  if (!cortexOrigin()) {
    return "Issue key is Cortex loopback only. Open " + cortexLocalHint() + " (login).";
  }
  const remote = await cortexPost("/cortex/constructor/issue-key", {});
  if (!remote || !remote.token) {
    const why =
      (remote && (remote.detail || remote.error || remote.status)) || "offline";
    return "OpenVault did not issue a key (" + why + ").";
  }
  const keyEl = document.getElementById("cortex-key");
  if (keyEl) keyEl.value = remote.token;
  await bindSession(remote.token);
  const kid = remote.key && (remote.key.key_id || remote.key.id);
  setOvStatus("OpenVault: issued " + (kid || "ov_") + " (shown once). Fetch and run all are live.");
  return "OpenVault key issued once. Token is in the box. Lost keys cannot be recovered.";
}

async function loadOntology() {
  const C = window.Constructor;
  if (!cortexOrigin() || !C.replaceCatalog) return;
  const remote = await cortexGet("/cortex/constructor/ontology");
  let nobj = 0;
  let nact = 0;
  let nplace = 0;
  if (remote && remote.ok && remote.objects) {
    C.replaceCatalog(remote.objects, remote.actions, remote.fetch_places);
    nobj = Object.keys(remote.objects).length;
    nact = (remote.actions || []).length;
    nplace = (remote.fetch_places || []).length;
  } else if (remote && remote.status === 401) {
    setOvStatus("OpenVault: key required. Issue ov_ or paste one, then fetch / run all.");
    return;
  }
  const hadWs = (remote && Array.isArray(remote.fetch_places)
    ? remote.fetch_places.some(function (p) {
        return String(p).indexOf("workspace.") === 0;
      })
    : false);
  const ws = await cortexGet("/api/connectors/workspaces");
  const extra = [];
  if (ws && Array.isArray(ws.workspaces)) {
    for (let i = 0; i < ws.workspaces.length; i++) {
      const id = ws.workspaces[i] && ws.workspaces[i].id;
      if (id) extra.push("workspace." + id);
    }
  }
  if (extra.length) {
    C.replaceCatalog({}, [], extra);
    if (!hadWs) nplace += extra.length;
  }
  if (nobj || nplace) {
    setOvStatus(
      "Cortex catalog live: " +
        nobj +
        " objects, " +
        nact +
        " actions, " +
        nplace +
        " fetch places (warehouse + workspaces). Paste or issue an ov_ key to run."
    );
  }
}

async function liveOrGhost(forceLive) {
  const C = window.Constructor;
  const ir = compileIR(C.getState());
  if (forceLive) {
    if (!cortexOrigin()) {
      return "Live run is Cortex only (POST /cortex/constructor/run). Pages never fetch. Open " + cortexLocalHint() + ".";
    }
    C.setGhost(false);
    const remote = await cortexPost("/cortex/constructor/run", {
      nodes: C.getState().nodes,
      edges: C.getState().edges,
    });
    C.showAudit({ mode: "cortex-run", ir: ir, remote: remote });
    if (!remote || remote.ok === false) {
      return (
        "Cortex run_dag failed (" +
        ((remote && (remote.status || remote.error || remote.detail)) || "offline") +
        "). No internal fallback."
      );
    }
    const nfetch = remote.fetches ? Object.keys(remote.fetches).length : 0;
    return recordLastRun(
      "run",
      "Cortex run_dag accepted. Actor " + (remote.actor || "?") + ". Fetches " + nfetch + ". Audit has node outputs.",
      remote
    );
  }
  if (C.ghost || !cortexOrigin()) {
    return ghostRun();
  }
  return liveOrGhost(true);
}

function chatSay(role, text) {
  const log = document.getElementById("chat-log");
  if (!log) return;
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
  if (!t) return "Say the object, point, action, or run all.";
  if (t === "help") {
    return "Chat a whole desk: warehouse, venue/CRM, define data, govern agents, business insights, client company, or a police suspect desk. Pages compile is a local template (no LLM). Cortex generate needs /cortex + ov_ key. Chat catalog after issue key. Or: seed warehouse|venue|suspect|define|govern|insights|understand. issue key. ghost on/off. propose 3. maximize. run all. why. add <kind>. Ctrl+/ toggles chat.";
  }
  const seed = t.match(/^seed (warehouse|venue|suspect|foundry|define|govern|insights|understand)$/);
  if (seed && C.applySeed) {
    const id = seed[1] === "foundry" ? "warehouse" : seed[1];
    C.applySeed(id);
    return "Loaded " + seed[1] + " seed. Ghost on. Propose 3 still ranks approaches.";
  }
  if (t === "catalog" || t === "browse the catalog") {
    if (!cortexOrigin()) {
      return "Catalog is Cortex. Open " + cortexLocalHint() + " then: issue key. catalog.";
    }
    const remote = await cortexGet("/cortex/constructor/catalog");
    if (!remote || remote.ok === false) {
      return (
        "Catalog unread (" +
        ((remote && (remote.detail || remote.status || remote.error)) || "offline") +
        "). Issue key first."
      );
    }
    return remote.answer || "Catalog empty.";
  }
  if (/^issue( key)?$/.test(t) || /generate key|openvault key/.test(t)) {
    return await issueOpenVaultKey();
  }
  const setObj = t.match(/^set object (\S+)$/);
  if (setObj) {
    if (!C.OBJECTS[setObj[1]]) {
      return "Object must be a Cortex ontology table: " + Object.keys(C.OBJECTS).join(", ") + ".";
    }
    return C.patchSelected("object_type", setObj[1])
      ? "Object " + setObj[1] + " on selected node."
      : "Select a node first.";
  }
  const setPoint = t.match(/^set point (\S+)$/);
  if (setPoint) {
    const node = C.selected && C.selected();
    if (!node) return "Select a node first.";
    const obj = node.object_type && C.OBJECTS[node.object_type] ? node.object_type : "inventory";
    if (!C.OBJECTS[obj] || !C.OBJECTS[obj].points[setPoint[1]]) {
      return "Point not on " + obj + ". " + Object.keys((C.OBJECTS[obj] && C.OBJECTS[obj].points) || {}).join(", ");
    }
    return C.patchSelected("data_point", setPoint[1]) ? "Data point " + setPoint[1] + "." : "Select a node first.";
  }
  const setType = t.match(/^set type (\S+)$/);
  if (setType) {
    return C.patchSelected("data_type", setType[1]) ? "Data type " + setType[1] + "." : "Select a node first.";
  }
  const setAct = t.match(/^set action (\S+)$/);
  if (setAct) {
    if (C.ACTIONS.indexOf(setAct[1]) < 0) {
      return "Action must be export_pptx, item.intake, agent.checked, image.enhance, or suspect.match.";
    }
    return C.patchSelected("action_type", setAct[1]) ? "Action " + setAct[1] + "." : "Select a node first.";
  }
  const setFetch = t.match(/^set fetch (.+)$/);
  if (setFetch) {
    return C.patchSelected("fetch_from", setFetch[1].trim())
      ? "Fetch/place " + setFetch[1].trim() + "."
      : "Select a node first.";
  }
  if (/^set tier (t[01])$/.test(t) === false && t.match(/^set tier /)) {
    return "Tier must be T0 or T1 (ModelRouter). Not a network load balancer.";
  }
  const setTier = t.match(/^set tier (t[01])$/);
  if (setTier) {
    const tier = setTier[1].toUpperCase();
    return C.patchSelected("tier", tier) ? "Router tier " + tier + " (ModelRouter)." : "Select a node first.";
  }
  if (/^set stream on$/.test(t)) {
    return C.patchSelected("stream", true) ? "Stream flag on. Cortex /dms/streams. Pages cannot stream." : "Select a node.";
  }
  if (/^set stream off$/.test(t)) {
    return C.patchSelected("stream", false) ? "Stream flag off." : "Select a node.";
  }
  if (/ghost off/.test(t)) {
    C.setGhost(false);
    return "Ghost off. run all calls Cortex run_dag. Pages still cannot fetch.";
  }
  if (/ghost on|ghost mode/.test(t)) {
    C.setGhost(true);
    return "Ghost on. Dry-run only.";
  }
  if (/^ghost( run)?$/.test(t) || /dry.?run/.test(t)) {
    C.setGhost(true);
    return await ghostRun();
  }
  if (/automate off|stop automate/.test(t)) {
    setAutomate(false);
    return "Automate off.";
  }
  if (/automate on|automate forever|^automate$/.test(t)) {
    setAutomate(true);
    return "Automate on. Ghost-run every 15s. Live run_dag stays the Run button.";
  }
  if (/propose|bakeoff|approach/.test(t) && !/openclaw/.test(t)) {
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
  if (/^foundry( path)?$/.test(t) || /ontology path/.test(t) || /^create app$/.test(t)) {
    C.loadCompilePath();
    C.setGhost(true);
    const ranked = await rankApproaches();
    return (
      "Loaded connector -> ontology -> insight -> foundry -> app + export_pptx. Ghost on. Winner: " +
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
  if (/^fetch$/.test(t) || /^fetch now$/.test(t)) {
    if (!cortexOrigin()) {
      return "Fetch is Cortex only (POST /cortex/constructor/fetch). Pages never fetch. Open " + cortexLocalHint() + ".";
    }
    const node = C.selected && C.selected();
    if (!node) return "Select a node first.";
    const remote = await cortexPost("/cortex/constructor/fetch", { nodes: [node], edges: [] });
    C.showAudit({ mode: "cortex-fetch", remote: remote });
    if (!remote || remote.ok === false) {
      return (
        "Fetch failed (" +
        ((remote && (remote.status || remote.detail || remote.error)) || "offline") +
        "). No internal fallback."
      );
    }
    const s = remote.slice || {};
    return (
      "Fetched " +
      (s.table || "none") +
      " " +
      (s.data_point || "") +
      ": " +
      (s.row_count || 0) +
      " rows" +
      (s.error ? " (" + s.error + ")" : "") +
      "."
    );
  }
  if (/^run all$/.test(t) || /run api|execute all|live run/.test(t)) {
    return await liveOrGhost(true);
  }
  if (/^run$/.test(t) || (/execute/.test(t) && !/all/.test(t))) {
    return await liveOrGhost(false);
  }
  if (/^why$/.test(t) || /decision layer|press (it|node)|why this/.test(t)) {
    return await pressNode();
  }
  if (/^bench$/.test(t) || /openclaw|testbench|accuracy/.test(t)) {
    const ranked = await rankApproaches();
    let live = "";
    if (cortexOrigin()) {
      const remote = await cortexPost("/cortex/constructor/fetch", {
        nodes: [
          {
            id: "bench",
            kind: "connector",
            object_type: "inventory",
            data_point: "sku",
            fetch_from: "warehouse.inventory",
          },
        ],
        edges: [],
      });
      if (remote && remote.ok && remote.slice) {
        live = " Live DuckDB inventory rows: " + (remote.slice.row_count || 0) + ".";
      } else {
        live = " Live warehouse fetch failed (no fake score).";
      }
    }
    return (
      "Cortex G1 bakeoff (repo, not a live OpenClaw rerun): static DAG 4.1 vs OpenClaw 1.7. DMS golden 36/36, 0 confident-wrong." +
      live +
      " This graph winner: " +
      ranked[0].name +
      " score " +
      ranked[0].score +
      ". Constructor does not invent an OpenClaw host."
    );
  }
  return await generateFromChat(text);
}

function objectsInPrompt(text) {
  const low = text.toLowerCase();
  const rows = [
    ["places", ["maps", "nearby", "geo", "latitude", "longitude", "place"]],
    ["venues", ["club", "clubs", "venue", "venues", "nightlife", "restaurant"]],
    ["contacts", ["contact", "contacts"]],
    ["leads", ["customer", "customers", "lead", "leads", "prospect"]],
    ["inventory", ["inventory", "sku", "stock", "warehouse"]],
    ["suppliers", ["supplier", "vendor"]],
    ["locations", ["location", "site", "bin"]],
    ["shipments", ["shipment", "consignment", "carrier"]],
    ["transactions", ["transaction", "txn", "movement"]],
    ["alerts", ["alert", "alarm"]],
    ["incidents", ["incident", "case desk", "case file", "ops desk"]],
    ["images", ["image", "images", "footage", "cctv", "camera"]],
    ["suspects", ["suspect", "watchlist", "police"]],
    ["matches", ["match", "face match", "similarity"]],
  ];
  const found = [];
  for (const row of rows) {
    if (row[1].some((w) => low.indexOf(w) >= 0) && found.indexOf(row[0]) < 0) found.push(row[0]);
  }
  if (found.indexOf("venues") >= 0 && found.indexOf("places") < 0) found.unshift("places");
  return found;
}

function refusePrompt(text) {
  const low = (text || "").toLowerCase();
  const hits = [
    "prostitut",
    "escort",
    "brothel",
    "sex work",
    "sexworker",
    "stalk",
    "doxx",
    "scrape the internet",
    "scrape internet",
    "scrap intenr",
    "public webcam",
    "scrape camera",
  ];
  return hits.some(function (h) {
    return low.indexOf(h) >= 0;
  });
}

function isSuspectDesk(low) {
  return /suspect|watchlist|face|cctv|camera|police|comfy|image enhance|enhance image|label face/.test(low || "");
}

function fetchPlaceFor(obj) {
  if (obj === "places") return "maps.places";
  if (obj === "venues") return "maps.venues";
  if (obj === "contacts") return "crm.contacts";
  if (obj === "leads") return "crm.leads";
  if (obj === "incidents") return "db.incidents";
  if (obj === "images") return "owned.images";
  if (obj === "suspects") return "owned.watchlist";
  if (obj === "matches") return "owned.matches";
  return "warehouse." + obj;
}

function generateLocal(prompt) {
  const C = window.Constructor;
  const low = prompt.toLowerCase();
  if (refusePrompt(prompt)) {
    return {
      ok: false,
      refused: true,
      summary:
        "Refused. Constructor will not compile internet stalking, doxxing, public-webcam scrape, or sex-work targeting. A police suspect desk is allowed on owned images + owned.watchlist. Chat: police suspect desk, local model enhance, match watchlist.",
    };
  }
  let objects = objectsInPrompt(prompt);
  let assumed = false;
  if (!objects.length) {
    objects = ["inventory"];
    assumed = true;
  }
  const points = {
    inventory: "sku",
    suppliers: "supplier_id",
    locations: "location_id",
    shipments: "shipment_id",
    transactions: "txn_id",
    alerts: "alert_id",
    places: "place_id",
    venues: "venue_id",
    contacts: "contact_id",
    leads: "lead_id",
    incidents: "incident_id",
    images: "image_id",
    suspects: "suspect_id",
    matches: "match_id",
  };
  let sourceKind = "place";
  if (/cloud|sign[- ]?in|oauth/.test(low)) sourceKind = "cloud";
  if (/database|db link|postgres|add link|db\./.test(low)) sourceKind = "database";
  if (/local model|comfy|onnx|ollama/.test(low)) sourceKind = "local_model";
  if (/online api|http api|replicate/.test(low)) sourceKind = "online_api";
  const suspectish = isSuspectDesk(low);
  if (suspectish) {
    objects = ["images", "suspects", "matches"];
    assumed = false;
  }
  let action = "export_pptx";
  if (low.indexOf("intake") >= 0 && low.indexOf("client") < 0) action = "item.intake";
  else if (low.indexOf("agent.checked") >= 0 || (low.indexOf("check") >= 0 && low.indexOf("agent") >= 0)) {
    action = "agent.checked";
  } else if (/govern agents?|agent governance/.test(low)) {
    action = "agent.checked";
  } else if (suspectish || /suspect\.match/.test(low)) {
    action = "suspect.match";
  } else if (/image\.enhance|enhance/.test(low) && suspectish) {
    action = "suspect.match";
  }
  const verify = /verify|audit|hypothes|claim|fact-?check/.test(low);
  const agentish = /single agent|one agent|worker loop/.test(low);
  const clientish =
    (low.indexOf("understand") >= 0 && low.indexOf("company") >= 0) ||
    /client company|onboard client|client intake|semantic layer/.test(low);
  const foundryish =
    clientish ||
    /foundry|create app|whole (app|workflow|desk)|generate whole|pptx|export|ontology|insight|define data|\bapp\b|maps|club|venue|contact|customer|incident|case desk|suspect|face|cctv|enhance|comfy|police|watchlist/.test(
    low
  );
  const governish = /govern agents?|agent governance/.test(low);
  const venueish = objects.some(function (o) {
    return o === "places" || o === "venues" || o === "contacts" || o === "leads";
  });
  let pattern = "orchestrator_subagent";
  let kinds = ["ingest", "connector", "ontology", "insight", "foundry", "app", "tool_call"];
  let enhanceBind = "local_model";
  if (/online api|http api|replicate/.test(low)) enhanceBind = "online_api";
  if (suspectish) {
    kinds = ["ingest", "enhance", "ontology", "insight", "foundry", "app", "tool_call"];
    if (sourceKind === "local_model" || sourceKind === "online_api") enhanceBind = sourceKind;
    sourceKind = "database";
  } else if (verify && !foundryish && !governish) {
    pattern = "generator_verifier";
    kinds = ["ingest", "hypothesize", "audit"];
  } else if (governish && !foundryish) {
    pattern = "generator_verifier";
    kinds = ["ingest", "connector", "ontology", "agent", "audit"];
  } else if (agentish && !foundryish) {
    pattern = "single_agent";
    kinds = ["ingest", "agent", "audit"];
  }
  const firstObj = objects[0];
  const sourcePlace =
    sourceKind === "cloud"
      ? "cloud.signed_in"
      : sourceKind === "local_model"
        ? "local.model"
        : sourceKind === "online_api"
          ? "api.enhance"
          : fetchPlaceFor(firstObj);
  const doing = {
    ingest: suspectish
      ? "Hop 0. Load owned images from owned.images (station archive or operator upload). Ghost on Pages. No write. No internet scrape."
      : clientish
        ? "Read customer terms into glossary rows. Ghost. No invented DuckDB."
      : "Hop 0. Load " +
        objects.join("/") +
        " rows from " +
        sourcePlace +
        " (" +
        sourceKind +
        "). Ghost on Pages. No write.",
    connector:
      sourceKind === "cloud"
        ? "Ghost cloud sign-in. Bind the signed-in catalog to an object. No OAuth. No fetch on Pages."
        : sourceKind === "database"
          ? "Bind a database link the operator pasted. Ghost on Pages. No live driver."
          : venueish
            ? "Bind Place/Venue fields to Cortex objects. Ghost on Pages. No live scrape."
            : "First-party Cortex input bound to the ingested object.",
    enhance:
      "Comfy-style enhance. Bind a " +
      enhanceBind +
      ". Ghost on Pages. Distill Comfy, do not clone. Zoom/refresh/improve quality. No public scrape.",
    ontology: suspectish
      ? "Object types images, suspects, matches. Links image_at_location, suspect_image, match_of_image, match_of_suspect. Owned watchlist only."
      : venueish
        ? "Object types " +
          objects.join(", ") +
          ". Links venue_at_place, contact_at_venue, lead_of_contact."
        : clientish
          ? "SWAP packs/dms/semantic_layer.yaml then object_types.yaml name-parity. P1 parked."
        : objects.indexOf("incidents") >= 0
          ? "Object types " + objects.join(", ") + ". Link incident_at_location. Owned rows."
          : "Cortex ontology objects/links/actions. Not a custom type picker.",
    insight: suspectish
      ? "Cite enhanced image vs owned.watchlist. Score is a claim, steward reviews. Not a conviction."
      : venueish
        ? "Cite nearby venues by Place lat/lng, contacts at those venues, leads from contacts."
        : clientish
          ? "Cite catalog_answer + ledger. Crew wakes keep 24/7 meaning."
        : objects.indexOf("incidents") >= 0
          ? "Cite incident rows + location links. What you may claim from the owned ledger."
          : "Cite ontology + ledger. What you may claim from those objects.",
    foundry: clientish
      ? "Compile a DMS shell app from ontology insights. Extra tab/route, not a second SPA."
      : "Compile insights into a governed Cortex app.",
    app: "Runnable output. Hosted inside Cortex at /cortex/constructor/.",
    tool_call: suspectish
      ? "F8 governed write. requires_confirm. Action suspect.match against owned.watchlist."
      : "F8 governed write. requires_confirm. Real tool is export_pptx.",
    hypothesize: "Surface a testable claim.",
    audit: "Why this node exists. DETERMINISTIC_RULE, not a second EMIT.",
    agent: "AGENT_TASK loop. One bounded worker.",
  };
  const nodes = kinds.map(function (kind, i) {
    let obj = objects[0];
    if (kind === "ontology" && objects[1]) obj = objects[1];
    if (kind === "insight" && objects[2]) obj = objects[2];
    if (kind === "tool_call" && objects[objects.length - 1]) obj = objects[objects.length - 1];
    const meta = (C.KINDS && C.KINDS[kind]) || {};
    const node = {
      id: "g" + (i + 1),
      kind: kind,
      x: 32 + (i % 4) * 208,
      y: 48 + Math.floor(i / 4) * 168,
      note: doing[kind] || meta.note || kind,
      doing: doing[kind] || meta.note || kind,
      persona: meta.persona || "",
      tier: "T0",
      stream: false,
    };
    if (kind === "ingest" || kind === "connector" || kind === "ontology" || kind === "tool_call" || kind === "insight" || kind === "enhance") {
      node.object_type = obj;
      node.data_point = points[obj] || "sku";
      node.data_type = "string";
      node.fetch_from = sourceKind === "cloud" ? "cloud.signed_in" : fetchPlaceFor(obj);
      node.source_kind = sourceKind;
      node.source_link = sourceKind === "cloud" ? "signed-in" : sourceKind === "database" ? node.fetch_from : "";
    }
    if (kind === "enhance") {
      node.object_type = "images";
      node.data_point = "image_id";
      node.source_kind = enhanceBind;
      node.fetch_from = enhanceBind === "online_api" ? "api.enhance" : "local.model";
      node.source_link = enhanceBind === "online_api" ? "api.enhance" : "local://enhance";
      node.action_type = "image.enhance";
    }
    if (kind === "tool_call" || kind === "foundry") node.action_type = action;
    if (kind === "app") node.action_type = "emit";
    return node;
  });
  const edges = [];
  for (let i = 0; i < nodes.length - 1; i++) edges.push({ from: nodes[i].id, to: nodes[i + 1].id });
  return {
    ok: true,
    pattern: pattern,
    assumed_object: assumed,
    objects: objects,
    action: action,
    engine: "local_template",
    nodes: nodes,
    edges: edges,
    summary:
      "Compiled " +
      nodes.length +
      " Cortex nodes (" +
      pattern +
      "). " +
      (assumed ? "Assumed inventory. " : "Objects " + objects.join(", ") + ". ") +
      "Action " +
      action +
      ". Source " +
      sourceKind +
      ". Engine local_template (no LLM).",
  };
}

window.Constructor = window.Constructor || {};
window.Constructor.generateLocal = generateLocal;

async function generateFromChat(text) {
  const C = window.Constructor;
  if (refusePrompt(text)) {
    return generateLocal(text).summary;
  }
  let graph = null;
  let via = "local_template";
  if (cortexOrigin()) {
    const remote = await cortexPost("/cortex/constructor/generate", { prompt: text });
    if (remote && remote.ok && Array.isArray(remote.nodes) && remote.nodes.length) {
      const localObjs = objectsInPrompt(text);
      if (remote.assumed_object && localObjs.length) graph = null;
      else {
        graph = remote;
        via = "cortex_generate";
      }
    }
  }
  if (!graph) {
    graph = generateLocal(text);
    via = "local_template";
  }
  if (!graph.ok) return graph.summary;
  C.replaceGraph(graph.nodes, graph.edges);
  let task = C.intendedTask ? C.intendedTask(C.getState()) : { ok: true, missing: [] };
  if (!task.ok && via === "local_template" && /define data|business insights/.test(text.toLowerCase())) {
    graph = generateLocal("ingest warehouse inventory then " + text);
    via = "local_template";
    C.replaceGraph(graph.nodes, graph.edges);
    task = C.intendedTask ? C.intendedTask(C.getState()) : task;
  }
  await rankApproaches();
  const first = C.getState().nodes[0];
  const prefix =
    via === "cortex_generate"
      ? "Cortex generate. "
      : "Local template (no LLM). Cortex generate needs /cortex + ov_ key. ";
  const taskLine = task.ok
    ? " Intended task PASS."
    : " Intended task FAIL: " + (task.missing || []).join("; ") + ". Redo compile.";
  const summary = prefix + (graph.summary || "Compiled the graph.") + taskLine;
  if (first) C.showDecision({ node: first, response: summary });
  return summary;
}

async function pressNode() {
  const C = window.Constructor;
  const node = C.selected && C.selected();
  if (!node) return "Select or click a node first.";
  const ir = compileIR(C.getState());
  const compiled = (ir.nodes || []).find((n) => n.id === node.id) || {};
  let remote = null;
  if (cortexOrigin()) {
    remote = await cortexPost("/cortex/constructor/decision", {
      node_id: node.id,
      nodes: C.getState().nodes,
      edges: C.getState().edges,
    });
  }
  const remoteOk = remote && remote.ok !== false && remote.cortex_kind;
  const layer = {
    mode: "cortex-decision",
    node_id: node.id,
    constructor_kind: node.kind,
    cortex_kind: (remoteOk && remote.cortex_kind) || compiled.kind || CORTEX_KIND[node.kind],
    is_emit: ir.output_node_id === node.id,
    is_entry: ir.entry_node_id === node.id,
    requires_confirm: node.kind === "tool_call",
    would_write: node.kind === "tool_call" || node.kind === "app",
    tier: node.tier || "T0",
    object_type: node.object_type || null,
    data_point: node.data_point || null,
    action_type: node.action_type || null,
    fetch_from: node.fetch_from || null,
    tool_name: remote && remote.tool_name,
    approach: C.lastRanking && C.lastRanking[0] ? C.lastRanking[0].id : null,
    remote: remote,
  };
  const response = remoteOk
    ? "Cortex compile " + layer.cortex_kind + (layer.is_emit ? " EMIT" : "") + "."
    : "Local compile " + layer.cortex_kind + (cortexOrigin() ? " (engine decision missed)" : " (Pages, no fetch)") + ".";
  C.showDecision({
    node: node,
    cortex_kind: layer.cortex_kind,
    is_emit: layer.is_emit,
    tool_name: layer.tool_name,
    response: response,
    openDialog: true,
    raw: layer,
  });
  const rec = remote && remote.recommendation && remote.recommendation.pattern;
  return (
    "DOING " +
    (node.doing || node.kind) +
    ". ACTION " +
    (node.action_type || "none") +
    ". APP " +
    (layer.is_emit ? "EMIT" : "downstream") +
    ". CODE " +
    (layer.cortex_kind || "?") +
    ". RESPONSE " +
    response +
    (rec ? " Pattern " + rec : "")
  );
}

function bindChat() {
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  function keepChat() {
    if (window.Constructor && window.Constructor.ensureChatOpen) window.Constructor.ensureChatOpen();
  }
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = input.value;
    if (!text.trim()) return;
    keepChat();
    chatSay("user", text);
    input.value = "";
    const send = form.querySelector("button[type=submit]");
    if (send) send.disabled = true;
    try {
      const reply = await handleChat(text);
      chatSay("assistant", reply);
    } finally {
      if (send) send.disabled = false;
    }
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  document.getElementById("ghost-toggle").addEventListener("click", () => {
    const C = window.Constructor;
    C.setGhost(!C.ghost);
    keepChat();
    chatSay("assistant", C.ghost ? "Ghost on." : "Ghost off.");
  });
  const autoBtn = document.getElementById("automate");
  if (autoBtn) {
    autoBtn.addEventListener("click", () => {
      const C = window.Constructor;
      setAutomate(!C.automate);
      keepChat();
      chatSay(
        "assistant",
        C.automate
          ? cortexOrigin()
            ? "Automate on. Live POST /cortex/constructor/run after each finish, then 15s. Pages stay ghost."
            : "Automate on. Ghost-run every 15s. Live run is Cortex only."
          : "Automate off."
      );
    });
  }
  document.getElementById("propose").addEventListener("click", async () => {
    keepChat();
    chatSay("assistant", await handleChat("propose 3"));
  });
  document.getElementById("maximize").addEventListener("click", async () => {
    keepChat();
    chatSay("assistant", await handleChat("maximize"));
  });
  const runBtn = document.getElementById("run-graph");
  if (runBtn) {
    runBtn.addEventListener("click", async () => {
      keepChat();
      chatSay("assistant", await handleChat("run"));
    });
  }
  const issueBtn = document.getElementById("issue-key");
  if (issueBtn) {
    if (!cortexOrigin()) issueBtn.hidden = true;
    issueBtn.addEventListener("click", async () => {
      keepChat();
      chatSay("assistant", await issueOpenVaultKey());
    });
  }
  const keyBox = document.getElementById("cortex-key");
  if (keyBox && cortexOrigin()) {
    keyBox.addEventListener("change", async () => {
      const key = keyBox.value.trim();
      if (!key) return;
      const remote = await bindSession(key);
      if (remote && remote.ok === false) {
        setOvStatus("OpenVault: key refused (" + (remote.status || remote.detail || remote.error || "401") + ").");
        return;
      }
      setOvStatus("OpenVault: session bound. Catalog + fetch + run all are live.");
      await loadOntology();
    });
  }
  chatSay("assistant", "Type a desk. Ctrl+/ chat.");
  window.Constructor.pressNode = pressNode;
  loadOntology();
  rankApproaches();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", bindChat);
} else {
  bindChat();
}
