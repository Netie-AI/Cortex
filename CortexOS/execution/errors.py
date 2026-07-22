class OutboundNotSendable(RuntimeError):
    """Raised when an outbound DAG node executes outside allowed send windows."""

    def __init__(self, node_id: str, reason: str) -> None:
        super().__init__(f"Node {node_id}: outbound blocked — {reason}")
        self.node_id = node_id
        self.reason = reason


class UnsupportedDAGNodeKind(RuntimeError):
    def __init__(self, node_id: str, kind: object) -> None:
        super().__init__(f"Node {node_id}: unsupported DAG kind {kind!r}")
        self.node_id = node_id
        self.kind = kind


class WorkflowCostCeilingExceeded(RuntimeError):
    """Raised when cumulative projected workflow spend would breach the workflow-level cap."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class CostCeilingExceeded(RuntimeError):
    """Raised before an LLM call when projected cumulative cost would breach the ceiling."""

    def __init__(
        self,
        node_id: str,
        *,
        projected_myr: float,
        spent_myr: float,
        ceiling_myr: float,
    ) -> None:
        total = spent_myr + projected_myr
        super().__init__(
            f"Node {node_id}: projected cumulative {total:.4f} MYR would breach ceiling "
            f"{ceiling_myr:.4f} MYR (spent {spent_myr:.4f}, projected_delta {projected_myr:.4f})"
        )
        self.node_id = node_id
        self.projected_myr = projected_myr
        self.spent_myr = spent_myr
        self.ceiling_myr = ceiling_myr
