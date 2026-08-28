from night_shift.idempotency import OrderLedger, po_key
from night_shift.pipeline import DEMO_INBOX, NightShift, extract_po


def test_same_identity_same_key():
    a = po_key(vendor="Ah Seng", sku="M8", qty=200, week="week-33")
    b = po_key(vendor="ah seng", sku="m8", qty=200, week="week-33")
    assert a == b


def test_resume_does_not_order_twice():
    led = OrderLedger()
    led.crash_after_intent(vendor="Ah Seng", sku="M8", qty=200, week="week-33")
    first = led.place(vendor="Ah Seng", sku="M8", qty=200, week="week-33")
    second = led.place(vendor="Ah Seng", sku="M8", qty=200, week="week-33")
    assert first["status"] == "placed"
    assert second["status"] == "idempotent_replay"
    assert second["did_not_reorder"] is True
    assert first["po"]["po_id"] == second["po"]["po_id"]
    assert led.placed_count() == 1


def test_extract_demo_inbox():
    d = extract_po(DEMO_INBOX)
    assert d["vendor"] == "Ah Seng"
    assert d["sku"] == "M8"
    assert d["qty"] == 200


def test_full_graph_crash_resume():
    ns = NightShift()
    run = ns.start(DEMO_INBOX, "t1")
    ns.critic_until_pass(run)
    ns.approve("t1")
    ns.crash_before_commit("t1")
    ns.resume_place("t1")
    assert ns.ledger.placed_count() == 1
    assert run.result["resume_replay"]["status"] == "idempotent_replay"
    assert run.step == "done"
