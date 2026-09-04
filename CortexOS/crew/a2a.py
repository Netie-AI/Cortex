"""Structured agent-to-agent messaging for one crew space.

This replaces the bare ``asyncio.Queue[str]`` inbox the runtime used to carry
teammate traffic. The string queue could not do three things, and each gap was
a real failure the operator could see:

- **Correlate a reply.** ``Switchboard.ask`` blocks the caller until *that
  target* answers, so two teammates can hold a conversation without a third
  agent's report being mistaken for the answer.
- **Say who is talking.** Every item carries sender, recipient, kind, and the
  id it answers, so the transcript and the UI can draw the real graph instead
  of a flat list of strings.
- **Fail loudly.** A pending ask whose target dies is resolved with a visible
  error instead of burning the full timeout in silence (R-0011: a silent
  fallback is a lie).

**Correlation is exact where it can be.** When an agent is asked something the
runtime remembers the question id and stamps ``reply_to`` on that agent's next
message back to the asker, so the answer is matched to the question rather
than to the sender. Where no stamp is present the fallback is deliberately
narrow: only a kind in :data:`ANSWERING_KINDS` may resolve an ask. A question,
a brief, or a broadcast never can - correlating on the sender alone meant a
teammate's own clarifying question came back to the Manager labelled as the
answer, and both sides then sat waiting on each other.

The mailbox is a wake signal and a delivery channel only. The durable record
is the transcript in :mod:`CortexOS.crew.store`; an agent that is restarted
rebuilds its context from there, never from whatever happened to still be
sitting in a queue. Every envelope therefore carries the ``seq`` of its stored
message, so a rebuilt agent can drop the envelopes its history already covers
rather than reading them twice.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Any

BRIEF = "brief"
TELL = "tell"
ASK = "ask"
REPLY = "reply"
BROADCAST = "broadcast"
USER = "user"
REPORT = "report"

BROADCAST_TARGET = "*"

#: Kinds that may resolve a pending ask when there is no exact ``reply_to``.
#: An ASK is by definition not a reply; a BRIEF and a BROADCAST are addressed
#: to the room rather than to the question.
ANSWERING_KINDS = frozenset({REPLY, TELL, REPORT})

#: Reported back when an ask ended without anyone answering it.
NO_ANSWER = "none"

#: HUD thread status. These are not delivery kinds; the bus stays Envelope.kind.
WAITING = "waiting"
DEAD = "dead"
TIMEOUT = "timeout"
ANSWERED = "answered"
OPEN = "open"
SENT = "sent"

#: How a kind is announced to the model. Kept terse; models read a lot of these.
_LEAD = {
    BRIEF: "brief",
    TELL: "message",
    ASK: "question - answer it",
    REPLY: "reply",
    BROADCAST: "broadcast",
    USER: "operator",
    REPORT: "report",
}


@dataclass(frozen=True)
class Envelope:
    """One A2A delivery. ``seq`` is the stored message's per-space sequence."""

    id: str
    kind: str
    from_id: str
    from_name: str
    to_id: str | None
    to_name: str
    text: str
    seq: int = 0
    reply_to: str | None = None

    def render(self) -> str:
        """The single line an agent sees in its context for this envelope."""
        lead = _LEAD.get(self.kind, self.kind)
        if self.kind == BROADCAST:
            head = f"[{lead} from {self.from_name} to everyone]"
        elif self.kind == ASK:
            head = f"[{lead} from {self.from_name}, ref {self.id}]"
        else:
            head = f"[{lead} from {self.from_name}]"
        if self.reply_to:
            head = head[:-1] + f", answering {self.reply_to}]"
        return f"{head} {self.text}"

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "from": self.from_name,
            "to": self.to_name,
            "seq": self.seq,
            "reply_to": self.reply_to,
        }


class Mailbox:
    """One agent's delivery queue.

    Unbounded on purpose: dropping an A2A message would lose work silently,
    and the run budget already bounds how many can ever be produced. Backed by
    a deque rather than an ``asyncio.Queue`` so the runtime can look at what is
    waiting without consuming it - deciding whether an unanswered operator
    message is still sitting here needs a look, not a read.
    """

    def __init__(self) -> None:
        self._items: deque[Envelope] = deque()
        self._ready = asyncio.Event()

    def put(self, env: Envelope) -> None:
        self._items.append(env)
        self._ready.set()

    def drain(self, after_seq: int = 0) -> list[Envelope]:
        """Take everything queued now. Envelopes at or below ``after_seq`` are
        dropped: the agent's rebuilt history already contains them."""
        out: list[Envelope] = []
        while self._items:
            env = self._items.popleft()
            if env.seq and env.seq <= after_seq:
                continue
            out.append(env)
        self._ready.clear()
        return out

    async def get(self, timeout: float | None = None) -> Envelope | None:
        """Take the next envelope. ``timeout is None`` waits until one arrives
        or the waiter is cancelled - that is smart-idle, not a second loop."""
        if not self._items:
            try:
                if timeout is None:
                    await self._ready.wait()
                else:
                    await asyncio.wait_for(self._ready.wait(), timeout=timeout)
            except (TimeoutError, asyncio.TimeoutError):
                return None
        if not self._items:
            return None
        env = self._items.popleft()
        if not self._items:
            self._ready.clear()
        return env

    def unget(self, env: Envelope) -> None:
        """Put ``env`` back at the front so a parked waiter can re-drain it."""
        self._items.appendleft(env)
        self._ready.set()

    def empty(self) -> bool:
        return not self._items

    def snapshot(self) -> tuple[Envelope, ...]:
        """What is waiting, without consuming it."""
        return tuple(self._items)

    def has_kind(self, kind: str) -> bool:
        return any(env.kind == kind for env in self._items)


