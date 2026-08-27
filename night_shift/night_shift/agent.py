"""ADK 2 root agent -- three webinar patterns as a Workflow graph.

Sequential: scout_extract.
Parallel fan-out: stock + vendor memory + armor.
Loop / route: critic retries or hands to placer.
App.resumability_config=True so a crash can continue.
place_order_tool is idempotent because Resume may re-run it.
"""

from __future__ import annotations

from typing import Any

from night_shift import MODEL_FLASH
from night_shift.gateway import check as gateway_check
from night_shift.idempotency import OrderLedger
from night_shift.memory import MemoryBank
from night_shift.pipeline import check_stock, critic_pass, extract_po, recall_vendor

_LEDGER = OrderLedger()
_MEMORY = MemoryBank()
_MEMORY.remember(
    "Ah Seng supplies M8 bolts. Last PO-0007 was 200 pcs week 33.",
    kind="vendor_habit",
    vendor="Ah Seng",
)
_MEMORY.set_session("on_hand_m8", 40)


def scout_extract(inbox: str) -> dict[str, Any]:
    return extract_po(inbox if isinstance(inbox, str) else str(inbox))


def stock_node(draft: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(draft, dict):
        draft = scout_extract(str(draft))
    return check_stock(draft, on_hand=int(_MEMORY.get_session("on_hand_m8", 0)))


def vendor_node(draft: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(draft, dict):
        draft = {}
    return recall_vendor(draft, _MEMORY)


def armor_node(draft: dict[str, Any]) -> dict[str, Any]:
    text = " ".join(str(v) for v in draft.values()) if isinstance(draft, dict) else str(draft)
    return gateway_check(agent_id="floor.scout.v1", tool="extract", payload_text=text)


def place_order_tool(vendor: str, sku: str, qty: int, week: str) -> dict[str, Any]:
    gate = gateway_check(
        agent_id="floor.placer.v1",
        tool="place_order",
        payload_text=f"{vendor} {sku} {qty} {week}",
    )
    if not gate["allow"]:
        return {"status": "blocked", "gate": gate}
    return _LEDGER.place(vendor=vendor, sku=sku, qty=int(qty), week=week)


def build_root_agent():
    from google.adk import Agent, Workflow
    from google.adk.apps.app import App, ResumabilityConfig
    from google.adk.workflow import START, JoinNode

    scout = Agent(
        name="scout",
        model=MODEL_FLASH,
        instruction=(
            "Extract vendor, SKU, qty, week from messy factory chat. "
            "Do not invent a vendor. Call scout_extract if you need a deterministic parse."
        ),
        tools=[scout_extract],
    )
    stock = Agent(
        name="stock",
        model=MODEL_FLASH,
        instruction="Report on-hand vs requested qty. Numeric only.",
        tools=[stock_node],
    )
    vendor_mem = Agent(
        name="vendor_memory",
        model=MODEL_FLASH,
        instruction="Recall last PO and price for this vendor from memory hits.",
        tools=[vendor_node],
    )
    armor = Agent(
        name="armor",
        model=MODEL_FLASH,
        instruction="If armor_node.allow is false, refuse. Never weaken a block.",
        tools=[armor_node],
    )
    critic = Agent(
        name="critic",
        model=MODEL_FLASH,
        instruction=(
            "If vendor/sku/qty/week is missing, say RETRY. If safe, say PLACE. "
            "No praise. Numbered risks only."
        ),
        tools=[critic_pass],
    )
    placer = Agent(
        name="placer",
        model=MODEL_FLASH,
        instruction=(
            "Call place_order_tool only after human approval. "
            "idempotent_replay is success, not a second PO."
        ),
        tools=[place_order_tool],
    )

    join = JoinNode(name="join_specialists")
    root = Workflow(
        name="night_shift",
        edges=[
            (START, scout, (stock, vendor_mem, armor)),
            (stock, join),
            (vendor_mem, join),
            (armor, join),
            (join, critic, placer),
        ],
    )
    return App(
        name="night_shift",
        root_agent=root,
        resumability_config=ResumabilityConfig(is_resumable=True),
    )


try:
    app = build_root_agent()
    root_agent = app.root_agent
except Exception:
    app = None
    root_agent = None
