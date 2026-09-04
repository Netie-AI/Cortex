const STORAGE_KEY = "netie.constructor.v2";
const KINDS = {
  ingest: { label: "Ingest", note: "Read operations into the graph." },
  connector: { label: "Connector", note: "First-party Cortex input. No n8n." },
  ontology: { label: "Ontology", note: "Object/link/action types on this graph." },
  insight: { label: "Insight", note: "Cite ontology + ledger." },
  foundry: { label: "Foundry", note: "Compile a governed Cortex app from insights." },
  app: { label: "App", note: "Emit the app a stranger can run inside Cortex." },
  agent: { label: "Agent", note: "AGENT_TASK loop. One bounded worker." },
  hypothesize: { label: "Hypothesize", note: "Surface a testable claim." },
  improve: { label: "Improve", note: "Change a product from the claim." },
  audit: { label: "Audit", note: "Show why this node exists. DETERMINISTIC_RULE, not a second EMIT." },
  tool_call: { label: "Tool call", note: "Governed write. requires_confirm." },
};

const nodesEl = document.getElementById("nodes");
const wiresEl = document.getElementById("wires");
const inspectJson = document.getElementById("inspect-json");
const inspectEmpty = document.getElementById("inspect-empty");
const inspectForm = document.getElementById("inspect-form");

const OBJECTS = {
  inventory: {
    points: {
      sku: "string",
      sku_name: "string",
      category: "string",
      supplier_id: "string",
      location_id: "string",
      storage_bin: "string",
      quantity_kg: "number",
      reorder_level_kg: "number",
      unit_cost_myr: "number",
      last_restocked: "date",
      expiry_date: "date",
      is_hazardous: "boolean",
    },
  },
  suppliers: {
    points: {
      supplier_id: "string",
      supplier_name: "string",
      country: "string",
      lead_time_days: "integer",
      payment_terms: "string",
      last_audit_date: "date",
      risk_score: "number",
    },
  },
};
const ACTIONS = ["export_pptx", "item.intake", "agent.checked"];
const TIERS = ["T0", "T1"];

const state = load() || sample();
let selectedId = state.nodes[0] ? state.nodes[0].id : null;
let armedPort = null;
let drag = null;

function sample() {
  return foundrySample();
}

function foundrySample() {
  return {
    nodes: [
      {
        id: "c1",
        kind: "connector",
        x: 32,
        y: 48,
        object_type: "inventory",
        data_point: "sku",
        data_type: "string",
        fetch_from: "warehouse.inventory",
        tier: "T0",
        stream: false,
        note: "First-party Cortex input. No n8n. WhatsApp stays a draft, not a send.",
      },
      {
        id: "o1",
        kind: "ontology",
        x: 240,
        y: 48,
        object_type: "suppliers",
        data_point: "supplier_id",
        data_type: "string",
        fetch_from: "warehouse.suppliers",
        note: "Cortex ontology objects/links/actions. Not a custom type picker.",
      },
      {
        id: "i1",
        kind: "insight",
        x: 448,
        y: 48,
        object_type: "Insight",
        note: "Cite ontology + ledger. What you may claim from those objects.",
      },
      {
        id: "f1",
        kind: "foundry",
        x: 240,
        y: 200,
        action_type: "export_pptx",
        note: "Compile insights into a governed Cortex app. Not an Activepieces clone.",
      },
      {
        id: "a1",
        kind: "app",
        x: 448,
        y: 200,
        action_type: "emit",
        note: "Runnable output. Hosted inside Cortex at /cortex/constructor/.",
      },
      {
        id: "g1",
        kind: "audit",
        x: 32,
        y: 200,
        note: "Why this node exists. Ghost ledgers would-call, not a second EMIT.",
      },
      {
        id: "t1",
        kind: "tool_call",
        x: 656,
        y: 200,
        action_type: "export_pptx",
        object_type: "inventory",
        data_point: "sku",
        data_type: "string",
        tier: "T0",
        note: "F8 governed write. requires_confirm. Only real tool on this pack is export_pptx.",
      },
    ],
    edges: [
      { from: "c1", to: "o1" },
      { from: "o1", to: "i1" },
      { from: "i1", to: "f1" },
      { from: "f1", to: "a1" },
      { from: "f1", to: "g1" },
      { from: "f1", to: "t1" },
    ],
  };
}

