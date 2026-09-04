const STORAGE_KEY = "netie.constructor.v4";
const CHAT_DOCK_KEY = "netie.constructor.chatdock.v2";
const SOURCE_KINDS = ["place", "cloud", "database", "local_model", "online_api"];

function ico(paths) {
  return (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
    paths +
    "</svg>"
  );
}

const KINDS = {
  ingest: {
    label: "Ingest",
    persona: "loader",
    color: "#7eb8ff",
    note: "Hop 0. Load rows from a place into an object. No write.",
    icon: ico(
      '<path d="M12 3v11"/><path d="M8 10l4 4 4-4"/><path d="M4 17h16v3H4z"/>'
    ),
  },
  connector: {
    label: "Connector",
    persona: "source",
    color: "#9ad7c2",
    note: "First-party Cortex input bound to an object.",
    icon: ico('<path d="M8 7v10"/><path d="M16 7v10"/><path d="M8 12h8"/><circle cx="8" cy="7" r="2"/><circle cx="16" cy="17" r="2"/>'),
  },
  ontology: {
    label: "Ontology",
    persona: "modeler",
    color: "#d4b4ff",
    note: "Object, link, and action types on this graph.",
    icon: ico('<circle cx="7" cy="8" r="3"/><circle cx="17" cy="8" r="3"/><circle cx="12" cy="17" r="3"/><path d="M10 9.5l2 5.5M14 9.5l-2 5.5M10 8h4"/>'),
  },
  insight: {
    label: "Insight",
    persona: "analyst",
    color: "#f0c36d",
    note: "Cite ontology + ledger. What you may claim.",
    icon: ico('<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>'),
  },
  foundry: {
    label: "Compile",
    persona: "compiler",
    color: "#e8a07a",
    note: "Compile insights into a governed Cortex app.",
    icon: ico('<path d="M4 18h16M6 18V9l6-4 6 4v9M9 18v-5h6v5"/>'),
  },
  app: {
    label: "App",
    persona: "operator",
    color: "#8ec8f0",
    note: "Emit the app a stranger can run inside Cortex.",
    icon: ico('<rect x="4" y="5" width="16" height="14" rx="2"/><path d="M4 9h16"/>'),
  },
  agent: {
    label: "Agent",
    persona: "worker",
    color: "#b7e08a",
    note: "AGENT_TASK loop. One bounded worker.",
    icon: ico('<circle cx="12" cy="8" r="3"/><path d="M5 20c1.5-3.5 4-5 7-5s5.5 1.5 7 5"/>'),
  },
  hypothesize: {
    label: "Hypothesize",
    persona: "skeptic",
    color: "#c9c27a",
    note: "Surface a testable claim.",
    icon: ico('<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 114 2c-.8.8-1.5 1.2-1.5 3"/><path d="M12 17.5h.01"/>'),
  },
  enhance: {
    label: "Enhance",
    persona: "enhancer",
    color: "#c4a0e8",
    note: "Comfy-style. Local model or online API. Ghost on Pages.",
    icon: ico('<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 14l3-3 2 2 3-4"/><circle cx="9" cy="8" r="1.2"/>'),
  },
  improve: {
    label: "Improve",
    persona: "editor",
    color: "#9fd0e8",
    note: "Change a product from the claim.",
    icon: ico('<path d="M12 3v18M8 8h3M13 12h3M8 16h3"/>'),
  },
  audit: {
    label: "Audit",
    persona: "steward",
    color: "#d0d0d0",
    note: "Why this node exists. DETERMINISTIC_RULE, not a second EMIT.",
    icon: ico('<path d="M12 3l8 4v6c0 5-3.5 7.5-8 9-4.5-1.5-8-4-8-9V7z"/>'),
  },
  tool_call: {
    label: "Tool call",
    persona: "writer",
    color: "#f2a3a3",
    note: "Governed write. requires_confirm.",
    icon: ico('<path d="M13 3l-2 8h6l-8 10 2-8H5z"/>'),
  },
};

const PERSONAS = ["loader", "source", "modeler", "analyst", "compiler", "operator", "worker", "skeptic", "editor", "enhancer", "steward", "writer"];
const ACTION_META = [
  { id: "export_pptx", objects: ["*"], label: "export_pptx (any object)" },
  { id: "item.intake", objects: ["inventory"], label: "item.intake (inventory)" },
  { id: "agent.checked", objects: ["*"], label: "agent.checked (ledger)" },
  { id: "image.enhance", objects: ["images", "matches"], label: "image.enhance (images)" },
  { id: "suspect.match", objects: ["suspects", "matches", "images"], label: "suspect.match (watchlist)" },
];


const nodesEl = document.getElementById("nodes");
const wiresEl = document.getElementById("wires");
const inspectCard = document.getElementById("inspect-card");
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
  locations: {
    points: {
      location_id: "string",
      location_name: "string",
      city: "string",
      latitude: "number",
      longitude: "number",
    },
  },
  places: {
    points: {
      place_id: "string",
      name: "string",
      locality: "string",
      latitude: "number",
      longitude: "number",
    },
  },
  venues: {
    points: {
      venue_id: "string",
      name: "string",
      category: "string",
      place_id: "string",
      website: "string",
    },
  },
  contacts: {
    points: {
      contact_id: "string",
      name: "string",
      role: "string",
      venue_id: "string",
      email: "string",
    },
  },
  leads: {
    points: {
      lead_id: "string",
      account: "string",
      status: "string",
      contact_id: "string",
    },
  },
  incidents: {
    points: {
      incident_id: "string",
      opened_at: "date",
      status: "string",
      location_id: "string",
      summary: "string",
    },
  },
  images: {
    points: {
      image_id: "string",
      captured_at: "date",
      location_id: "string",
      asset_uri: "string",
      quality: "number",
    },
  },
  suspects: {
    points: {
      suspect_id: "string",
      name: "string",
      watchlist: "string",
      image_id: "string",
      notes: "string",
    },
  },
  matches: {
    points: {
      match_id: "string",
      image_id: "string",
      suspect_id: "string",
      score: "number",
      reviewed: "boolean",
    },
  },
};
const LINKS = [
  { id: "inventory_supplier", from: "inventory", to: "suppliers", via: "supplier_id" },
  { id: "inventory_location", from: "inventory", to: "locations", via: "location_id" },
  { id: "venue_at_place", from: "venues", to: "places", via: "place_id" },
  { id: "contact_at_venue", from: "contacts", to: "venues", via: "venue_id" },
  { id: "lead_of_contact", from: "leads", to: "contacts", via: "contact_id" },
  { id: "incident_at_location", from: "incidents", to: "locations", via: "location_id" },
  { id: "image_at_location", from: "images", to: "locations", via: "location_id" },
  { id: "suspect_image", from: "suspects", to: "images", via: "image_id" },
  { id: "match_of_image", from: "matches", to: "images", via: "image_id" },
  { id: "match_of_suspect", from: "matches", to: "suspects", via: "suspect_id" },
];
const ACTIONS = ["export_pptx", "item.intake", "agent.checked", "image.enhance", "suspect.match"];
const FETCH_PLACES = [
  "warehouse.inventory",
  "warehouse.suppliers",
  "warehouse.locations",
  "warehouse.shipments",
  "warehouse.transactions",
  "warehouse.alerts",
  "maps.places",
  "maps.venues",
  "crm.contacts",
  "crm.leads",
  "cloud.signed_in",
  "db.link",
  "db.incidents",
  "owned.images",
  "owned.watchlist",
  "owned.matches",
  "local.model",
  "api.enhance",
];
const TIERS = ["T0", "T1"];
const MOCK_ROWS = {
  inventory: [
    { sku: "RM-110", sku_name: "Palm olein", supplier_id: "SUP-02", location_id: "WH-JB", quantity_kg: 840 },
    { sku: "RM-204", sku_name: "Sugar", supplier_id: "SUP-01", location_id: "WH-KL", quantity_kg: 120 },
    { sku: "PK-018", sku_name: "Carton 12", supplier_id: "SUP-03", location_id: "WH-JB", quantity_kg: 40 },
  ],
  suppliers: [
    { supplier_id: "SUP-02", supplier_name: "Sawit Co", country: "MY", risk_score: 0.31 },
    { supplier_id: "SUP-01", supplier_name: "Gula Sdn Bhd", country: "MY", risk_score: 0.22 },
  ],
  locations: [
    { location_id: "WH-JB", location_name: "Johor warehouse", city: "Johor Bahru" },
    { location_id: "WH-KL", location_name: "KL warehouse", city: "Kuala Lumpur" },
  ],
  places: [
    { place_id: "PL-01", name: "Owned lot A", locality: "Petaling Jaya", latitude: 3.1, longitude: 101.6 },
  ],
  venues: [
    { venue_id: "VN-01", name: "Owned venue desk", category: "office", place_id: "PL-01" },
  ],
  contacts: [
    { contact_id: "CT-01", name: "Ops lead", role: "buyer", venue_id: "VN-01" },
  ],
  leads: [
    { lead_id: "LD-01", account: "Owned account", status: "open", contact_id: "CT-01" },
  ],
  incidents: [
    { incident_id: "IN-01", status: "open", location_id: "WH-JB", summary: "Owned case row" },
  ],
  images: [
    { image_id: "IMG-01", location_id: "WH-JB", quality: 0.41, asset_uri: "owned.images/IMG-01" },
  ],
  suspects: [
    { suspect_id: "S-01", name: "owned watchlist row", watchlist: "owned.watchlist", image_id: "IMG-01" },
  ],
  matches: [
    { match_id: "M-01", image_id: "IMG-01", suspect_id: "S-01", score: 0.81, reviewed: false },
  ],
};

