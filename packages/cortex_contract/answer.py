from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import (
    BaseModel,
    Field,
    SerializerFunctionWrapHandler,
    ValidationError,
    model_serializer,
    model_validator,
)
from pydantic_core import InitErrorDetails, PydanticCustomError


class Badge(str, Enum):
    CERTIFIED = "certified"
    GOVERNED_METRIC = "governed_metric"
    QUERY_SKILL = "query_skill"
    SESSION = "session"
    ABSTAIN = "abstain"
    BLOCKED = "blocked"
    # 1.5.0: the plan+SQL loop could not ground an answer and is asking the user.
    # Not an abstain. Older clients ignore the value.
    RECONFIRM = "reconfirm"
    # 1.7.0: answered from the Space's documents, citing the passages it used.
    # Not governed data, so never SESSION. Older clients ignore the value.
    DOCUMENT = "document"


class AbstainReason(str, Enum):
    NO_TRUSTWORTHY_PATH = "no_trustworthy_path"
    POLICY_BLOCK = "policy_block"
    NEEDS_CLARIFICATION = "needs_clarification"


class Provenance(BaseModel):
    layer: str
    badge: Badge
    metric_id: str | None = None
    query_source: str | None = None
    assumptions: str | None = None


class AskPayloadColumn(BaseModel):
    """1.5.0: one column of a table the consumer selected for this ask."""

    name: str = Field(min_length=1)
    type: str | None = None
    description: str | None = None
    # 1.9.0: the consumer's mask state for this column. Fail closed: only an
    # explicit ``false`` lets the engine put this column's values in a prompt.
    masked: bool | None = Field(
        default=None,
        description=(
            "1.9.0 the consumer's mask state for this column. The plan+SQL path "
            "samples distinct values of a column into its model prompt only when "
            "this is false. True or absent: the column's name and type may reach "
            "the prompt, its values never do. A schema_context column this "
            "payload does not list has no mask state and is not sampled."
        ),
    )


class AskPayloadTable(BaseModel):
    """1.5.0: one selected table and its schema."""

    name: str = Field(min_length=1)
    columns: list[AskPayloadColumn] = Field(default_factory=list)
    description: str | None = None


class AskPayloadJoin(BaseModel):
    """1.5.0: one ontology join between two selected tables."""

    left_table: str = Field(min_length=1)
    left_column: str = Field(min_length=1)
    right_table: str = Field(min_length=1)
    right_column: str = Field(min_length=1)
    relation: str | None = None


class PlanSqlReading(BaseModel):
    """1.10.0: one reading a split reconfirm offers. No values, no SQL, no rows.

    When two SQL readings of the question disagree, the reconfirm may offer up
    to three of them. Send ``AskRequest.plan_sql_reading_id`` = ``reading_id``
    (with the same question, ``dms_payload``, ``schema_context``, session and
    Space) to run that reading again through every gate under the session
    grant.
    """

    reading_id: str = Field(
        min_length=1,
        max_length=64,
        description="1.10.0 opaque id the engine holds; read only by exact id.",
    )
    statement: str = Field(
        min_length=1,
        max_length=320,
        description=(
            "1.10.0 plain-language description Cortex wrote from the gated SQL's "
            "syntax tree after replacing every literal in it with '<a value>'. It "
            "contains no returned value and no SQL comment; the only literal it "
            "shows is the LIMIT row cap. The bound holds after the response scrub."
        ),
    )
    row_count: int = Field(
        ge=1,
        description="1.10.0 rows this reading returned in the offering ask, after its LIMIT.",
    )
    expires_at: str = Field(
        description=(
            "1.10.0 ISO-8601 UTC. The hold lives at most 600 s and never past the "
            "session manifest's expiry. A pick after it is HTTP 409 "
            "plan_sql_reading_unbound."
        ),
    )


