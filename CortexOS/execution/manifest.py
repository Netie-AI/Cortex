"""Signed session manifest verification and enforcement.

DMS mints a manifest and signs it. Cortex verifies and enforces it. OpenVault
roots the key. No single one of those three can widen a session's reach on its
own, which is the whole point of splitting them.

Two halves live here:

**Verification** — is this manifest genuinely from an issuer we trust, right
now? Signature over :func:`canonical_manifest_bytes`, issuer resolved from a
JWKS cached on disk. Verification never touches the network: an OpenVault
outage must not take the appliance down, and a verify path that can block on a
socket is a verify path an attacker can stall.

**Enforcement** — does this SQL stay inside what the manifest grants? Answered
by walking the sqlglot AST, never by matching strings. Every table reference,
every ``read_parquet``/``read_csv``/``read_json`` argument and every ``ATTACH``
target is resolved and checked against ``allowed_paths``; every referenced table
carrying a predicate is rewritten to apply it.

The asymmetry is deliberate: anything this module cannot fully analyse is
refused. A query the enforcer does not understand is not a query it can prove
safe.
"""

from __future__ import annotations

import base64
import json
import os
import posixpath
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sqlglot
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import build_scope

from CortexOS.paths import data_path

try:  # installed wheel ships the contract as a top-level package
    from cortex_contract.execution import Manifest, canonical_manifest_bytes
except ImportError:  # in-repo checkout
    from packages.cortex_contract.execution import Manifest, canonical_manifest_bytes

__all__ = [
    "ManifestError",
    "ManifestMalformed",
    "ManifestUnknownIssuer",
    "ManifestSignatureInvalid",
    "ManifestExpired",
    "ManifestNotYetValid",
    "PathNotAllowed",
    "StatementNotAllowed",
    "SqlNotAnalyzable",
    "JwksCache",
    "ManifestVerifier",
    "VerifiedManifest",
    "enforce_manifest",
]


# ── errors ───────────────────────────────────────────────────────────────────
# Distinct classes, not one error with a string: DMS must react differently to
# "expired, re-mint once" and "rejected, log a security event and never
# re-mint", and a caller cannot branch safely on prose.


class ManifestError(Exception):
    """Base for every manifest refusal. Never raised directly."""

    #: Stable slug for logs, metrics and the DMS error-class mapping.
    code = "manifest_error"


class ManifestMalformed(ManifestError):
    """Structurally unusable — missing issuer, unparseable timestamp, bad signature encoding."""

    code = "manifest_malformed"


class ManifestUnknownIssuer(ManifestError):
    """``issuer_key_id`` is not in the cached JWKS, or its key has expired."""

    code = "manifest_unknown_issuer"


class ManifestSignatureInvalid(ManifestError):
    """Signature does not verify against the issuer's public key."""

    code = "manifest_signature_invalid"


class ManifestExpired(ManifestError):
    """``expires_at`` is in the past."""

    code = "manifest_expired"


class ManifestNotYetValid(ManifestError):
    """``issued_at`` is in the future by more than the allowed clock skew."""

    code = "manifest_not_yet_valid"


class PathNotAllowed(ManifestError):
    """The SQL reaches a file, database or URI outside ``allowed_paths``."""

    code = "path_not_allowed"


class StatementNotAllowed(ManifestError):
    """A statement class a read manifest never grants — writes, ATTACH, extensions."""

    code = "statement_not_allowed"


class SqlNotAnalyzable(ManifestError):
    """The enforcer could not fully analyse the SQL, so it refuses it.

    Not a lesser failure than the others. A query this module cannot read is a
    query it cannot prove safe, and approving it would make every check above
    decorative.
    """

    code = "sql_not_analyzable"


# ── time helpers ─────────────────────────────────────────────────────────────