function mockRowsForObject(obj) {
  return MOCK_ROWS[obj] || MOCK_ROWS.inventory;
}

function mockHop(node) {
  const obj = node && node.object_type && MOCK_ROWS[node.object_type] ? node.object_type : "inventory";
  const rows = mockRowsForObject(obj);
  const kind = (node && node.kind) || "ingest";
  if (kind === "insight") {
    return {
      title: "Claims from " + obj,
      rows: rows.slice(0, 3).map(function (row, i) {
        const key = row.sku || row.image_id || row.place_id || row.contact_id || String(i);
        return { claim: key + " is on the owned ledger", cite: node.fetch_from || "ledger" };
      }),
    };
  }
  if (kind === "foundry") {
    return {
      title: "Compile to Cortex app",
      rows: [{ object: obj, action: node.action_type || "export_pptx", count: rows.length }],
    };
  }
  if (kind === "app") {
    return {
      title: "Emit (ghost)",
      rows: [{ emit: "/cortex/constructor/", object: obj, count: rows.length }],
    };
  }
  if (kind === "tool_call") {
    return {
      title: "Would-write " + (node.action_type || "export_pptx"),
      rows: [{ ghost: true, requires_confirm: true, count: rows.length }],
    };
  }
  if (kind === "enhance") {
    return {
      title: "Enhance owned images",
      rows: rows.map(function (row) {
        return {
          image_id: row.image_id,
          quality_in: row.quality,
          quality_out: Math.min(0.99, (row.quality || 0.4) + 0.35),
          bind: node.source_link || node.fetch_from || "local.model",
        };
      }),
    };
  }
  if (kind === "ontology") {
    return { title: "Object " + obj, rows: rows, links: linksFor(obj) };
  }
  if (kind === "audit" || kind === "hypothesize" || kind === "agent" || kind === "improve") {
    return {
      title: kind,
      rows: [{ doing: node.doing || node.note || kind, object: obj }],
    };
  }
  return { title: "Source " + (node.fetch_from || obj), rows: rows };
}

function mockWalk(graphState) {
  const nodes = (graphState && graphState.nodes) || state.nodes;
  return nodes.map(function (node) {
    return { id: node.id, kind: node.kind, hop: mockHop(node) };
  });
}

function intendedTask(graphState) {
  const nodes = (graphState && graphState.nodes) || state.nodes;
  const byKind = {};
  for (let i = 0; i < nodes.length; i++) byKind[nodes[i].kind] = nodes[i];
  const missing = [];
  const source = byKind.ingest || byKind.connector;
  const hops = mockWalk({ nodes: nodes });
  const hasRows = hops.some(function (hop) {
    return hop.hop && hop.hop.rows && hop.hop.rows.length;
  });
  if (!hasRows) missing.push("mock rows for the bound object");
  if (byKind.agent && byKind.audit && !byKind.foundry && !byKind.app) {
    if (!source) missing.push("ingest or connector (source hop 0)");
    if (!byKind.ontology) missing.push("ontology (object types)");
    if (source && !source.fetch_from && !source.source_link && source.source_kind !== "cloud") {
      missing.push("source place on connector");
    }
    return { ok: missing.length === 0, missing: missing, hops: hops, shape: "govern" };
  }
  if (byKind.hypothesize && byKind.audit && !byKind.app) {
    if (!source) missing.push("ingest or connector (source hop 0)");
    return { ok: missing.length === 0, missing: missing, hops: hops, shape: "verify" };
  }
  if (!source) missing.push("ingest or connector (source hop 0)");
  if (!byKind.ontology) missing.push("ontology (object types)");
  if (!byKind.app && !byKind.foundry) missing.push("foundry or app (emit)");
  if (source && !source.fetch_from && !source.source_link && source.source_kind !== "cloud") {
    missing.push("source place on connector");
  }
  const writer = byKind.tool_call || byKind.foundry;
  if (writer && !writer.action_type) missing.push("action on foundry/tool_call");
  return { ok: missing.length === 0, missing: missing, hops: hops, shape: "desk" };
}

function previewTableHtml(hop) {
  const rows = (hop && hop.rows) || [];
  if (!rows.length) return '<p class="hint">No mock rows for this object.</p>';
  const keys = Object.keys(rows[0]);
  let html =
    "<p class=\"hint\">" +
    escapeAttr((hop && hop.title) || "Preview") +
    "</p><table class=\"preview-table\"><thead><tr>" +
    keys
      .map(function (key) {
        return "<th>" + escapeAttr(key) + "</th>";
      })
      .join("") +
    "</tr></thead><tbody>";
  for (let i = 0; i < rows.length; i++) {
    html +=
      "<tr>" +
      keys
        .map(function (key) {
          return "<td>" + escapeAttr(rows[i][key]) + "</td>";
        })
        .join("") +
      "</tr>";
  }
  html += "</tbody></table>";
  if (hop.links && hop.links.length) {
    html += '<p class="hint">Links: ' + escapeAttr(hop.links.join("; ")) + "</p>";
  }
  return html;
}