@dataclass
class _Ask:
    #: resolves to (kind, text); kind is NO_ANSWER when nobody actually replied
    future: asyncio.Future[tuple[str, str]]
    question_id: str


class Switchboard:
    """Mailboxes plus pending asks for every agent the runtime knows about.

    An ask is keyed ``(asker_id, target_id)``. The runtime correlates the
    answer, not the model, so a teammate cannot lose an answer by forgetting to
    quote an id - but the match is only accepted from an envelope that could
    actually be an answer.
    """

    def __init__(self) -> None:
        self._boxes: dict[str, Mailbox] = {}
        self._asks: dict[tuple[str, str], _Ask] = {}

    # -- mailboxes ---------------------------------------------------------

    def mailbox(self, agent_id: str) -> Mailbox:
        box = self._boxes.get(agent_id)
        if box is None:
            box = Mailbox()
            self._boxes[agent_id] = box
        return box

    def forget(self, agent_id: str) -> None:
        self._boxes.pop(agent_id, None)

    # -- delivery ----------------------------------------------------------

    def deliver(self, env: Envelope) -> str | None:
        """Route one envelope. Returns the kind that answered a pending ask,
        or None when nothing was waiting on it.

        An answered ask is not also queued: the asker is blocked inside
        :meth:`ask` and would otherwise read the same text twice. The caller
        gets the kind back so it can say honestly whether the target actually
        answered the question or merely finished and reported.
        """
        if env.to_id is None:
            return None
        key = (env.to_id, env.from_id)
        pending = self._asks.get(key)
        if pending is not None and not pending.future.done():
            exact = bool(env.reply_to) and env.reply_to == pending.question_id
            if exact or env.kind in ANSWERING_KINDS:
                kind = REPLY if exact else env.kind
                pending.future.set_result((kind, env.text))
                self._asks.pop(key, None)
                return kind
        self.mailbox(env.to_id).put(env)
        return None

    # -- asks --------------------------------------------------------------

    def would_deadlock(self, asker_id: str, target_id: str) -> bool:
        """True when the target is already blocked asking the asker.

        Arming the second half of that cycle guarantees both sides wait out
        their timeouts, so the runtime refuses it and says why.
        """
        return (target_id, asker_id) in self._asks

    def open_ask(
        self, asker_id: str, target_id: str, question_id: str
    ) -> asyncio.Future[tuple[str, str]]:
        key = (asker_id, target_id)
        stale = self._asks.pop(key, None)
        if stale is not None and not stale.future.done():
            stale.future.set_result((NO_ANSWER, "(superseded by a newer question)"))
        future: asyncio.Future[tuple[str, str]] = asyncio.get_running_loop().create_future()
        self._asks[key] = _Ask(future=future, question_id=question_id)
        return future

    def rekey_ask(self, asker_id: str, target_id: str, question_id: str) -> None:
        """Attach the real question id once the message has been stored."""
        pending = self._asks.get((asker_id, target_id))
        if pending is not None:
            pending.question_id = question_id

    def close_ask(self, asker_id: str, target_id: str) -> None:
        self._asks.pop((asker_id, target_id), None)

    def pending_asks(self) -> list[dict[str, Any]]:
        """Open asks the operator HUD can paint. Not a second mailbox."""
        out: list[dict[str, Any]] = []
        for (asker_id, target_id), ask in self._asks.items():
            if ask.future.done():
                continue
            out.append(
                {
                    "asker_id": asker_id,
                    "target_id": target_id,
                    "question_id": ask.question_id,
                    "status": WAITING,
                }
            )
        return out

    def abandon(self, agent_id: str, reason: str) -> list[dict[str, str]]:
        """Resolve every ask waiting on ``agent_id``; it is not coming back.

        Returns one row per unblocked asker, including the question id, so the
        HUD can stamp the dead closer onto that thread instead of a silent
        stall (R-0011).
        """
        freed: list[dict[str, str]] = []
        for key in list(self._asks):
            asker_id, target_id = key
            if target_id != agent_id:
                continue
            pending = self._asks.pop(key)
            if not pending.future.done():
                pending.future.set_result((NO_ANSWER, f"(no answer: {reason})"))
                freed.append(
                    {
                        "asker_id": asker_id,
                        "target_id": target_id,
                        "question_id": pending.question_id,
                        "reason": reason,
                    }
                )
        return freed

    def waiting_targets(self, asker_id: str) -> list[str]:
        return [t for (a, t) in self._asks if a == asker_id]

    def pending_count(self) -> int:
        return len(self._asks)

    def any_waiting(self) -> bool:
        """True when any mailbox still holds unread envelopes.

        Derived look only. Does not allocate a second mailbox.
        """
        return any(not box.empty() for box in self._boxes.values())