class PlanSqlReconfirm(BaseModel):
    """1.5.0: why the plan+SQL loop would abstain, and the closest question it can run.

    Present only when the loop could not ground an answer. Yes
    (``AskRequest.plan_sql_confirm`` = ``yes``, with ``question`` set to
    ``closest_question``) runs closest_question, No answers not found in the
    database, or send ``AskRequest.plan_sql_reading_id`` to run one reading
    (1.10.0). Older clients ignore the object.
    """

    why: str = Field(min_length=1)
    closest_question: str = Field(min_length=1)
    # 1.10.0: the disagreeing readings a split offers. Omitted when empty, so a
    # reconfirm without readings serializes exactly as on 1.9.
    readings: list[PlanSqlReading] = Field(
        default_factory=list,
        max_length=3,
        description=(
            "1.10.0 up to three disagreeing readings of the question, each a "
            "description with a row count and no values. Present only when the "
            "engine's readings switch is on and the vote split; omitted when empty."
        ),
    )

    @model_serializer(mode="wrap")
    def _omit_empty_readings(self, handler: SerializerFunctionWrapHandler):  # noqa: ANN202
        # No return annotation: the JSON schema stays the model's own.
        data = handler(self)
        if isinstance(data, dict) and not data.get("readings"):
            data.pop("readings", None)
        return data


class AskPayload(BaseModel):
    """1.5.0: selected tables, their schema and ontology joins for one question.

    The payload can only narrow what the signed grant allows: a table or join
    outside the grant is refused before any model call.
    """

    tables: list[AskPayloadTable] = Field(min_length=1)
    joins: list[AskPayloadJoin] = Field(default_factory=list)


# Named wire fields only. Not a scan of question text, and not extra=forbid.
# Unknown keys are ignored, as on main. Full strictness is #388.
_REJECTED_WIRE_FIELDS = ("model", "strict", "provider")


class AskRequest(BaseModel):
    """Ask plane request.

    Unknown keys are ignored, as on main. ``model``, ``strict`` and
    ``provider`` are rejected by name before any model call. Routing stays
    in OpenVault's route config. Full request strictness is a breaking
    change tracked in #388.
    """

    @model_validator(mode="before")
    @classmethod
    def _reject_named_wire_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        errors: list[InitErrorDetails] = []
        for name in _REJECTED_WIRE_FIELDS:
            if name not in data:
                continue
            errors.append(
                InitErrorDetails(
                    type=PydanticCustomError(
                        "rejected_wire_field",
                        "wire field is not accepted",
                    ),
                    loc=(name,),
                    input=data[name],
                )
            )
        if errors:
            raise ValidationError.from_exception_data(cls.__name__, errors)
        return data

    question: str = Field(min_length=1)
    session_id: str = "demo"
    space_id: str | None = None
    # 1.4.0: set on every ask of a scored round. Solution memory stays empty and
    # nothing is written to memory while it is set.
    scored_pack_id: str | None = None
    # 1.5.0: ignored unless the engine runs with its plan+SQL path switched on.
    dms_payload: AskPayload | None = None
    # 1.5.0: decision on a prior plan+SQL reconfirm. Absent on a normal ask.
    # ``yes`` runs ``question`` (the closest question). ``no`` answers
    # ``not found in the database`` and does not call a model.
    plan_sql_confirm: Literal["yes", "no"] | None = None
    # 1.9.0: the caller's schema shortlist, the same string and rule as
    # ``InsightsSchemaContext.schema_context`` on POST /v1/insights. Read only
    # when the engine's schema_context switch is on (off by default) and the
    # plan+SQL path answers the ask; otherwise only its declared type applies.
    schema_context: str | None = Field(
        default=None,
        max_length=8192,
        description=(
            "1.9.0 caller shortlist, the SCHEMA-CONTEXT-01 string POST /v1/insights "
            "takes. Read only when the engine has its schema_context switch on "
            "(off by default). Off, only its declared type (string or null, "
            "maxLength 8192) is checked and the answer is the one the same ask "
            "gets without this field. On, it is "
            "used only by the plan+SQL path (dms_payload set and that path "
            "switched on): its SQL prompt is then the signed grant intersected "
            "with the tables, columns and joins this string declares, and a table "
            "outside the grant is dropped. Absent or blank: the dms_payload "
            "schema is the prompt, as on 1.7. A value the insights rule refuses "
            "(over 8192 UTF-8 bytes, a control or format character, a line "
            "outside the shapes it parses) is HTTP 422 with that code before "
            "any model call. A string that declares no table, or no granted "
            "table, is a named abstain. maxLength 8192; the engine cap is 8192 "
            "UTF-8 bytes."
        ),
    )
    # 1.10.0: pick one reading a split reconfirm offered. Needs dms_payload and
    # cannot be sent with plan_sql_confirm (422).
    plan_sql_reading_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description=(
            "1.10.0 a PlanSqlReading.reading_id from a prior reconfirm. Send it "
            "with the same question, dms_payload, schema_context, session and "
            "Space as the ask that offered it, and without plan_sql_confirm. The "
            "engine re-runs that reading's SQL through every gate under the "
            "current session grant, with no model call. Without dms_payload, or "
            "together with plan_sql_confirm, it is HTTP 422. An id the engine does "
            "not hold for this session, Space, manifest, request and scored pack, "
            "or one past its expiry, is HTTP 409 plan_sql_reading_unbound; an "
            "engine with readings switched off answers HTTP 409 "
            "plan_sql_reading_unavailable. Through 1.9 this key was ignored: check "
            "Answer.plan_sql_reading_id."
        ),
    )

    @model_validator(mode="after")
    def _reading_is_a_pick(self) -> AskRequest:
        if self.plan_sql_reading_id is None:
            return self
        if self.dms_payload is None:
            raise PydanticCustomError(
                "reading_needs_payload",
                "plan_sql_reading_id needs dms_payload",
            )
        if self.plan_sql_confirm is not None:
            raise PydanticCustomError(
                "reading_excludes_confirm",
                "plan_sql_reading_id cannot be sent with plan_sql_confirm",
            )
        return self