function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed.nodes) || !Array.isArray(parsed.edges)) return null;
    return parsed;
  } catch {
    return null;
  }
}

function save() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

function uid() {
  return "n" + Math.random().toString(36).slice(2, 8);
}

function render() {
  nodesEl.innerHTML = "";
  for (const node of state.nodes) {
    const el = document.createElement("article");
    el.className = "node" + (node.id === selectedId ? " selected" : "");
    el.dataset.id = node.id;
    el.style.left = node.x + "px";
    el.style.top = node.y + "px";
    const meta = KINDS[node.kind] || { label: node.kind };
    el.innerHTML =
      '<div class="kind">' +
      node.kind.toUpperCase() +
      "</div><h2>" +
      meta.label +
      '</h2><div class="ports">' +
      '<button type="button" class="port" data-port="in" aria-label="input port"></button>' +
      '<button type="button" class="port" data-port="out" aria-label="output port"></button>' +
      "</div>";
    nodesEl.appendChild(el);
  }
  drawWires();
  showInspect();
}

function nodeCenter(id, port) {
  const el = nodesEl.querySelector('[data-id="' + id + '"]');
  if (!el) return { x: 0, y: 0 };
  const r = el.getBoundingClientRect();
  const stage = document.getElementById("stage").getBoundingClientRect();
  const x = r.left - stage.left + (port === "out" ? r.width - 18 : 18);
  const y = r.top - stage.top + r.height - 18;
  return { x, y };
}

function drawWires() {
  const stage = document.getElementById("stage");
  wiresEl.setAttribute("width", String(stage.clientWidth));
  wiresEl.setAttribute("height", String(stage.clientHeight));
  wiresEl.setAttribute("viewBox", "0 0 " + stage.clientWidth + " " + stage.clientHeight);
  wiresEl.setAttribute("preserveAspectRatio", "none");
  const parts = [];
  for (const edge of state.edges) {
    const a = nodeCenter(edge.from, "out");
    const b = nodeCenter(edge.to, "in");
    const mid = (a.x + b.x) / 2;
    parts.push(
      '<path fill="none" stroke="#e5e5e5" stroke-width="1.25" d="M' +
        a.x +
        " " +
        a.y +
        " C" +
        mid +
        " " +
        a.y +
        " " +
        mid +
        " " +
        b.y +
        " " +
        b.x +
        " " +
        b.y +
        '" />'
    );
  }
  wiresEl.innerHTML = parts.join("");
}

function showInspect() {
  const node = state.nodes.find((n) => n.id === selectedId);
  if (!node) {
    inspectJson.hidden = true;
    inspectForm.hidden = true;
    inspectEmpty.hidden = false;
    return;
  }
  inspectEmpty.hidden = true;
  inspectJson.hidden = true;
  inspectForm.hidden = false;
  const obj = node.object_type && OBJECTS[node.object_type] ? node.object_type : "inventory";
  const points = OBJECTS[obj].points;
  const point = node.data_point && points[node.data_point] ? node.data_point : Object.keys(points)[0];
  const dtype = node.data_type || points[point];
  const action = ACTIONS.indexOf(node.action_type) >= 0 ? node.action_type : "export_pptx";
  const tier = TIERS.indexOf(node.tier) >= 0 ? node.tier : "T0";
  inspectForm.innerHTML =
    fieldSelect("object_type", "Object (ontology)", Object.keys(OBJECTS), obj) +
    fieldSelect("data_point", "Data point", Object.keys(points), point) +
    fieldSelect("data_type", "Data type", ["string", "number", "integer", "boolean", "date"], dtype) +
    fieldSelect("action_type", "Action", ACTIONS, action) +
    '<label>Fetch / place</label><input name="fetch_from" value="' +
    escapeAttr(node.fetch_from || "") +
    '" placeholder="warehouse.inventory" />' +
    fieldSelect("tier", "Router tier (ModelRouter, not a network LB)", TIERS, tier) +
    fieldSelect("stream", "Stream (/dms/streams flag)", ["false", "true"], node.stream ? "true" : "false") +
    '<p class="hint">Object/point/type write onto the DAG. Fetch/place is DuckDB (warehouse.inventory). Action export_pptx is F8; item.intake and agent.checked are events. Tier is ModelRouter T0/T1 on tool/agent, not a network LB. Stream lists /dms/streams. Pages never fetch.</p>';
}

