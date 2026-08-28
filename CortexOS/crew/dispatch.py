"""Wave planner for a fan-out of crew work. It plans; it never runs anything.

``CortexOS/crew/runtime.py`` lets the Manager spawn teammates one at a time,
so a wave of work arrives with no declared batching and no declared ceiling.
The board law is that Ticket Runner seats existing writers - it does not swarm
- and a ceiling nobody wrote down is a ceiling nobody can review. This module
turns a list of candidate items into ordered waves, each no wider than the
caller's concurrency cap, and states the reason every item landed where it did.

It returns a plan and nothing else: no spawning, no execution, no scheduling,
no retry, no clock. It is **not** a second DAG - ``dag_runner`` + manifest +
ledger stay the decision layer for governed work. A plan from here is an
argument the operator (or the Manager) reads and may refuse.

Every refusal names what it refused. A cycle comes back printed as a path and
an unknown dependency names both the missing id and the item that declared it,
because silently breaking a cycle would return a plan that starts work before
its input exists - which surfaces much later as a data bug rather than as the
planning bug it is (KB R-0011: a control that refuses returns its reason).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

#: Matches nothing in particular on purpose - callers pass their own ceiling.
#: Four is a readable default for an operator eyeballing a plan, not a tuned
#: throughput number.
DEFAULT_MAX_PARALLEL = 4

_WHITE, _GREY, _BLACK = 0, 1, 2


class DispatchRefused(ValueError):
    """A plan was refused outright.

    No partial plan is ever returned alongside this: half a plan for a cyclic
    or dangling graph would be read as a working plan for the part that parsed.
    """


def as_error(exc: BaseException) -> str:
    """Refusal text for a transcript or a tool result, matching workspace.as_error."""
    return f"DENIED: {exc}"


@dataclass(frozen=True)
class WorkItem:
    """One candidate piece of work. ``cost`` is a hint, not a measurement."""

    id: str
    description: str = ""
    depends_on: tuple[str, ...] = ()
    cost: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "depends_on": list(self.depends_on),
            "cost": self.cost,
        }


@dataclass(frozen=True)
class Placement:
    """Where one item landed, and why it landed there."""

    id: str
    wave: int
    reason: str
    cost: int
    unblocks: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "wave": self.wave,
            "reason": self.reason,
            "cost": self.cost,
            "unblocks": self.unblocks,
        }


@dataclass(frozen=True)
class Wave:
    """One batch that may run in parallel. Never wider than the cap."""

    index: int
    placements: tuple[Placement, ...]

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(p.id for p in self.placements)

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "ids": list(self.ids),
            "placements": [p.as_dict() for p in self.placements],
        }


@dataclass(frozen=True)
class DispatchPlan:
    """Ordered waves plus the ceiling they were planned under."""

    max_parallel: int
    waves: tuple[Wave, ...]

    @property
    def item_count(self) -> int:
        return sum(len(w.placements) for w in self.waves)

    @property
    def width(self) -> int:
        """Widest wave. Asserted against the cap by the planner and by tests."""
        return max((len(w.placements) for w in self.waves), default=0)

    def wave_of(self, item_id: str) -> int | None:
        for wave in self.waves:
            for placement in wave.placements:
                if placement.id == item_id:
                    return wave.index
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "plan",
            "decides_work_shape": False,
            "max_parallel": self.max_parallel,
            "item_count": self.item_count,
            "width": self.width,
            "waves": [w.as_dict() for w in self.waves],
        }

    def render(self) -> str:
        head = (
            f"plan: {self.item_count} item(s) in {len(self.waves)} wave(s),"
            f" cap {self.max_parallel} (plan only - nothing was spawned)"
        )
        lines = [head]
        for wave in self.waves:
            lines.append(
                f"wave {wave.index} ({len(wave.placements)}/{self.max_parallel}):"
                f" {', '.join(wave.ids)}"
            )
            for placement in wave.placements:
                lines.append(f"  {placement.id} - {placement.reason}")
        return "\n".join(lines)


def parse_items(raw: Sequence[Any]) -> tuple[WorkItem, ...]:
    """Build WorkItems from loose dicts (a tool call's JSON arguments).

    Refusals name the offending row index, because a model that passed a
    malformed list needs to be told which entry it got wrong rather than
    receiving a plan built from the rows that happened to parse.
    """
    items: list[WorkItem] = []
    for index, row in enumerate(raw or []):
        if not isinstance(row, dict):
            raise DispatchRefused(f"item {index} is not an object: {type(row).__name__}")
        item_id = str(row.get("id") or "").strip()
        if not item_id:
            raise DispatchRefused(f"item {index} has no id")
        depends_raw = row.get("depends_on") or row.get("deps") or []
        if isinstance(depends_raw, str):
            depends_raw = [p for p in depends_raw.split(",")]
        if not isinstance(depends_raw, list):
            raise DispatchRefused(f"item '{item_id}' has a non-list depends_on")
        depends: list[str] = []
        for dep in depends_raw:
            text = str(dep).strip()
            if text and text not in depends:
                depends.append(text)
        try:
            cost = int(row.get("cost", 1))
        except (TypeError, ValueError) as exc:
            raise DispatchRefused(f"item '{item_id}' has a non-integer cost hint") from exc
        items.append(
            WorkItem(
                id=item_id,
                description=str(row.get("description") or "").strip(),
                depends_on=tuple(depends),
                cost=cost,
            )
        )
    return tuple(items)


def plan(
    items: Sequence[WorkItem],
    *,
    max_parallel: int = DEFAULT_MAX_PARALLEL,
) -> DispatchPlan:
    """Rank ``items`` into waves no wider than ``max_parallel``.

    Ranking inside one wave is deterministic and has no clock in it: an item
    that unblocks more downstream work is seated first (the critical path is
    what makes the whole fan-out long), then the costlier item (starting the
    long pole late is what leaves a wave half idle), then declaration order so
    that equal candidates never reorder between two runs of the same input.

    Raises DispatchRefused - with the cycle path, or with the unknown id and
    the item that named it - rather than returning a partial plan.
    """
    if max_parallel < 1:
        raise DispatchRefused(f"concurrency cap must be at least 1, got {max_parallel}")

    order = _validate(items)
    deps = {item.id: item.depends_on for item in items}
    cost = {item.id: item.cost for item in items}
    position = {item_id: i for i, item_id in enumerate(order)}

    cycle = _find_cycle(order, deps)
    if cycle is not None:
        # Printed by following declared dependencies, so the arrow reads
        # "waits on". Without that said, a reader can take the same path for
        # an execution order and conclude the planner emitted one.
        raise DispatchRefused(
            "dependency cycle refused (each item waits on the next): "
            + " -> ".join(cycle)
        )

    unblocks = _unblocks(order, deps)

    scheduled: set[str] = set()
    landed: dict[str, int] = {}
    held: dict[str, int] = {}
    waves: list[Wave] = []
    remaining = list(order)
    index = 0

    while remaining:
        ready = [i for i in remaining if all(d in scheduled for d in deps[i])]
        if not ready:
            # Unreachable once _find_cycle has passed; kept because a planner
            # that quietly emitted a short plan would look like it had finished.
            raise DispatchRefused(
                "no item is runnable and none were planned: " + ", ".join(remaining)
            )
        ranked = sorted(ready, key=lambda i: (-unblocks[i], -cost[i], position[i]))
        taken = ranked[:max_parallel]
        bumped = ranked[max_parallel:]

        placements: list[Placement] = []
        for item_id in taken:
            placements.append(
                Placement(
                    id=item_id,
                    wave=index,
                    reason=_reason(
                        item_id,
                        deps=deps[item_id],
                        landed=landed,
                        held=held.get(item_id, 0),
                        cap=max_parallel,
                        unblocks=unblocks[item_id],
                    ),
                    cost=cost[item_id],
                    unblocks=unblocks[item_id],
                )
            )
        waves.append(Wave(index=index, placements=tuple(placements)))

        for item_id in taken:
            scheduled.add(item_id)
            landed[item_id] = index
        for item_id in bumped:
            held[item_id] = held.get(item_id, 0) + 1
        remaining = [i for i in remaining if i not in scheduled]
        index += 1

    built = DispatchPlan(max_parallel=max_parallel, waves=tuple(waves))
    if built.width > max_parallel:
        # The ceiling is the whole point of the module; a plan that broke it
        # must not leave here looking valid.
        raise DispatchRefused(
            f"internal: planned a wave of {built.width} over the cap of {max_parallel}"
        )
    return built


def _validate(items: Sequence[WorkItem]) -> list[str]:
    """Return declaration order, refusing anything that makes a plan ambiguous."""
    order: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        item_id = item.id.strip()
        if not item_id:
            raise DispatchRefused(f"item {index} has an empty id")
        if item_id in seen:
            raise DispatchRefused(f"duplicate item id '{item_id}'")
        if item.cost < 0:
            raise DispatchRefused(
                f"item '{item_id}' has a negative cost hint ({item.cost});"
                " a negative hint would invert the ranking"
            )
        seen.add(item_id)
        order.append(item_id)
    for item in items:
        for dep in item.depends_on:
            if dep not in seen:
                raise DispatchRefused(
                    f"item '{item.id}' depends on unknown id '{dep}'"
                )
    return order


def _find_cycle(order: Sequence[str], deps: dict[str, tuple[str, ...]]) -> list[str] | None:
    """Return one cycle as a path (``a -> b -> a``), or None.

    Iterative on purpose: a deep chain of items must not turn a refusal into a
    RecursionError, which would reach the operator as a crash instead of as
    the named cycle it is.
    """
    color = dict.fromkeys(order, _WHITE)
    for root in order:
        if color[root] != _WHITE:
            continue
        color[root] = _GREY
        path: list[str] = [root]
        stack: list[tuple[str, int]] = [(root, 0)]
        while stack:
            node, cursor = stack[-1]
            children = deps[node]
            if cursor < len(children):
                stack[-1] = (node, cursor + 1)
                child = children[cursor]
                if color[child] == _GREY:
                    return [*path[path.index(child):], child]
                if color[child] == _WHITE:
                    color[child] = _GREY
                    path.append(child)
                    stack.append((child, 0))
            else:
                color[node] = _BLACK
                stack.pop()
                path.pop()
    return None


def _unblocks(order: Sequence[str], deps: dict[str, tuple[str, ...]]) -> dict[str, int]:
    """Count transitive dependents, so the critical path gets the first seats."""
    dependents: dict[str, list[str]] = {item_id: [] for item_id in order}
    for item_id in order:
        for dep in deps[item_id]:
            dependents[dep].append(item_id)
    counts: dict[str, int] = {}
    for item_id in order:
        seen: set[str] = set()
        queue = deque(dependents[item_id])
        while queue:
            node = queue.popleft()
            if node in seen:
                continue
            seen.add(node)
            queue.extend(dependents[node])
        counts[item_id] = len(seen)
    return counts


def _reason(
    item_id: str,
    *,
    deps: tuple[str, ...],
    landed: dict[str, int],
    held: int,
    cap: int,
    unblocks: int,
) -> str:
    bits: list[str] = []
    if held:
        bits.append(f"held {held} wave(s) by the cap of {cap}")
    if deps:
        bits.append("after " + ", ".join(f"{d} (wave {landed[d]})" for d in deps))
    else:
        bits.append("no declared dependencies")
    bits.append(f"unblocks {unblocks} later item(s)")
    return "; ".join(bits)
