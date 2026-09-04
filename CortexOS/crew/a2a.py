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

    def abandon(self, agent_id: str, reason: str) -> list[str]:
        """Resolve every ask waiting on ``agent_id``; it is not coming back.

        Returns the asker ids that were unblocked, so the caller can record
        that a wait ended because the target died rather than because it
        answered.
        """
        freed: list[str] = []
        for key in list(self._asks):
            asker_id, target_id = key
            if target_id != agent_id:
                continue
            pending = self._asks.pop(key)
            if not pending.future.done():
                pending.future.set_result((NO_ANSWER, f"(no answer: {reason})"))
                freed.append(asker_id)
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


def lead_for(kind: str) -> str:
    """The word used to announce ``kind`` to a model. Shared by live delivery
    and by history rebuild so a replayed message reads identically to a fresh
    one - an agent should not be able to tell it was restarted."""
    return _LEAD.get(kind, kind)


def render_all(envs: list[Envelope]) -> str:
    return "\n---\n".join(e.render() for e in envs)