function fieldSelect(name, label, values, current) {
  return (
    "<label>" +
    label +
    '</label><select name="' +
    name +
    '">' +
    values
      .map(function (v) {
        return (
          '<option value="' +
          v +
          '"' +
          (v === current ? " selected" : "") +
          ">" +
          v +
          "</option>"
        );
      })
      .join("") +
    "</select>"
  );
}

function escapeAttr(value) {
  return String(value).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

function patchSelected(field, value) {
  const node = state.nodes.find((n) => n.id === selectedId);
  if (!node) return false;
  if (field === "stream") node.stream = value === true || value === "true";
  else node[field] = value;
  if (field === "object_type" && OBJECTS[value]) {
    const first = Object.keys(OBJECTS[value].points)[0];
    node.data_point = first;
    node.data_type = OBJECTS[value].points[first];
  }
  if (field === "data_point" && OBJECTS[node.object_type] && OBJECTS[node.object_type].points[value]) {
    node.data_type = OBJECTS[node.object_type].points[value];
  }
  save();
  render();
  return true;
}

inspectForm.addEventListener("change", (event) => {
  const el = event.target;
  if (!el || !el.name) return;
  patchSelected(el.name, el.value);
});

document.querySelectorAll("[data-add]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const kind = btn.getAttribute("data-add");
    const node = {
      id: uid(),
      kind,
      x: 80 + state.nodes.length * 16,
      y: 80 + state.nodes.length * 16,
      note: KINDS[kind].note,
    };
    state.nodes.push(node);
    selectedId = node.id;
    save();
    render();
  });
});

nodesEl.addEventListener("pointerdown", (event) => {
  const nodeEl = event.target.closest(".node");
  if (!nodeEl) return;
  const port = event.target.closest(".port");
  const id = nodeEl.dataset.id;
  selectedId = id;

  if (port) {
    event.stopPropagation();
    const side = port.getAttribute("data-port");
    if (!armedPort) {
      armedPort = { id, side };
      port.classList.add("armed");
      showInspect();
      return;
    }
    const a = armedPort.side === "out" ? armedPort.id : id;
    const b = armedPort.side === "out" ? id : armedPort.id;
    const valid =
      a !== b &&
      ((armedPort.side === "out" && side === "in") || (armedPort.side === "in" && side === "out"));
    if (valid && !state.edges.some((e) => e.from === a && e.to === b)) {
      state.edges.push({ from: a, to: b });
      save();
    }
    armedPort = null;
    render();
    return;
  }

  drag = {
    id,
    dx: event.clientX - nodeEl.getBoundingClientRect().left,
    dy: event.clientY - nodeEl.getBoundingClientRect().top,
  };
  nodeEl.setPointerCapture(event.pointerId);
  showInspect();
  for (const el of nodesEl.querySelectorAll(".node")) {
    el.classList.toggle("selected", el.dataset.id === selectedId);
  }
});

nodesEl.addEventListener("pointermove", (event) => {
  if (!drag) return;
  const node = state.nodes.find((n) => n.id === drag.id);
  const stage = document.getElementById("stage").getBoundingClientRect();
  node.x = Math.max(8, event.clientX - stage.left - drag.dx);
  node.y = Math.max(8, event.clientY - stage.top - drag.dy);
  const el = nodesEl.querySelector('[data-id="' + drag.id + '"]');
  el.style.left = node.x + "px";
  el.style.top = node.y + "px";
  drawWires();
});

nodesEl.addEventListener("pointerup", () => {
  if (drag) save();
  drag = null;
});