function pageEl() {
  return document.getElementById("node-page") || document.getElementById("cal-pop");
}

function defaultPageTab(kind) {
  if (kind === "ingest" || kind === "connector" || kind === "enhance") return "source";
  if (kind === "ontology" || kind === "insight") return "object";
  return "action";
}

function setPageTab(tab) {
  const page = pageEl();
  if (!page) return;
  const next = tab === "object" || tab === "action" ? tab : "source";
  page.setAttribute("data-page-tab", next);
  const panes = page.querySelectorAll("[data-page-pane]");
  for (let i = 0; i < panes.length; i++) {
    panes[i].hidden = panes[i].getAttribute("data-page-pane") !== next;
  }
  const tabs = page.querySelectorAll("[data-page-tab]");
  for (let j = 0; j < tabs.length; j++) {
    tabs[j].classList.toggle("on", tabs[j].getAttribute("data-page-tab") === next);
  }
}

function paintPreview(node) {
  const box = document.getElementById("preview-table");
  const taskEl = document.getElementById("preview-task");
  const engineEl = document.getElementById("preview-engine");
  if (engineEl) {
    engineEl.textContent = cortexOrigin() ? "Mock rows." : "Mock rows.";
  }
  const hop = mockHop(node);
  if (box) box.innerHTML = previewTableHtml(hop);
  const task = intendedTask(state);
  if (taskEl) {
    taskEl.className = "hint " + (task.ok ? "task-pass" : "task-fail");
    taskEl.textContent = task.ok
      ? "Intended task PASS. Mock rows reach foundry/app."
      : "Intended task FAIL: " + task.missing.join("; ") + ". Redo compile or bind a source.";
  }
}

function bindDesk(name) {
  const node = state.nodes.find(function (n) {
    return n.id === selectedId;
  });
  if (!node) return false;
  if (name === "warehouse") {
    node.object_type = "inventory";
    node.data_point = "sku";
    node.data_type = "string";
    node.source_kind = "place";
    node.fetch_from = "warehouse.inventory";
    node.source_link = "";
  } else if (name === "venue") {
    node.object_type = node.kind === "ingest" ? "places" : "venues";
    node.data_point = node.object_type === "places" ? "place_id" : "venue_id";
    node.data_type = "string";
    node.source_kind = "place";
    node.fetch_from = node.object_type === "places" ? "maps.places" : "crm.contacts";
    node.source_link = "";
  } else if (name === "suspect") {
    node.object_type = "images";
    node.data_point = "image_id";
    node.data_type = "string";
    node.source_kind = "place";
    node.fetch_from = "owned.images";
    node.source_link = "";
  } else {
    return false;
  }
  if (node.kind === "ingest") {
    node.doing =
      "Hop 0. Load " + node.object_type + " rows from " + node.fetch_from + ". No write.";
    node.note = node.doing;
  }
  if (node.kind === "connector") {
    node.doing = "Bind " + name + " first-party source to " + node.object_type + ".";
    node.note = node.doing;
  }
  save();
  render();
  if (calOpen) openCalPop(node, { response: "bound " + name });
  return true;
}

const state = load() || sample();
let selectedId = state.nodes[0] ? state.nodes[0].id : null;
let armedPort = null;
let drag = null;
let calOpen = false;
const pan = { x: 0, y: 0, k: 1 };
let spaceDown = false;
let panning = null;

function applyPan() {
  const world = document.getElementById("world");
  if (!world) return;
  world.style.transform = "translate(" + pan.x + "px, " + pan.y + "px) scale(" + pan.k + ")";
}

function screenToWorld(clientX, clientY) {
  const stage = document.getElementById("stage").getBoundingClientRect();
  return {
    x: (clientX - stage.left - pan.x) / pan.k,
    y: (clientY - stage.top - pan.y) / pan.k,
  };
}

function actionsForObject(objectType) {
  return ACTION_META.filter(function (row) {
    return row.objects.indexOf("*") >= 0 || row.objects.indexOf(objectType) >= 0;
  }).map(function (row) {
    return row.id;
  });
}

function seedNode(kind, x, y) {
  const meta = KINDS[kind];
  const node = {
    id: uid(),
    kind: kind,
    x: x != null ? x : 80 + state.nodes.length * 16,
    y: y != null ? y : 80 + state.nodes.length * 16,
    note: meta.note,
    doing: meta.note,
    persona: meta.persona,
    tier: "T0",
    stream: false,
  };
  if (kind === "ingest" || kind === "connector" || kind === "ontology") {
    node.object_type = "inventory";
    node.data_point = "sku";
    node.data_type = "string";
    node.fetch_from = "warehouse.inventory";
    node.source_kind = "place";
    node.source_link = "";
  }
  if (kind === "enhance") {
    node.object_type = "images";
    node.data_point = "image_id";
    node.data_type = "string";
    node.fetch_from = "local.model";
    node.source_kind = "local_model";
    node.source_link = "local://enhance";
    node.action_type = "image.enhance";
  }
  if (kind === "tool_call" || kind === "foundry") node.action_type = "export_pptx";
  if (kind === "app") node.action_type = "emit";
  return node;
}

function sample() {
  return foundrySample();
}

function foundrySample() {
  return {
    nodes: [
      {
        id: "n0",
        kind: "ingest",
        x: 32,
        y: 48,
        object_type: "inventory",
        data_point: "sku",
        data_type: "string",
        fetch_from: "warehouse.inventory",
        source_kind: "place",
        source_link: "",
        persona: "loader",
        tier: "T0",
        stream: false,
        doing: "Hop 0. Load inventory rows from warehouse.inventory. No write.",
        note: "Hop 0. Load inventory rows from warehouse.inventory. No write.",
      },
      {
        id: "c1",
        kind: "connector",
        x: 240,
        y: 48,
        object_type: "inventory",
        data_point: "sku",
        data_type: "string",
        fetch_from: "warehouse.inventory",
        source_kind: "place",
        source_link: "",
        persona: "source",
        tier: "T0",
        stream: false,
        note: "First-party Cortex input. WhatsApp stays a draft, not a send.",
      },
      {
        id: "o1",
        kind: "ontology",
        x: 448,
        y: 48,
        object_type: "suppliers",
        data_point: "supplier_id",
        data_type: "string",
        fetch_from: "warehouse.suppliers",
        persona: "modeler",
        note: "Cortex ontology objects/links/actions. Not a custom type picker.",
      },
      {
        id: "i1",
        kind: "insight",
        x: 656,
        y: 48,
        object_type: "Insight",
        persona: "analyst",
        note: "Cite ontology + ledger. What you may claim from those objects.",
      },
      {
        id: "f1",
        kind: "foundry",
        x: 240,
        y: 208,
        action_type: "export_pptx",
        persona: "compiler",
        note: "Compile insights into a governed Cortex app. Not an Activepieces clone.",
      },
      {
        id: "a1",
        kind: "app",
        x: 448,
        y: 208,
        action_type: "emit",
        persona: "operator",
        note: "Runnable output. Hosted inside Cortex at /cortex/constructor/.",
      },
      {
        id: "g1",
        kind: "audit",
        x: 32,
        y: 208,
        persona: "steward",
        note: "Why this node exists. Ghost ledgers would-call, not a second EMIT.",
      },
      {
        id: "t1",
        kind: "tool_call",
        x: 656,
        y: 208,
        action_type: "export_pptx",
        object_type: "inventory",
        data_point: "sku",
        data_type: "string",
        persona: "writer",
        tier: "T0",
        note: "F8 governed write. requires_confirm. Only real tool on this pack is export_pptx.",
      },
    ],
    edges: [
      { from: "n0", to: "c1" },
      { from: "c1", to: "o1" },
      { from: "o1", to: "i1" },
      { from: "i1", to: "f1" },
      { from: "f1", to: "a1" },
      { from: "f1", to: "g1" },
      { from: "f1", to: "t1" },
    ],
  };
}