def _parse_ts(raw: str | None, *, field_name: str) -> datetime:
    """Parse an ISO-8601 timestamp, requiring it to be unambiguous about zone.

    A naive timestamp is refused rather than assumed UTC. Guessing a zone in a
    security check means the same manifest is valid on one host and expired on
    another.
    """
    if not raw:
        raise ManifestMalformed(f"{field_name} is missing")
    text = raw.strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ManifestMalformed(f"{field_name} is not ISO-8601: {raw!r}") from exc
    if parsed.tzinfo is None:
        raise ManifestMalformed(f"{field_name} has no timezone offset: {raw!r}")
    return parsed.astimezone(timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── JWKS cache ───────────────────────────────────────────────────────────────


def _b64url_decode(raw: str, *, what: str) -> bytes:
    """Decode unpadded base64url. Rejects anything else — one encoding, strictly."""
    text = raw.strip()
    padding = "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(text + padding)
    except (ValueError, TypeError) as exc:
        raise ManifestMalformed(f"{what} is not base64url") from exc


@dataclass(frozen=True, slots=True)
class IssuerKey:
    """One Ed25519 public key from the JWKS, with the validity window it declares."""

    kid: str
    public_key: Ed25519PublicKey
    not_before: datetime | None = None
    not_after: datetime | None = None

    def usable_at(self, when: datetime, *, skew_s: int) -> bool:
        if self.not_before and when < self.not_before - _delta(skew_s):
            return False
        if self.not_after and when > self.not_after + _delta(skew_s):
            return False
        return True


def _delta(seconds: int):
    from datetime import timedelta

    return timedelta(seconds=seconds)


def _epoch_or_iso(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str):
        try:
            return _parse_ts(value, field_name="key validity")
        except ManifestMalformed:
            return None
    return None


def _parse_jwks(document: Any) -> dict[str, IssuerKey]:
    """Turn a JWKS document into usable keys, skipping entries we cannot use.

    A JWKS carrying one key type we do not support must not poison the whole
    set — otherwise OpenVault adding an RSA key for some unrelated purpose
    would silently stop every manifest from verifying.
    """
    keys: dict[str, IssuerKey] = {}
    if not isinstance(document, dict):
        return keys
    for entry in document.get("keys") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("kty") != "OKP" or entry.get("crv") != "Ed25519":
            continue
        kid = entry.get("kid")
        x = entry.get("x")
        if not isinstance(kid, str) or not isinstance(x, str):
            continue
        try:
            raw = _b64url_decode(x, what=f"jwk {kid}.x")
            public = Ed25519PublicKey.from_public_bytes(raw)
        except (ManifestMalformed, ValueError):
            continue
        keys[kid] = IssuerKey(
            kid=kid,
            public_key=public,
            not_before=_epoch_or_iso(entry.get("nbf")),
            not_after=_epoch_or_iso(entry.get("exp")),
        )
    return keys


DEFAULT_JWKS_PATH = "/keys/jwks"
DEFAULT_REFRESH_TTL_S = 900


class JwksCache:
    """Issuer public keys, cached on disk, refreshed off the hot path.

    Two clocks, deliberately separate:

    * the **cache TTL** says when a refresh is *wanted*. Passing it never makes
      cached keys unusable — that would turn an OpenVault outage into a total
      outage, which is exactly what the disk cache exists to prevent;
    * each key's own ``exp`` says when it stops being *trusted*. Intermediates
      are short-lived by design, so expiry is carried in the key material where
      an outage cannot quietly extend it.
    """

    def __init__(
        self,
        *,
        path: Path | None = None,
        ttl_s: int = DEFAULT_REFRESH_TTL_S,
        jwks_path: str = DEFAULT_JWKS_PATH,
    ) -> None:
        self.path = path or Path(
            os.environ.get("CORTEX_JWKS_CACHE") or data_path("engine", "openvault_jwks.json")
        )
        self.ttl_s = ttl_s
        self.jwks_path = jwks_path
        self._keys: dict[str, IssuerKey] = {}
        self._fetched_at: float = 0.0
        self._loaded = False

    # -- disk ---------------------------------------------------------------

    def _load_from_disk(self) -> None:
        self._loaded = True
        try:
            blob = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(blob, dict):
            return
        self._keys = _parse_jwks(blob.get("jwks"))
        fetched = blob.get("fetched_at")
        self._fetched_at = float(fetched) if isinstance(fetched, (int, float)) else 0.0

    def _write_to_disk(self, document: Any, fetched_at: float) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"fetched_at": fetched_at, "jwks": document}, indent=2),
                encoding="utf-8",
            )
        except OSError:
            # A read-only or full disk must not break verification for a
            # process that already holds the keys in memory.
            pass

    # -- reads (hot path — never network) ------------------------------------

    def get(self, kid: str) -> IssuerKey | None:
        if not self._loaded:
            self._load_from_disk()
        return self._keys.get(kid)

    @property
    def is_stale(self) -> bool:
        if not self._loaded:
            self._load_from_disk()
        return (time.time() - self._fetched_at) > self.ttl_s

    @property
    def known_kids(self) -> tuple[str, ...]:
        if not self._loaded:
            self._load_from_disk()
        return tuple(sorted(self._keys))

    # -- writes (cold path — may touch the network) --------------------------

    def install(self, document: Any, *, fetched_at: float | None = None) -> int:
        """Replace the cached set from a JWKS document. Returns keys accepted."""
        keys = _parse_jwks(document)
        if not keys:
            # Never let an empty or unparseable response erase working keys.
            return 0
        stamp = time.time() if fetched_at is None else fetched_at
        self._keys = keys
        self._fetched_at = stamp
        self._loaded = True
        self._write_to_disk(document, stamp)
        return len(keys)

    def refresh(self, *, timeout: float = 2.0) -> bool:
        """Fetch the JWKS from OpenVault. Cold path only — never call from verify().

        Returns whether the cache was updated. Failure is not an error: the
        cached keys keep working and the caller keeps serving.
        """
        from CortexOS.integrations.openvault_client import get_json

        document = get_json(self.jwks_path, timeout=timeout)
        if document is None:
            return False
        return self.install(document) > 0