document.getElementById("export-json").addEventListener("click", () => {
  const blob = new Blob([JSON.stringify(state, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "constructor-graph.json";
  a.click();
  URL.revokeObjectURL(url);
});

document.getElementById("reset-graph").addEventListener("click", () => {
  const next = sample();
  state.nodes = next.nodes;
  state.edges = next.edges;
  selectedId = state.nodes[0].id;
  localStorage.removeItem(STORAGE_KEY);
  save();
  render();
});

window.addEventListener("resize", drawWires);

function cortexOrigin() {
  const host = location.hostname;
  const path = location.pathname;
  if (host === "app.netie.ai" && path.indexOf("/cortex") === 0) return true;
  if ((host === "127.0.0.1" || host === "localhost") && path.indexOf("/cortex") === 0) {
    return true;
  }
  return false;
}

function addNode(kind, x, y) {
  const meta = KINDS[kind];
  if (!meta) return null;
  const node = {
    id: uid(),
    kind,
    x: x != null ? x : 80 + state.nodes.length * 16,
    y: y != null ? y : 80 + state.nodes.length * 16,
    note: meta.note,
  };
  state.nodes.push(node);
  selectedId = node.id;
  save();
  render();
  return node;
}

function wire(from, to) {
  if (!state.nodes.some((n) => n.id === from) || !state.nodes.some((n) => n.id === to)) return false;
  if (from === to) return false;
  if (!state.edges.some((e) => e.from === from && e.to === to)) state.edges.push({ from, to });
  save();
  render();
  return true;
}

function setGhost(on) {
  window.Constructor.ghost = !!on;
  document.body.classList.toggle("ghost-mode", window.Constructor.ghost);
  const btn = document.getElementById("ghost-toggle");
  if (btn) btn.textContent = window.Constructor.ghost ? "Ghost on" : "Ghost off";
}

function showAudit(obj) {
  inspectEmpty.hidden = true;
  inspectForm.hidden = true;
  inspectJson.hidden = false;
  inspectJson.textContent = JSON.stringify(obj, null, 2);
}

function markGhostWalk(ids) {
  for (const el of nodesEl.querySelectorAll(".node")) {
    el.classList.toggle("ghosting", ids.indexOf(el.dataset.id) !== -1);
  }
}

function loadFoundryPath() {
  const next = foundrySample();
  state.nodes = next.nodes;
  state.edges = next.edges;
  selectedId = state.nodes[0].id;
  save();
  render();
}

function replaceGraph(nodes, edges) {
  if (!Array.isArray(nodes) || !nodes.length) return false;
  state.nodes = nodes;
  state.edges = Array.isArray(edges) ? edges : [];
  selectedId = state.nodes[0].id;
  save();
  render();
  return true;
}

function ensureKinds(kinds) {
  for (const kind of kinds) {
    if (!state.nodes.some((n) => n.kind === kind)) addNode(kind);
  }
}

function replaceCatalog(objects, actions) {
  Object.keys(OBJECTS).forEach(function (k) {
    delete OBJECTS[k];
  });
  Object.keys(objects || {}).forEach(function (k) {
    OBJECTS[k] = objects[k];
  });
  if (Array.isArray(actions) && actions.length) {
    ACTIONS.length = 0;
    for (let i = 0; i < actions.length; i++) ACTIONS.push(actions[i]);
  }
  render();
}

window.Constructor = {
  KINDS,
  OBJECTS,
  ACTIONS,
  ghost: true,
  getState: () => state,
  selected: () => state.nodes.find((n) => n.id === selectedId) || null,
  addNode,
  wire,
  setGhost,
  showAudit,
  markGhostWalk,
  loadFoundryPath,
  replaceGraph,
  ensureKinds,
  patchSelected,
  replaceCatalog,
  selectedId: () => selectedId,
  render,
  save,
};

const power = document.getElementById("power");
if (power) {
  power.textContent = cortexOrigin()
    ? "Powered by Cortex. Paste key, then fetch / run all. Ghost is dry-run. Production https://app.netie.ai/cortex is still LiteSpeed 404 until the host proxies this pack."
    : "Sketch (no fetch). Live run needs http://127.0.0.1:8012/cortex . https://app.netie.ai/cortex is still LiteSpeed 404.";
}
const keyBox = document.getElementById("cortex-key");
if (keyBox && !cortexOrigin()) keyBox.hidden = true;

setGhost(true);
render();