function venueSample() {
  const g = foundrySample();
  const n0 = g.nodes[0];
  const c1 = g.nodes[1];
  const o1 = g.nodes[2];
  n0.object_type = "places";
  n0.data_point = "place_id";
  n0.fetch_from = "maps.places";
  n0.doing = "Hop 0. Load owned places. No write.";
  n0.note = n0.doing;
  c1.object_type = "venues";
  c1.data_point = "venue_id";
  c1.fetch_from = "crm.contacts";
  c1.note = "Venue/CRM connector. Owned contacts only.";
  o1.object_type = "contacts";
  o1.data_point = "contact_id";
  o1.fetch_from = "crm.contacts";
  o1.note = "Place-Venue-Contact-Lead. Cortex objects. Ontology stays Cortex.";
  const t1 = g.nodes[g.nodes.length - 1];
  t1.object_type = "leads";
  t1.data_point = "lead_id";
  t1.note = "F8 governed write. requires_confirm.";
  return g;
}

function suspectSample() {
  return {
    nodes: [
      {
        id: "n0",
        kind: "ingest",
        x: 32,
        y: 48,
        object_type: "images",
        data_point: "image_id",
        data_type: "string",
        fetch_from: "owned.images",
        source_kind: "place",
        persona: "loader",
        doing: "Hop 0. Owned images only. No scrape.",
        note: "Hop 0. Owned images only. No scrape.",
      },
      {
        id: "e1",
        kind: "enhance",
        x: 240,
        y: 48,
        object_type: "images",
        data_point: "image_id",
        fetch_from: "local.model",
        source_kind: "local_model",
        source_link: "local://enhance",
        action_type: "image.enhance",
        persona: "enhancer",
        note: "Comfy-style enhance on owned images.",
      },
      {
        id: "o1",
        kind: "ontology",
        x: 448,
        y: 48,
        object_type: "suspects",
        data_point: "suspect_id",
        fetch_from: "owned.watchlist",
        persona: "modeler",
        note: "Match owned.watchlist. No public CCTV scrape.",
      },
      {
        id: "i1",
        kind: "insight",
        x: 656,
        y: 48,
        persona: "analyst",
        note: "Cite owned match rows + ledger.",
      },
      {
        id: "f1",
        kind: "foundry",
        x: 240,
        y: 208,
        action_type: "suspect.match",
        persona: "compiler",
        note: "Compile suspect desk app inside Cortex.",
      },
      {
        id: "a1",
        kind: "app",
        x: 448,
        y: 208,
        action_type: "emit",
        persona: "operator",
        note: "Runnable desk. Hosted inside Cortex.",
      },
      {
        id: "t1",
        kind: "tool_call",
        x: 656,
        y: 208,
        action_type: "suspect.match",
        object_type: "matches",
        data_point: "match_id",
        persona: "writer",
        note: "F8 governed write. requires_confirm. suspect.match against owned.watchlist.",
      },
    ],
    edges: [
      { from: "n0", to: "e1" },
      { from: "e1", to: "o1" },
      { from: "o1", to: "i1" },
      { from: "i1", to: "f1" },
      { from: "f1", to: "a1" },
      { from: "f1", to: "t1" },
    ],
  };
}

function applySeed(id) {
  const palantir = {
    define: "define data for inventory",
    govern: "govern agents on inventory",
    insights: "business insights on inventory",
    understand: "understand this company",
  };
  const prompt = palantir[id];
  if (prompt) {
    const box = document.getElementById("chat-input");
    if (box) box.value = prompt;
    const gen = window.Constructor && window.Constructor.generateLocal;
    if (typeof gen === "function") {
      const graph = gen(prompt);
      if (graph && graph.ok && Array.isArray(graph.nodes)) {
        replaceGraph(graph.nodes, graph.edges || []);
      }
    }
    return id;
  }
  const graph = id === "venue" ? venueSample() : id === "suspect" ? suspectSample() : foundrySample();
  replaceGraph(graph.nodes, graph.edges);
  return id;
}

function paintLastRun() {
  const el = document.getElementById("last-run");
  if (!el) return;
  const run = window.Constructor && window.Constructor.lastRun;
  if (!run) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  el.hidden = false;
  el.textContent =
    "Last " +
    (run.mode || "run") +
    ": " +
    String(run.summary || "").slice(0, 180) +
    (run.ledger_id ? " ledger " + run.ledger_id : "");
}