# ── verification ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class VerifiedManifest:
    """A manifest whose signature, issuer and validity window all checked out.

    Enforcement takes one of these, never a raw ``Manifest`` — the type is the
    proof that verification happened, so no call site can skip it by accident.
    """

    manifest: Manifest
    issuer_kid: str
    verified_at: datetime

    @property
    def allowed_paths(self) -> tuple[str, ...]:
        return tuple(self.manifest.allowed_paths)

    @property
    def row_predicates(self) -> dict[str, str]:
        return dict(self.manifest.row_predicates)


DEFAULT_CLOCK_SKEW_S = 120


class ManifestVerifier:
    """Verifies manifests against cached issuer keys. Never makes a network call."""

    def __init__(
        self,
        cache: JwksCache | None = None,
        *,
        clock_skew_s: int = DEFAULT_CLOCK_SKEW_S,
    ) -> None:
        self.cache = cache or JwksCache()
        self.clock_skew_s = clock_skew_s

    def verify(self, manifest: Manifest, *, now: datetime | None = None) -> VerifiedManifest:
        """Return a :class:`VerifiedManifest` or raise a :class:`ManifestError` subclass.

        Checked in this order so the cheapest refusal comes first and a caller
        reading logs sees the most specific reason, not a generic one.
        """
        when = now or _now()

        kid = (manifest.issuer_key_id or "").strip()
        if not kid:
            raise ManifestMalformed("issuer_key_id is missing; nothing to verify against")
        if not manifest.signature:
            raise ManifestMalformed("signature is missing")

        issued_at = _parse_ts(manifest.issued_at, field_name="issued_at")
        expires_at = _parse_ts(manifest.expires_at, field_name="expires_at")
        if expires_at <= issued_at:
            raise ManifestMalformed("expires_at is not after issued_at")

        skew = _delta(self.clock_skew_s)
        if issued_at - skew > when:
            raise ManifestNotYetValid(
                f"issued_at {manifest.issued_at} is ahead of this host by more than "
                f"{self.clock_skew_s}s"
            )
        if expires_at + skew < when:
            raise ManifestExpired(f"expired at {manifest.expires_at}")

        key = self.cache.get(kid)
        if key is None:
            raise ManifestUnknownIssuer(f"issuer_key_id {kid!r} is not in the cached JWKS")
        if not key.usable_at(when, skew_s=self.clock_skew_s):
            raise ManifestUnknownIssuer(f"issuer key {kid!r} is outside its own validity window")

        signature = _b64url_decode(manifest.signature, what="signature")
        try:
            key.public_key.verify(signature, canonical_manifest_bytes(manifest))
        except InvalidSignature as exc:
            raise ManifestSignatureInvalid(f"signature does not verify under key {kid!r}") from exc

        return VerifiedManifest(manifest=manifest, issuer_kid=kid, verified_at=when)