class MemoryRead(BaseModel):
    """1.4.0: provenance of one per-Space memory entry an answer read."""

    id: str
    kind: str
    space_id: str
    source: str
    version: int
    written_at: str
    served_at: str


class ContributingSource(BaseModel):
    """One source card for the Sources panel (architecture §4.7 / §4.8)."""

    ref_id: str
    container: str | None = None
    member: str | None = None
    kind: str | None = None
    row_count: int | None = None
    contribution: float | None = None


class Answer(BaseModel):
    answer: str
    sql_used: str | None = None
    audit_id: str
    route: str
    row_count: int | None = None
    rows: list[dict[str, Any]] | None = None
    provenance: Provenance
    suggestions: list[str] = Field(default_factory=list)
    # T7 / contract 1.2.0 additive — older clients ignore these.
    answer_id: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    contributing_sources: list[ContributingSource] = Field(default_factory=list)
    drillthrough_token: str | None = None
    # 1.3.0: actual FreeRoute response identity for model-served L2 answers.
    served_provider: str | None = None
    served_model: str | None = None
    served_local: bool = False
    served_reason: str | None = None
    # 1.4.0 C-MEM: per-Space memory this answer read. [] when memory is off or
    # nothing was read. ``reused`` marks a stored solution re-run on current
    # data; it is never a validation by itself.
    memory_ids_read: list[str] = Field(default_factory=list)
    memory_reads: list[MemoryRead] = Field(default_factory=list)
    reused: bool = False
    # 1.5.0: set when the plan+SQL loop asks the user to confirm the closest
    # question. Null on every other answer. 1.4 clients ignore it.
    reconfirm: PlanSqlReconfirm | None = None
    # 1.5.0: which ladder rung produced this plan+SQL answer.
    # ``plan`` | ``error-fed-retry-<N>`` | ``stronger-model`` | ``reconfirm``.
    # Null when this path did not answer (flag off, direct abstain, confirm-no).
    # 1.4 clients ignore it.
    plan_sql_rung: str | None = None
    # 1.5.0: logical complete() calls per ladder step, plus ``total``.
    # One per complete() the ladder issues. A 429 retried under that call
    # is not a step. Null when this path made no model call.
    model_calls: dict[str, int] | None = Field(
        default=None,
        description=(
            "1.5.0 logical FreeRoute complete() calls per ladder step, plus "
            "total. One count per complete() the ladder issues. Transport or "
            "proxy retries (an upstream 429 retried by the cap proxy or the "
            "FreeRoute client) are not counted. Keys that ran: plan, "
            "error-fed-retry-<N> (N starts at 1), stronger-model, reconfirm. "
            "total is their sum. DMS reads per-step counts only from this "
            "object. Null when this path made no model call. 1.4 clients "
            "ignore it."
        ),
    )
    # 1.10.0: echo of AskRequest.plan_sql_reading_id. Omitted when null.
    plan_sql_reading_id: str | None = Field(
        default=None,
        description=(
            "1.10.0 the plan_sql_reading_id this ask sent, on every 200 response "
            "to a pick: a served answer, a reconfirm the pick re-gated into, or a "
            "direct abstain. Omitted on every other answer. A served pick "
            "(sql_used set) is an answer the user chose between disagreeing "
            "readings: a second reading did not confirm it, and it must not be "
            "presented as validated. On it plan_sql_rung is the rung whose model "
            "wrote the reading in the offering ask, served_* name that model, "
            "provenance.query_source carries that earlier call id, and "
            "model_calls is null. A reconfirm or abstain carrying the echo is "
            "presented as what it is, never as the pick. A 200 to a pick without "
            "this echo, or with another id, came from an engine that ignored the "
            "field."
        ),
    )

    # 1.11.0 (VERIFIED-QUERY #309): ids of the steward-confirmed examples that
    # were in the SQL generator prompt for this served answer, best match
    # first. One id per element, never a joined string. Omitted when empty.
    # Not a phrasing-match id and not a stored answer. Older clients ignore it.
    verified_query_ids: list[str] = Field(
        default_factory=list,
        description=(
            "1.11.0 ids of the steward-confirmed question/SQL examples that "
            "were in the SQL generator prompt for this served answer, best "
            "match first. The SQL was generated and executed on current data; "
            "this does not mark a stored answer and reused stays false. "
            "Omitted when no example was used. Older clients ignore it."
        ),
    )

    @model_serializer(mode="wrap")
    def _omit_empty_optional(self, handler: SerializerFunctionWrapHandler):  # noqa: ANN202
        # No return annotation: the JSON schema stays the model's own. A null
        # reading echo and an empty verified_query_ids are omitted, so an
        # answer without either serializes exactly as on 1.9.
        data = handler(self)
        if isinstance(data, dict):
            if data.get("plan_sql_reading_id") is None:
                data.pop("plan_sql_reading_id", None)
            if not data.get("verified_query_ids"):
                data.pop("verified_query_ids", None)
        return data