function filterRail(q) {
  const query = String(q || "").toLowerCase().trim();
  const hitsEl = document.getElementById("rail-hits");
  document.querySelectorAll("[data-add]").forEach(function (btn) {
    const kind = btn.getAttribute("data-add") || "";
    const meta = KINDS[kind] || {};
    const blob = (kind + " " + (meta.label || "") + " " + (meta.note || "") + " " + (meta.persona || "")).toLowerCase();
    btn.classList.toggle("rail-hide", !!(query && blob.indexOf(query) < 0));
    btn.hidden = !!(query && blob.indexOf(query) < 0);
  });
  document.querySelectorAll("details.quiet").forEach(function (panel) {
    if (!query) return;
    panel.open = !!panel.querySelector("[data-add]:not(.rail-hide)");
  });
  if (!hitsEl) return;
  if (!query) {
    hitsEl.hidden = true;
    hitsEl.innerHTML = "";
    return;
  }
  const rows = [];
  Object.keys(OBJECTS).forEach(function (id) {
    if (id.toLowerCase().indexOf(query) >= 0) rows.push({ type: "object", id: id, label: "object " + id });
  });
  ACTION_META.forEach(function (row) {
    const blob = (row.id + " " + (row.label || "")).toLowerCase();
    if (blob.indexOf(query) >= 0) rows.push({ type: "action", id: row.id, label: "action " + (row.label || row.id) });
  });
  FETCH_PLACES.forEach(function (place) {
    if (String(place).toLowerCase().indexOf(query) >= 0) {
      rows.push({ type: "place", id: place, label: "place " + place });
    }
  });
  hitsEl.innerHTML = "";
  rows.slice(0, 8).forEach(function (hit) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = hit.label;
    b.addEventListener("click", function () {
      let node;
      if (hit.type === "object") {
        node = seedNode("ingest");
        node.object_type = hit.id;
        const pts = OBJECTS[hit.id] && OBJECTS[hit.id].points;
        const first = pts ? Object.keys(pts)[0] : "";
        if (first) {
          node.data_point = first;
          node.data_type = pts[first];
        }
      } else if (hit.type === "action") {
        node = seedNode("tool_call");
        node.action_type = hit.id;
      } else {
        node = seedNode("connector");
        node.fetch_from = hit.id;
      }
      state.nodes.push(node);
      selectedId = node.id;
      save();
      render();
      openCalPop(node);
    });
    hitsEl.appendChild(b);
  });
  hitsEl.hidden = rows.length === 0;
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
    const meta = KINDS[node.kind] || { label: node.kind, icon: "", persona: "", color: "#888" };
    el.className = "node" + (node.id === selectedId ? " selected" : "");
    el.dataset.id = node.id;
    el.dataset.kind = node.kind;
    el.style.left = node.x + "px";
    el.style.top = node.y + "px";
    el.style.setProperty("--kind", meta.color);
    const persona = node.persona || meta.persona;
    const sub = node.object_type
      ? node.object_type + (node.data_point ? " · " + node.data_point : "")
      : node.action_type || "";
    el.innerHTML =
      '<div class="node-head">' +
      '<span class="ico">' +
      (meta.icon || "") +
      "</span>" +
      '<div class="kind">' +
      (meta.label || node.kind).toUpperCase() +
      "</div>" +
      '<button type="button" class="node-edit" data-edit="1">Edit</button>' +
      "</div><h2>" +
      meta.label +
      "</h2>" +
      (persona ? '<p class="persona">' + escapeAttr(persona) + "</p>" : "") +
      (sub ? '<p class="sub">' + escapeAttr(sub) + "</p>" : "") +
      '<div class="ports">' +
      '<button type="button" class="port" data-port="in" aria-label="input port"></button>' +
      '<button type="button" class="port" data-port="out" aria-label="output port"></button>' +
      "</div>";
    nodesEl.appendChild(el);
  }
  drawWires();
  showInspect();
}

function nodeCenter(id, port) {
  const node = state.nodes.find((n) => n.id === id);
  if (!node) return { x: 0, y: 0 };
  const w = 188;
  const h = 110;
  return {
    x: node.x + (port === "out" ? w - 18 : 18),
    y: node.y + h - 18,
  };
}