# ── enforcement ──────────────────────────────────────────────────────────────
# Everything below answers one question: does this SQL stay inside what the
# manifest grants? Answered on the parse tree, never on the text. String
# matching loses to the first nested CTE; an AST walk does not.


#: Cap on SQL length before parsing. sqlglot raises RecursionError, not a
#: SqlglotError, on deeply nested input — the stack blows around 50 nested
#: parens — so size is refused before the parser is handed the string.
MAX_SQL_CHARS = 100_000

#: DuckDB functions that read a file. The three shapes are not interchangeable:
#: ReadParquet keeps paths in ``expressions``, ReadCSV keeps the path in ``this``
#: with options in ``expressions``, and everything else falls through to
#: ``Anonymous``. Collecting string literals from the whole call covers all of
#: them without depending on argument position.
_FILE_FUNCTIONS = frozenset(
    {
        "read_parquet",
        "parquet_scan",
        "read_csv",
        "read_csv_auto",
        "read_json",
        "read_json_auto",
        "read_json_objects",
        "read_ndjson",
        "read_ndjson_auto",
        "read_text",
        "read_blob",
        "read_xlsx",
        "glob",
        "sniff_csv",
        "st_read",
        "iceberg_scan",
        "delta_scan",
        "postgres_scan",
        "postgres_scan_pushdown",
        "mysql_scan",
        "sqlite_scan",
    }
)

#: Table functions that take SQL or a relation name as a *string* and resolve it
#: at run time. They are refused outright rather than analysed: the AST holds a
#: string literal where the relation should be, so every table check below sees
#: an empty relation set and waves the statement through while DuckDB goes on to
#: execute whatever the string said.
_DYNAMIC_SQL_FUNCTIONS = frozenset({"query", "query_table"})

#: Extensions that make a quoted identifier a file rather than a table name.
_DATA_SUFFIXES = (
    ".parquet",
    ".csv",
    ".tsv",
    ".json",
    ".ndjson",
    ".jsonl",
    ".arrow",
    ".feather",
    ".db",
    ".duckdb",
    ".sqlite",
    ".sqlite3",
    ".xlsx",
    ".txt",
    ".gz",
    ".zst",
)

#: Statement classes a *read* manifest can grant. Anything else is refused by
#: class rather than analysed: a manifest lists paths to read, so a statement
#: that writes, attaches, loads an extension or changes session state is out of
#: scope by construction, not by accident.
_ALLOWED_ROOTS = (exp.Select, exp.SetOperation, exp.Subquery)