class DrillthroughRequest(BaseModel):
    token: str = Field(min_length=1)


class DrillthroughResponse(BaseModel):
    answer_id: str
    session_id: str
    sql_used: str
    approximate: bool = False
    row_count: int
    total_count: int | None = None
    rows: list[dict[str, Any]] = Field(default_factory=list)


class InsightsSchemaContext(BaseModel):
    """1.6.0 request field on POST /v1/insights.

    The engine reads this on ``InsightsWireIn.schema_context``, not on
    ``InsightsAskIn``. One optional string. Present: it replaces the pack
    table list. Absent: the pack list stays, and the response stays
    byte-equal to the 279cbd85 pin. The ``usage`` key is omitted when
    ``schema_context`` is absent or no count was reported.

    Shortlist pick reasons are not a sibling field. The caller writes them
    into this string: a table line carries ``reason=score:<score>``, a
    column line may carry ``reason=<why>``, and a join line carries
    ``reason=<why>``.
    """

    schema_context: str | None = Field(
        default=None,
        max_length=8192,
        description=(
            "Caller shortlist. Replaces the pack table list when present. "
            "Pick reasons live in this string, not in a separate field: "
            "table lines use reason=score:<score>, column lines may include "
            "reason=<why>, join lines include reason=<why>. "
            "maxLength 8192. The engine cap is 8192 UTF-8 bytes."
        ),
    )


class InsightsUsage(BaseModel):
    """1.6.0 response field ``usage`` on POST /v1/insights.

    Sum of the counts each FreeRoute complete() call reported. A count is
    null when it was omitted. A missing count is never stored as 0. The
    live crew return carries prompt_tokens and completion_tokens. total_tokens
    is null when that return omits the key. The key itself is omitted when
    schema_context is absent or no count was reported.
    """

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
