"""OpenDMS streaming intake (S0) — webhook events → buffered → bronze lakehouse.

V0 tier (no broker): FastAPI accepts JSON events, a per-stream buffer batches
them, and a batch writer appends into `bronze.stream_<id>`. NATS/Redpanda tiers
(S1/S2) land the same bronze contract later. See
docs/dms/BUILD_PLAN_V2_LAKEHOUSE.md Feature S0 and
docs/research/findings/STREAMING_ORCH_2026.md.
"""
from packs.dms.streams.buffer import BackpressureError, append_events, buffer_depth, flush
from packs.dms.streams import registry

__all__ = ["BackpressureError", "append_events", "buffer_depth", "flush", "registry"]