def _looks_like_a_path(text: str) -> bool:
    """Whether a quoted identifier has to be treated as a file reference.

    sqlglot 30.11 records that an identifier was quoted but not *which* quote
    was used, so ``FROM '/data/x.parquet'`` and ``FROM "Orders"`` arrive as the
    same node shape. DuckDB resolves the first as a file. Anything that could be
    one is therefore screened as one.
    """
    lowered = text.lower()
    return (
        "/" in text
        or "\\" in text
        or "://" in text
        or "*" in text
        or "?" in text
        or lowered.endswith(_DATA_SUFFIXES)
    )


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile an allowed-path glob where ``*`` does not cross a separator.

    ``fnmatch`` is wrong here: its ``*`` matches ``/`` too, so a manifest
    granting ``/pool/a/*.parquet`` would also grant every nested directory
    beneath it. ``**`` still spans separators, because a manifest that means
    "everything below here" should be able to say so explicitly.
    """
    out: list[str] = ["^"]
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "*":
            if pattern.startswith("**", i):
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        i += 1
    out.append("$")
    return re.compile("".join(out))


def _normalize_path(raw: str) -> str | None:
    """Resolve a path to the single form the allowlist is compared against.

    Returns ``None`` for anything carrying a scheme (``s3://``, ``http://``,
    ``md:``). Those are refused before normalisation rather than after: running
    ``normpath`` over ``s3://bucket/../x`` produces something that looks local
    and could then match a local glob.
    """
    text = raw.strip()
    if not text:
        return None
    if "://" in text or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:(?![\\/])", text):
        return None
    unified = text.replace("\\", "/")
    if unified.startswith("//"):  # UNC share — not a local path
        return None
    normalized = posixpath.normpath(unified)
    # normpath cannot resolve a leading '..'; such a path escapes any root.
    if normalized.startswith("../") or normalized == "..":
        return None
    return normalized


def _path_allowed(raw: str, allowed: tuple[str, ...]) -> bool:
    normalized = _normalize_path(raw)
    if normalized is None:
        return False
    for pattern in allowed:
        rooted = _normalize_path(pattern)
        if rooted is None:
            continue
        if _glob_to_regex(rooted).match(normalized):
            return True
    return False


@dataclass(frozen=True, slots=True)
class _PathRef:
    """One place the SQL names something on disk, and how it got there."""

    origin: str
    raw: str


def _function_label(node: exp.Expression) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.this).lower()
    return "read_parquet" if isinstance(node, exp.ReadParquet) else "read_csv"


def _collect_path_refs(root: exp.Expression) -> list[_PathRef]:
    """Every string in the tree that names something outside the database.

    Three ways in, so three sources: a file-reading function anywhere in the
    query, a bare quoted path in ``FROM``, and an ``ATTACH`` target.
    """
    refs: list[_PathRef] = []

    for node in root.find_all(exp.ReadParquet, exp.ReadCSV, exp.Anonymous):
        if isinstance(node, exp.Anonymous) and str(node.this).lower() not in _FILE_FUNCTIONS:
            continue
        label = _function_label(node)
        for literal in node.find_all(exp.Literal):
            if literal.is_string:
                refs.append(_PathRef(origin=f"{label}()", raw=str(literal.this)))

    for table in root.find_all(exp.Table):
        identifier = table.this
        if isinstance(identifier, exp.Identifier) and identifier.quoted:
            text = str(identifier.this)
            if _looks_like_a_path(text):
                refs.append(_PathRef(origin="FROM", raw=text))

    return refs


def _physical_tables(root: exp.Expression) -> list[exp.Table]:
    """Table nodes that are real relations — not CTE references, not functions.

    A CTE reference parses as ``exp.Table`` like anything else. Injecting a row
    predicate into one is wrong twice over: it re-filters an already-filtered
    result, and if the CTE name shadows a real table it wraps the wrong
    relation entirely. The scope resolver is the only thing that tells them
    apart.
    """
    scope = build_scope(root)
    if scope is None:
        return [t for t in root.find_all(exp.Table) if isinstance(t.this, exp.Identifier)]
    tables: list[exp.Table] = []
    for inner in scope.traverse():
        for table in inner.tables:
            if not isinstance(table.this, exp.Identifier):
                continue  # a table function; the path checks cover it
            source = inner.sources.get(table.name)
            if source is not None and not isinstance(source, exp.Table):
                continue  # resolves to a CTE or a derived table
            tables.append(table)
    return tables


def _wrap_with_predicate(table: exp.Table, predicate: str) -> exp.Subquery:
    """Replace a table with ``(SELECT * FROM table WHERE predicate) AS alias``.

    The alias has to be *moved* rather than rebuilt: it lives in
    ``Table.args['alias']`` on the node being discarded, so a plain
    ``.replace()`` drops it and every ``o.column`` in the query stops binding.
    Where the original had no alias, one is synthesised from the bare name so
    qualified ``orders.column`` references keep resolving.
    """
    inner = table.copy()
    inner.set("alias", None)
    existing = table.args.get("alias")
    alias = existing or exp.TableAlias(
        this=exp.to_identifier(table.name, quoted=table.this.quoted)
    )
    return exp.Subquery(
        this=exp.select("*").from_(inner).where(predicate, dialect="duckdb"),
        alias=alias,
    )


def _parse_single_statement(sql: str) -> exp.Expression:
    if len(sql) > MAX_SQL_CHARS:
        raise SqlNotAnalyzable(f"SQL exceeds {MAX_SQL_CHARS} characters; refusing to parse")
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except SqlglotError as exc:
        # TokenError is a sibling of ParseError, not a subclass — an
        # unterminated string raises the former — so catch the base class.
        raise SqlNotAnalyzable(f"SQL did not parse: {exc}") from exc
    except RecursionError as exc:
        raise SqlNotAnalyzable("SQL nests too deeply to analyse") from exc

    real = [s for s in statements if s is not None]
    if not real:
        raise SqlNotAnalyzable("no statement to analyse")
    if len(real) > 1:
        raise StatementNotAllowed(
            f"{len(real)} statements submitted; a manifest authorises one query, not a script"
        )
    root = real[0]
    if isinstance(root, exp.Block):
        # 30.11 wraps stacked statements in a Block rather than raising, and
        # find_all would then only ever see the first one.
        raise StatementNotAllowed("stacked statements are not authorised")
    return root


def _refuse_unanalyzable(root: exp.Expression) -> None:
    if root.find(exp.Command) is not None:
        # exp.Command means the parser gave up and stuffed the remainder into
        # an opaque string. PREPARE, EXECUTE, LOAD and CALL all land here, and
        # their bodies are invisible to every check below.
        raise SqlNotAnalyzable(
            "statement contains syntax sqlglot cannot analyse; refusing rather than guessing"
        )


def _refuse_dynamic_sql(root: exp.Expression) -> None:
    """Refuse ``query('...')`` / ``query_table('...')``.

    These build a relation from a string at run time. There is no ``exp.Table``
    to check and no path argument to resolve, so an enforcer that only walks
    tables reports a clean statement and DuckDB then executes text this module
    never read.
    """
    for node in root.find_all(exp.Anonymous):
        name = str(node.this).lower()
        if name in _DYNAMIC_SQL_FUNCTIONS:
            raise SqlNotAnalyzable(
                f"{name}() builds a relation from a string at run time; "
                "there is nothing here to check against the manifest"
            )


def _refuse_shadowing(root: exp.Expression, predicated: set[str]) -> None:
    """Refuse binding a CTE or derived table to a predicated table's name.

    ``WITH orders AS (SELECT * FROM read_parquet(...)) SELECT * FROM orders``
    reads a path the manifest allows, then serves it under the name of a table
    the manifest filters. Predicate injection sees a CTE reference and correctly
    leaves it alone, so the rows come back unfiltered under a governed name. The
    name is the grant; rebinding it is not allowed.
    """
    for cte in root.find_all(exp.CTE):
        if cte.alias and cte.alias.lower() in predicated:
            raise PathNotAllowed(
                f"CTE {cte.alias!r} rebinds the name of a table this manifest filters"
            )
    for subquery in root.find_all(exp.Subquery):
        alias = subquery.alias
        if alias and alias.lower() in predicated:
            raise PathNotAllowed(
                f"derived table {alias!r} rebinds the name of a table this manifest filters"
            )


def _refuse_ungranted_tables(root: exp.Expression, granted: set[str]) -> None:
    """Deny by default: a relation the manifest never names is not readable.

    ``allowed_paths`` bounds what may be read out of files. Nothing bounds what
    may be read out of the database itself unless the readable tables are
    enumerated, and the only table-keyed field on a manifest is
    ``row_predicates`` — so its keys are the grant, and a table needing no row
    filter is granted with a predicate of ``TRUE``.

    Without this, ``SELECT id FROM orders UNION ALL SELECT id FROM secrets``
    passes every path check, because ``secrets`` is not a path.
    """
    for table in _physical_tables(root):
        name = table.name.lower()
        if name and name not in granted:
            raise PathNotAllowed(
                f"table {table.name!r} is not named by this manifest"
            )


def _refuse_cross_catalog(root: exp.Expression) -> None:
    """Refuse three-part names, which reach across attached catalogs.

    The manifest grants paths inside one pool. ``cat.db.tbl`` names a relation
    in a catalog the manifest never mentioned, and on a connection that already
    has a lakehouse attached that is a way out without touching ATTACH.
    """
    for table in root.find_all(exp.Table):
        if isinstance(table.this, exp.Identifier) and table.catalog:
            raise PathNotAllowed(
                f"cross-catalog reference {table.catalog}.{table.db}.{table.name} "
                "is outside this manifest"
            )


def enforce_manifest(sql: str, verified: VerifiedManifest) -> str:
    """Return SQL that cannot leave the manifest, or raise a :class:`ManifestError`.

    Takes a :class:`VerifiedManifest` rather than a ``Manifest`` so no call site
    can enforce against something it never verified.

    Order matters. Analysability is settled first, because every later check
    reads the tree; then statement class; then paths; then predicates. A
    rejection at any step is final — there is no partial enforcement.
    """
    root = _parse_single_statement(sql)
    _refuse_unanalyzable(root)
    _refuse_dynamic_sql(root)

    for attach in root.find_all(exp.Attach, exp.Detach):
        target = attach.this
        if isinstance(target, exp.Alias):
            target = target.this
        shown = target.this if isinstance(target, exp.Literal) else target.sql(dialect="duckdb")
        raise PathNotAllowed(
            f"ATTACH target {shown!r} is outside this manifest; a manifest grants "
            "paths to read, not databases to mount"
        )

    if not isinstance(root, _ALLOWED_ROOTS):
        raise StatementNotAllowed(
            f"{type(root).__name__.upper()} is not a read; this manifest authorises queries only"
        )

    allowed = verified.allowed_paths
    for ref in _collect_path_refs(root):
        if not _path_allowed(ref.raw, allowed):
            raise PathNotAllowed(f"{ref.origin} reads {ref.raw!r}, which is outside allowed_paths")

    _refuse_cross_catalog(root)

    predicates = {name.lower(): body for name, body in verified.row_predicates.items()}
    granted = set(predicates)
    _refuse_ungranted_tables(root, granted)
    _refuse_shadowing(root, granted)
    if not predicates:
        return root.sql(dialect="duckdb")

    # Materialise before mutating. Replacing a Table never detaches another
    # Table, so a lazy walk would also be sound here — but the list makes that
    # a local guarantee rather than a fact about sqlglot's traversal order.
    for table in list(_physical_tables(root)):
        predicate = predicates.get(table.name.lower())
        if predicate is None:
            continue
        try:
            wrapped = _wrap_with_predicate(table, predicate)
        except SqlglotError as exc:
            raise SqlNotAnalyzable(
                f"row predicate for {table.name!r} did not parse: {exc}"
            ) from exc
        table.replace(wrapped)

    return root.sql(dialect="duckdb")