def a2a_meta(msg: dict[str, Any]) -> dict[str, Any]:
    """Wire stamp on a stored message. CMD and the HUD both read this."""
    meta = msg.get("meta") if isinstance(msg.get("meta"), dict) else {}
    wire = meta.get("a2a") if isinstance(meta, dict) else None
    return dict(wire) if isinstance(wire, dict) else {}


def is_wire(msg: dict[str, Any]) -> bool:
    """True when the operator HUD should treat this row as Switchboard traffic.

    Agent rows are the original tape. Any role with ``meta.a2a`` is included
    so CMD operator ``@`` lines (role=user) and dead-ask closers (role=system)
    paint on the same bus rather than a fork.
    """
    if a2a_meta(msg):
        return True
    return msg.get("role") == "agent"


def hop_public(msg: dict[str, Any]) -> dict[str, Any]:
    wire = a2a_meta(msg)
    return {
        "id": msg.get("id"),
        "kind": wire.get("kind") or "message",
        "from": wire.get("from") or "",
        "to": wire.get("to") or "",
        "text": str(msg.get("content") or ""),
        "seq": int(msg.get("seq") or 0),
        "reply_to": wire.get("reply_to"),
        "status": wire.get("status"),
    }


def _thread_status(
    kind: str, mid: str, hops: list[dict[str, Any]], pending_q: set[str]
) -> str:
    if any(h.get("status") == DEAD for h in hops):
        return DEAD
    if any(h.get("status") == TIMEOUT for h in hops):
        return TIMEOUT
    if any(h.get("kind") == NO_ANSWER for h in hops):
        return DEAD
    if mid in pending_q:
        return WAITING
    if hops:
        return ANSWERED
    if kind == ASK:
        return OPEN
    return SENT


def hud_threads(
    messages: list[dict[str, Any]],
    pending: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Group ``/spaces/messages`` into correlated ask/reply threads.

    Roots are wires without ``reply_to``. Children attach by ``reply_to`` =
    the ask's message id. Pending overlay is live Switchboard state, not a
    second inbox.
    """
    pending = pending or []
    pending_q = {str(p.get("question_id") or "") for p in pending if p.get("question_id")}
    children: dict[str, list[dict[str, Any]]] = {}
    roots: list[dict[str, Any]] = []
    for msg in messages:
        if not is_wire(msg):
            continue
        rid = a2a_meta(msg).get("reply_to")
        if rid:
            children.setdefault(str(rid), []).append(msg)
        else:
            roots.append(msg)

    threads: list[dict[str, Any]] = []
    for msg in roots:
        mid = str(msg.get("id") or "")
        hops = [hop_public(child) for child in children.pop(mid, [])]
        wire = a2a_meta(msg)
        kind = str(wire.get("kind") or "message")
        threads.append(
            {
                "id": mid,
                "status": _thread_status(kind, mid, hops, pending_q),
                "kind": kind,
                "from": wire.get("from") or "",
                "to": wire.get("to") or "",
                "text": str(msg.get("content") or ""),
                "seq": int(msg.get("seq") or 0),
                "reply_to": None,
                "hops": hops,
            }
        )

    for rid, kids in children.items():
        hops = [hop_public(child) for child in kids]
        first = hops[0] if hops else {}
        threads.append(
            {
                "id": rid,
                "status": _thread_status(ASK, rid, hops, pending_q),
                "kind": ASK,
                "from": first.get("to") or "",
                "to": first.get("from") or "",
                "text": "",
                "seq": int(first.get("seq") or 0),
                "reply_to": None,
                "hops": hops,
            }
        )

    have = {str(t["id"]) for t in threads}
    for row in pending:
        qid = str(row.get("question_id") or "")
        if not qid or qid in have:
            continue
        threads.append(
            {
                "id": qid,
                "status": WAITING,
                "kind": ASK,
                "from": row.get("from") or "",
                "to": row.get("to") or "",
                "text": "",
                "seq": 0,
                "reply_to": None,
                "hops": [],
            }
        )
    threads.sort(key=lambda t: int(t.get("seq") or 0))
    return threads


def hud(
    messages: list[dict[str, Any]],
    pending: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Operator HUD payload. ``bus`` names the existing Switchboard."""
    pending = pending or []
    return {
        "bus": "switchboard",
        "pending": len(pending),
        "asks": pending,
        "threads": hud_threads(messages, pending),
    }


def lead_for(kind: str) -> str:
    """The word used to announce ``kind`` to a model. Shared by live delivery
    and by history rebuild so a replayed message reads identically to a fresh
    one - an agent should not be able to tell it was restarted."""
    return _LEAD.get(kind, kind)


def render_all(envs: list[Envelope]) -> str:
    return "\n---\n".join(e.render() for e in envs)