function drawWires() {
  let maxX = 900;
  let maxY = 640;
  for (const n of state.nodes) {
    maxX = Math.max(maxX, n.x + 240);
    maxY = Math.max(maxY, n.y + 180);
  }
  wiresEl.setAttribute("width", String(maxX));
  wiresEl.setAttribute("height", String(maxY));
  wiresEl.setAttribute("viewBox", "0 0 " + maxX + " " + maxY);
  wiresEl.setAttribute("preserveAspectRatio", "none");
  const parts = [];
  for (const edge of state.edges) {
    const a = nodeCenter(edge.from, "out");
    const b = nodeCenter(edge.to, "in");
    const mid = (a.x + b.x) / 2;
    parts.push(
      '<path fill="none" stroke="rgba(255,255,255,0.28)" stroke-width="1.4" d="M' +
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
  applyPan();
}

function linksFor(objectType) {
  return LINKS.filter(function (row) {
    return row.from === objectType || row.to === objectType;
  }).map(function (row) {
    return row.id + " (" + row.from + " -> " + row.to + " via " + row.via + ")";
  });
}

function decisionText(node, extra) {
  extra = extra || {};
  const cortex = extra.cortex_kind || extra.kind || "";
  const write = node.kind === "tool_call" || node.kind === "app";
  const linkLines = linksFor(node.object_type);
  const appId = (state.nodes.find(function (n) {
    return n.kind === "app";
  }) || {}).id;
  const lines = [
    "PERSONA: " + (node.persona || ((KINDS[node.kind] && KINDS[node.kind].persona) || "-")),
    "DOING: " + (node.doing || node.note || node.kind),
    "ACTION: " + (node.action_type || (write ? "export_pptx" : "none (read)")),
    "APP: " + (extra.is_emit ? "this node is the EMIT app" : appId ? "downstream " + appId : "no app node yet"),
    "CODE: " + (cortex || node.kind) + (extra.tool_name ? " tool=" + extra.tool_name : ""),
    "RESPONSE: " + (extra.response || "local preview. Press for Cortex compile."),
    "OBJECT: " + (node.object_type || "-") + (node.data_point ? " · " + node.data_point : ""),
    "FETCH: " + (node.fetch_from || "-") + (node.source_kind ? " · " + node.source_kind : ""),
    "WRITE: " + (write ? "would-write if live / confirm on tool_call" : "read"),
    "CONFIRM: " + (node.kind === "tool_call" ? "requires_confirm before live write" : "not required"),
  ];
  if (linkLines.length) lines.push("LINKS: " + linkLines.join("; "));
  return lines.join("\n");
}

function showDecision(layer) {
  const el = document.getElementById("inspect-decision");
  const node = (layer && layer.node) || state.nodes.find((n) => n.id === selectedId);
  if (!node) {
    if (el) {
      el.hidden = true;
      el.textContent = "";
    }
    const confirmEl = document.getElementById("inspect-confirm");
    if (confirmEl) confirmEl.hidden = true;
    return;
  }
  const text = decisionText(node, layer || {});
  const confirmEl = document.getElementById("inspect-confirm");
  if (confirmEl) confirmEl.hidden = node.kind !== "tool_call";
  if (el) {
    el.hidden = false;
    el.textContent = text;
  }
  paintLastRun();
  const inspect = document.getElementById("inspect");
  if (inspect && !(layer && layer.openDialog)) inspect.scrollTop = 0;
  for (const n of nodesEl.querySelectorAll(".node")) {
    n.classList.toggle("pressed", !!(layer && layer.openDialog) && n.dataset.id === node.id);
  }
  if (layer && layer.openDialog) openCalPop(node, layer);
}

function closeCalPop() {
  calOpen = false;
  document.body.classList.remove("node-page-open");
  const pop = pageEl();
  if (pop) pop.hidden = true;
  for (const n of nodesEl.querySelectorAll(".node")) n.classList.remove("pressed");
}

function openCalPop(node, extra) {
  const pop = pageEl();
  if (!pop || !node) return;
  calOpen = true;
  extra = extra || {};
  const meta = KINDS[node.kind] || { label: node.kind, icon: "", persona: "", color: "#888" };
  pop.style.setProperty("--kind", meta.color);
  document.body.classList.add("node-page-open");
  const icon = document.getElementById("event-icon");
  const title = document.getElementById("event-title");
  const kindLabel = document.getElementById("event-kind-label");
  const personaEl = document.getElementById("event-persona");
  const help = document.getElementById("ingest-help");
  const hint = document.getElementById("event-actions-hint");
  const fields = document.getElementById("event-fields");
  if (icon) icon.innerHTML = meta.icon || "";
  if (kindLabel) kindLabel.textContent = (meta.label || node.kind).toUpperCase();
  if (title) title.textContent = meta.label;
  if (personaEl) {
    personaEl.textContent =
      (node.persona || meta.persona) + " · " + (node.doing || node.note || meta.note);
  }
  if (help) help.hidden = node.kind !== "ingest";
  const enhanceHelp = document.getElementById("enhance-help");
  if (enhanceHelp) enhanceHelp.hidden = node.kind !== "enhance";
  const connectorHelp = document.getElementById("connector-help");
  if (connectorHelp) connectorHelp.hidden = node.kind !== "connector";
  const obj = node.object_type && OBJECTS[node.object_type] ? node.object_type : Object.keys(OBJECTS)[0];
  const allowed = actionsForObject(obj);
  if (hint) {
    hint.textContent =
      node.kind === "ingest"
        ? "Ingest has no action. Wire it into ontology, then foundry/tool_call to act."
        : "Actions on " + obj + ": " + allowed.join(", ") + ".";
  }
  if (fields) fields.innerHTML = eventFieldsHtml(node);
  setPageTab(defaultPageTab(node.kind));
  paintPreview(node);
  const facts = document.getElementById("decision-facts");
  const js = document.getElementById("decision-json");
  if (facts) facts.textContent = extra.cortex_kind ? decisionText(node, extra) : "";
  if (js) js.textContent = extra.raw ? JSON.stringify(extra.raw, null, 2) : "";
  pop.hidden = false;
}

function fieldInput(name, label, value, placeholder) {
  return (
    "<label>" +
    label +
    '</label><input name="' +
    name +
    '" value="' +
    escapeAttr(value || "") +
    '" placeholder="' +
    escapeAttr(placeholder || "") +
    '" />'
  );
}

function eventFieldsHtml(node) {
  const obj = node.object_type && OBJECTS[node.object_type] ? node.object_type : Object.keys(OBJECTS)[0];
  const points = OBJECTS[obj].points;
  const point = node.data_point && points[node.data_point] ? node.data_point : Object.keys(points)[0];
  const dtype = node.data_type || points[point];
  const allowed = actionsForObject(obj);
  const action = allowed.indexOf(node.action_type) >= 0 ? node.action_type : allowed[0];
  const persona = node.persona && PERSONAS.indexOf(node.persona) >= 0 ? node.persona : KINDS[node.kind].persona;
  const sourceKind =
    node.source_kind && SOURCE_KINDS.indexOf(node.source_kind) >= 0 ? node.source_kind : "place";
  const sourceLabel = node.kind === "ingest" ? "Source place (hop 0)" : "Fetch / place";
  const objectLabel = node.kind === "ingest" ? "Becomes object" : "Object (ontology)";
  const bindSource = node.kind === "ingest" || node.kind === "connector" || node.kind === "enhance";
  let sourceHtml =
    "<h3>Source</h3>" +
    '<p class="hint">Pick Warehouse, Venue/CRM, or Suspect, or a place.</p>' +
    '<div class="bind-row">' +
    '<button type="button" data-bind-desk="warehouse"' +
    (node.fetch_from === "warehouse.inventory" ? ' class="on"' : "") +
    ">Warehouse</button>" +
    '<button type="button" data-bind-desk="venue"' +
    (String(node.fetch_from || "").indexOf("maps.") === 0 || String(node.fetch_from || "").indexOf("crm.") === 0
      ? ' class="on"'
      : "") +
    ">Venue/CRM</button>" +
    '<button type="button" data-bind-desk="suspect"' +
    (String(node.fetch_from || "").indexOf("owned.") === 0 ? ' class="on"' : "") +
    ">Suspect desk</button></div>";
  if (bindSource) {
    sourceHtml += fieldSelect("source_kind", "Source kind", SOURCE_KINDS, sourceKind);
    if (sourceKind === "cloud") {
      sourceHtml +=
        '<p class="hint">Ghost cloud sign-in. No OAuth. No fetch on Pages.</p>' +
        '<button type="button" id="cloud-signin">Sign in (ghost)</button>';
    } else if (sourceKind === "database") {
      sourceHtml += fieldInput("source_link", "Database link", node.source_link || "db.link", "owned.images or db.incidents");
    } else if (sourceKind === "local_model") {
      sourceHtml += fieldInput("source_link", "Local model", node.source_link || "local://enhance", "local://enhance or a model path");
    } else if (sourceKind === "online_api") {
      sourceHtml += fieldInput("source_link", "Online API", node.source_link || "api.enhance", "api.enhance (ghost, no fetch on Pages)");
    } else {
      sourceHtml += fieldSelect("fetch_from", sourceLabel, FETCH_PLACES, node.fetch_from || "warehouse.inventory");
    }
  } else {
    sourceHtml += fieldSelect("fetch_from", sourceLabel, FETCH_PLACES, node.fetch_from || "warehouse.inventory");
  }
  const objectHtml =
    "<h3>Object</h3>" +
    fieldSelect("object_type", objectLabel, Object.keys(OBJECTS), obj) +
    fieldSelect("data_point", "Data point", Object.keys(points), point) +
    fieldSelect("data_type", "Data type", ["string", "number", "integer", "boolean", "date"], dtype) +
    '<p class="hint">Links: ' +
    escapeAttr(linksFor(obj).join("; ") || "none") +
    "</p>";
  let actionHtml =
    "<h3>Action</h3>" +
    fieldSelect("persona", "Persona", PERSONAS, persona);
  if (node.kind !== "ingest") {
    actionHtml += fieldSelect("action_type", "Action", allowed, action);
  } else {
    actionHtml += '<p class="hint">Ingest never writes. Actions live on Compile / tool_call.</p>';
  }
  actionHtml +=
    fieldSelect("tier", "Router tier", TIERS, TIERS.indexOf(node.tier) >= 0 ? node.tier : "T0") +
    fieldSelect("stream", "Stream", ["false", "true"], node.stream ? "true" : "false");
  return (
    '<section class="page-pane" data-page-pane="source">' +
    sourceHtml +
    "</section>" +
    '<section class="page-pane" data-page-pane="object">' +
    objectHtml +
    "</section>" +
    '<section class="page-pane" data-page-pane="action">' +
    actionHtml +
    "</section>"
  );
}

function provenanceStamp(node) {
  const src = String((node && (node.fetch_from || node.source_link)) || "");
  const kind = String((node && node.source_kind) || "");
  if (node && node.kind === "tool_call") return "EXTRACTED";
  if (kind === "cloud" || src === "cloud.signed_in") return "INFERRED";
  if (!src) return "AMBIGUOUS";
  if (
    src.indexOf("workspace.") === 0 ||
    src.indexOf("warehouse.") === 0 ||
    src.indexOf("owned.") === 0 ||
    src.indexOf("maps.") === 0 ||
    src.indexOf("crm.") === 0 ||
    src.indexOf("db.") === 0 ||
    src.indexOf("local://") === 0 ||
    kind === "place" ||
    kind === "database" ||
    kind === "local_model"
  ) {
    return "EXTRACTED";
  }
  return "INFERRED";
}

function showInspect() {
  const node = state.nodes.find((n) => n.id === selectedId);
  if (!node) {
    inspectJson.hidden = true;
    inspectForm.hidden = true;
    if (inspectCard) inspectCard.hidden = true;
    inspectEmpty.hidden = false;
    const confirmEl = document.getElementById("inspect-confirm");
    if (confirmEl) confirmEl.hidden = true;
    showDecision(null);
    return;
  }
  inspectEmpty.hidden = true;
  inspectForm.hidden = true;
  const meta = KINDS[node.kind] || { label: node.kind, icon: "", persona: "", color: "#888", note: "" };
  const obj = node.object_type || "-";
  if (inspectCard) {
    inspectCard.hidden = false;
    inspectCard.style.setProperty("--kind", meta.color);
    inspectCard.innerHTML =
      '<div class="inspect-card-head"><span class="ico">' +
      (meta.icon || "") +
      "</span><h3>" +
      meta.label +
      "</h3></div>" +
      '<p class="hint">' +
      escapeAttr(obj) +
      (node.fetch_from ? " · " + escapeAttr(node.fetch_from) : "") +
      (node.kind === "ingest" ? " · hop 0" : "") +
      "</p>" +
      (node.kind === "tool_call" ? '<p class="confirm-banner">Needs confirm before a live write.</p>' : "") +
      '<button type="button" id="press-decision">Edit</button>';
    const press = document.getElementById("press-decision");
    if (press) {
      press.addEventListener("click", function (event) {
        event.preventDefault();
        if (window.Constructor && window.Constructor.pressNode) window.Constructor.pressNode();
      });
    }
  }
  showDecision({ node: node, response: "preview" });
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
  if (field === "source_kind") {
    if (value === "cloud") {
      node.fetch_from = "cloud.signed_in";
      if (!node.source_link) node.source_link = "";
    } else if (value === "database") {
      node.fetch_from = node.source_link || "db.link";
    } else if (value === "local_model") {
      node.fetch_from = "local.model";
      if (!node.source_link) node.source_link = "local://enhance";
    } else if (value === "online_api") {
      node.fetch_from = "api.enhance";
      if (!node.source_link) node.source_link = "api.enhance";
    } else if (
      node.fetch_from === "cloud.signed_in" ||
      node.fetch_from === "db.link" ||
      node.fetch_from === "local.model" ||
      node.fetch_from === "api.enhance"
    ) {
      node.fetch_from =
        node.object_type === "incidents"
          ? "db.incidents"
          : node.object_type === "images"
            ? "owned.images"
            : "warehouse.inventory";
    }
  }
  if (field === "source_link" && (node.source_kind === "database" || node.source_kind === "local_model" || node.source_kind === "online_api")) {
    if (node.source_kind === "database") node.fetch_from = value || "db.link";
  }
  if (node.kind === "ingest" && (field === "object_type" || field === "fetch_from" || field === "source_kind")) {
    node.doing =
      "Hop 0. Load " +
      (node.object_type || "object") +
      " rows from " +
      (node.fetch_from || "place") +
      ". No write.";
    node.note = node.doing;
  }
  if (node.kind === "enhance" && (field === "source_kind" || field === "source_link")) {
    node.doing =
      "Comfy-style enhance via " +
      (node.source_kind || "local_model") +
      " " +
      (node.source_link || node.fetch_from || "") +
      ". Ghost on Pages.";
    node.note = node.doing;
    node.action_type = "image.enhance";
  }
  save();
  render();
  if (calOpen) {
    const next = state.nodes.find((n) => n.id === selectedId);
    if (next) openCalPop(next, { response: "saved" });
  }
  return true;
}

inspectForm.addEventListener("change", (event) => {
  const el = event.target;
  if (!el || !el.name) return;
  patchSelected(el.name, el.value);
});

const eventForm = document.getElementById("event-form");
if (eventForm) {
  eventForm.addEventListener("submit", function (event) {
    event.preventDefault();
  });
  eventForm.addEventListener("change", function (event) {
    const el = event.target;
    if (!el || !el.name) return;
    patchSelected(el.name, el.value);
  });
  eventForm.addEventListener("click", function (event) {
    const bind = event.target && event.target.closest && event.target.closest("[data-bind-desk]");
    if (bind) {
      event.preventDefault();
      bindDesk(bind.getAttribute("data-bind-desk"));
      return;
    }
    if (!event.target || event.target.id !== "cloud-signin") return;
    event.preventDefault();
    const node = state.nodes.find((n) => n.id === selectedId);
    if (!node) return;
    node.source_kind = "cloud";
    node.fetch_from = "cloud.signed_in";
    node.source_link = "signed-in";
    if (node.kind === "ingest") {
      node.doing =
        "Hop 0. Load " + (node.object_type || "object") + " rows from cloud.signed_in. No write.";
      node.note = node.doing;
    }
    save();
    render();
    openCalPop(node, { response: "cloud signed-in (ghost)" });
  });
}
const eventClose = document.getElementById("event-close");
if (eventClose) eventClose.addEventListener("click", closeCalPop);
const eventBack = document.getElementById("event-back");
if (eventBack) eventBack.addEventListener("click", closeCalPop);
const pageTabs = document.getElementById("node-page-tabs");
if (pageTabs) {
  pageTabs.addEventListener("click", function (event) {
    const tab = event.target && event.target.closest && event.target.closest("[data-page-tab]");
    if (!tab) return;
    setPageTab(tab.getAttribute("data-page-tab"));
  });
}

document.querySelectorAll("[data-add]").forEach((btn) => {
  const kind = btn.getAttribute("data-add");
  const meta = KINDS[kind];
  if (meta) {
    btn.style.setProperty("--kind", meta.color);
    btn.setAttribute("aria-label", meta.label);
    btn.innerHTML = '<span class="ico" aria-hidden="true">' + meta.icon + "</span>" + meta.label;
  }
  btn.addEventListener("click", () => {
    const node = seedNode(kind);
    state.nodes.push(node);
    selectedId = node.id;
    save();
    render();
    openCalPop(node);
  });
});

document.querySelectorAll("[data-seed]").forEach(function (btn) {
  btn.addEventListener("click", function () {
    applySeed(btn.getAttribute("data-seed"));
  });
});
const railFilter = document.getElementById("rail-filter");
if (railFilter) {
  railFilter.addEventListener("input", function () {
    filterRail(railFilter.value);
  });
}

nodesEl.addEventListener("pointerdown", (event) => {
  const nodeEl = event.target.closest(".node");
  if (!nodeEl) return;
  const port = event.target.closest(".port");
  const id = nodeEl.dataset.id;
  selectedId = id;

  const edit = event.target.closest("[data-edit]");
  if (event.detail === 2 && !port) {
    event.stopPropagation();
    showInspect();
    if (window.Constructor && window.Constructor.pressNode) window.Constructor.pressNode();
    return;
  }
  if (edit) {
    event.stopPropagation();
    showInspect();
    if (window.Constructor && window.Constructor.pressNode) window.Constructor.pressNode();
    return;
  }

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

  if (spaceDown || event.button === 1) return;

  const worldPt = screenToWorld(event.clientX, event.clientY);
  const node = state.nodes.find((n) => n.id === id);
  drag = {
    id,
    dx: worldPt.x - (node ? node.x : 0),
    dy: worldPt.y - (node ? node.y : 0),
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
  const worldPt = screenToWorld(event.clientX, event.clientY);
  node.x = Math.max(8, worldPt.x - drag.dx);
  node.y = Math.max(8, worldPt.y - drag.dy);
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

(function bindPanZoom() {
  const stage = document.getElementById("stage");
  if (!stage) return;

  stage.addEventListener(
    "wheel",
    function (event) {
      event.preventDefault();
      const rect = stage.getBoundingClientRect();
      const mx = event.clientX - rect.left;
      const my = event.clientY - rect.top;
      const oldK = pan.k;
      const next = Math.min(2.5, Math.max(0.35, oldK * (event.deltaY > 0 ? 0.92 : 1.08)));
      const wx = (mx - pan.x) / oldK;
      const wy = (my - pan.y) / oldK;
      pan.k = next;
      pan.x = mx - wx * next;
      pan.y = my - wy * next;
      applyPan();
    },
    { passive: false }
  );

  document.addEventListener("keydown", function (event) {
    if (event.code !== "Space" || event.repeat) return;
    const tag = (event.target && event.target.tagName) || "";
    if (tag === "TEXTAREA" || tag === "INPUT" || tag === "SELECT") return;
    event.preventDefault();
    spaceDown = true;
    stage.classList.add("panning");
  });
  document.addEventListener("keyup", function (event) {
    if (event.code !== "Space") return;
    spaceDown = false;
    if (!panning) stage.classList.remove("panning");
  });

  stage.addEventListener("pointerdown", function (event) {
    if (event.target.closest && event.target.closest(".node")) {
      if (event.button !== 1 && !spaceDown) return;
    }
    if (event.button !== 0 && event.button !== 1) return;
    event.preventDefault();
    panning = { x: event.clientX - pan.x, y: event.clientY - pan.y };
    stage.classList.add("panning");
    stage.setPointerCapture(event.pointerId);
  });
  stage.addEventListener("pointermove", function (event) {
    if (!panning) return;
    pan.x = event.clientX - panning.x;
    pan.y = event.clientY - panning.y;
    applyPan();
  });
  stage.addEventListener("pointerup", function () {
    panning = null;
    if (!spaceDown) stage.classList.remove("panning");
  });
  stage.addEventListener("pointercancel", function () {
    panning = null;
    if (!spaceDown) stage.classList.remove("panning");
  });
})();

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

function addNode(kind, x, y) {
  if (!KINDS[kind]) return null;
  const node = seedNode(kind, x, y);
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
  inspectJson.hidden = false;
  inspectJson.textContent = JSON.stringify(obj, null, 2);
}

function markGhostWalk(ids) {
  for (const el of nodesEl.querySelectorAll(".node")) {
    el.classList.toggle("ghosting", ids.indexOf(el.dataset.id) !== -1);
  }
}

function loadCompilePath() {
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

function replaceCatalog(objects, actions, places) {
  Object.keys(objects || {}).forEach(function (k) {
    OBJECTS[k] = objects[k];
  });
  if (Array.isArray(actions) && actions.length) {
    for (let i = 0; i < actions.length; i++) {
      if (ACTIONS.indexOf(actions[i]) < 0) ACTIONS.push(actions[i]);
    }
  }
  if (Array.isArray(places) && places.length) {
    for (let i = 0; i < places.length; i++) {
      if (FETCH_PLACES.indexOf(places[i]) < 0) FETCH_PLACES.push(places[i]);
    }
  }
  render();
  filterRail();
}

function loadChatDock() {
  try {
    const raw = JSON.parse(localStorage.getItem(CHAT_DOCK_KEY) || "null");
    if (raw && (raw.snap === "bottom" || raw.snap === "right")) {
      return { open: !!raw.open, snap: raw.snap };
    }
  } catch (err) {}
  return { open: false, snap: "right" };
}

const chatDock = loadChatDock();

function applyChatDock() {
  document.body.classList.toggle("chat-open", chatDock.open);
  document.body.classList.toggle("chat-snap-right", chatDock.snap === "right");
  document.body.classList.toggle("chat-snap-bottom", chatDock.snap === "bottom");
  const fab = document.getElementById("chat-fab");
  const toggle = document.getElementById("chat-toggle");
  const bottom = document.getElementById("chat-snap-bottom");
  const right = document.getElementById("chat-snap-right");
  if (fab) fab.hidden = chatDock.open;
  if (toggle) {
    toggle.textContent = chatDock.open ? "Hide chat" : "Chat";
    toggle.classList.toggle("on", chatDock.open);
  }
  if (bottom) bottom.classList.toggle("on", chatDock.snap === "bottom");
  if (right) right.classList.toggle("on", chatDock.snap === "right");
  try {
    localStorage.setItem(CHAT_DOCK_KEY, JSON.stringify(chatDock));
  } catch (err) {}
  drawWires();
}

function setChatDock(next) {
  if (next.open != null) chatDock.open = !!next.open;
  if (next.snap === "bottom" || next.snap === "right") chatDock.snap = next.snap;
  applyChatDock();
  if (chatDock.open) {
    const input = document.getElementById("chat-input");
    if (input) input.focus();
  }
}

function toggleChatDock() {
  setChatDock({ open: !chatDock.open });
}

function ensureChatOpen() {
  if (!chatDock.open) setChatDock({ open: true });
}

(function bindChatDock() {
  applyChatDock();
  const fab = document.getElementById("chat-fab");
  const toggle = document.getElementById("chat-toggle");
  const close = document.getElementById("chat-close");
  const bottom = document.getElementById("chat-snap-bottom");
  const right = document.getElementById("chat-snap-right");
  if (fab) fab.addEventListener("click", toggleChatDock);
  if (toggle) toggle.addEventListener("click", toggleChatDock);
  if (close) close.addEventListener("click", function () { setChatDock({ open: false }); });
  if (bottom) bottom.addEventListener("click", function () { setChatDock({ open: true, snap: "bottom" }); });
  if (right) right.addEventListener("click", function () { setChatDock({ open: true, snap: "right" }); });
  document.addEventListener("keydown", function (event) {
    if ((event.ctrlKey || event.metaKey) && event.key === "/") {
      event.preventDefault();
      toggleChatDock();
      return;
    }
    if (event.key !== "Escape") return;
    if (calOpen) {
      closeCalPop();
      return;
    }
    if (chatDock.open) setChatDock({ open: false });
  });
})();

window.Constructor = {
  KINDS,
  OBJECTS,
  ACTIONS,
  FETCH_PLACES,
  LINKS,
  PERSONAS,
  ACTION_META,
  SOURCE_KINDS,
  provenanceStamp,
  ghost: true,
  automate: false,
  getState: () => state,
  selected: () => state.nodes.find((n) => n.id === selectedId) || null,
  addNode,
  wire,
  setGhost,
  showAudit,
  showDecision,
  decisionText,
  mockWalk,
  mockHop,
  intendedTask,
  bindDesk,
  openCalPop,
  closeCalPop,
  markGhostWalk,
  loadCompilePath,
  replaceGraph,
  ensureKinds,
  patchSelected,
  replaceCatalog,
  selectedId: () => selectedId,
  render,
  save,
  applySeed,
  lastRun: null,
  setChatDock,
  toggleChatDock,
  ensureChatOpen,
};

const power = document.getElementById("power");
if (power) {
  power.textContent = cortexOrigin()
    ? "Cortex. Key is under More."
    : "Sketch. Ghost only.";
}
const keyBox = document.getElementById("cortex-key");
if (keyBox && !cortexOrigin()) keyBox.hidden = true;

setGhost(true);
render();
