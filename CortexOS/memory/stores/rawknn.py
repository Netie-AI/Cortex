"""rawknn — persistent mmap brute-force KNN store (M1 slice).

The user-specified layout from the D6 finding
(docs/research/findings/D1_D5_D6_vector_memory.md):

    {root}/
      manifest.json   # {"dim": D, "dtype": "float32", "count": N, "version": 1}
      vectors.bin     # fixed-width rows, row offset = row_index * D * 4  (mmap'd)
      norms.bin       # float32 L2 norm per row (sidecar → single-pass cosine)
      meta.sqlite     # WAL; id -> row_index, text, meta, scope/collection/role/tier

Query path (D5 finding): SQL prefilter on scope/collection first (big win), then
exact cosine over a NumPy memmap — brute force is the right call at <=100k
vectors. An unfiltered query does a no-copy full scan (`mm @ q` straight over
the map); filtered queries gather only candidate rows. The memmap is re-created
from the manifest per query, which sidesteps mremap/resize pain on Windows
(A1 finding: mappings don't auto-grow; close/re-map after growth).

vectors.bin is opened "r+b" — NEVER append mode: O_APPEND redirects every write
to EOF and silently breaks in-place row overwrites.

Residency control (A2 finding) is best-effort and failure-silent:
  evict()    Linux madvise(MADV_DONTNEED) / Windows VirtualUnlock
  prefetch() Linux madvise(MADV_WILLNEED) / Windows read-through

Import as ``netie.memory.stores.rawknn``.
"""
from __future__ import annotations

import ctypes
import json
import sqlite3
import sys
import time
from collections.abc import Iterable
from pathlib import Path

from netie.memory.store import Hit, MemoryRecord, Scope

_MANIFEST = "manifest.json"
_VECTORS = "vectors.bin"
_NORMS = "norms.bin"
_META = "meta.sqlite"


def _np():
    try:
        import numpy
        return numpy
    except ImportError as exc:  # pragma: no cover
        raise ImportError("rawknn needs numpy (core Cortex dependency)") from exc


class RawKnnStore:
    """VectorStore-protocol impl over the raw sequential-file layout."""

    def __init__(self, root: str | Path, dim: int | None = None) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._manifest = self._load_manifest()
        if dim is not None:
            if self._manifest["dim"] and self._manifest["dim"] != dim:
                raise ValueError(
                    f"dim mismatch: store has {self._manifest['dim']}, got {dim}")
            self._manifest["dim"] = dim
        self._db = sqlite3.connect(self.root / _META)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS records(
                 id TEXT PRIMARY KEY, row INTEGER NOT NULL, text TEXT NOT NULL,
                 meta TEXT NOT NULL DEFAULT '{}', scope TEXT NOT NULL DEFAULT 'personal',
                 collection TEXT NOT NULL DEFAULT 'default', role TEXT,
                 tier TEXT NOT NULL DEFAULT 'warm', created_at REAL NOT NULL)""")
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS ix_scope_coll ON records(scope, collection)")
        self._db.commit()

    # ── manifest / file plumbing ──────────────────────────────────────────
    def _load_manifest(self) -> dict:
        p = self.root / _MANIFEST
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
        return {"dim": 0, "dtype": "float32", "count": 0, "version": 1}

    def _save_manifest(self) -> None:
        (self.root / _MANIFEST).write_text(
            json.dumps(self._manifest, indent=2), encoding="utf-8")

    def _row_bytes(self) -> int:
        return int(self._manifest["dim"]) * 4  # float32

    def _open_rw(self, name: str):
        p = self.root / name
        if not p.exists():
            p.touch()
        return open(p, "r+b")

    def _mmap(self, name: str, shape):
        """Fresh read-only memmap sized from the manifest (A1: re-map after growth)."""
        np = _np()
        return np.memmap(self.root / name, dtype=np.float32, mode="r", shape=shape)

    # ── VectorStore protocol ──────────────────────────────────────────────
    def upsert(self, records: Iterable[MemoryRecord]) -> int:
        np = _np()
        recs = [r for r in records if r.vector]
        if not recs:
            return 0
        if not self._manifest["dim"]:
            self._manifest["dim"] = len(recs[0].vector or [])
        d = int(self._manifest["dim"])
        n_written = 0
        with self._open_rw(_VECTORS) as fv, self._open_rw(_NORMS) as fn:
            for r in recs:
                vec = np.asarray(r.vector, dtype=np.float32)
                if vec.shape != (d,):
                    raise ValueError(f"vector dim {vec.shape} != store dim ({d},)")
                cur = self._db.execute(
                    "SELECT row FROM records WHERE id=?", (r.id,)).fetchone()
                row = int(cur[0]) if cur else int(self._manifest["count"])
                fv.seek(row * self._row_bytes())
                fv.write(vec.tobytes())
                fn.seek(row * 4)
                fn.write(np.float32(np.linalg.norm(vec)).tobytes())
                if not cur:
                    self._manifest["count"] = row + 1
                self._db.execute(
                    """INSERT INTO records(id,row,text,meta,scope,collection,role,tier,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET row=excluded.row, text=excluded.text,
                         meta=excluded.meta, scope=excluded.scope, collection=excluded.collection,
                         role=excluded.role, tier=excluded.tier""",
                    (r.id, row, r.text, json.dumps(r.meta or {}), r.scope,
                     r.collection, r.role, r.tier, r.created_at or time.time()))
                n_written += 1
        self._db.commit()
        self._save_manifest()
        return n_written

    def query(self, vector: list[float], *, k: int = 5,
              scope: Scope | None = None, collection: str | None = None) -> list[Hit]:
        np = _np()
        n, d = int(self._manifest["count"]), int(self._manifest["dim"])
        if n == 0 or d == 0:
            return []
        mm = self._mmap(_VECTORS, (n, d))
        nrm = self._mmap(_NORMS, (n,))
        q = np.asarray(vector, dtype=np.float32)
        qn = float(np.linalg.norm(q)) or 1.0
        if not scope and not collection:
            # Unfiltered fast path: score every row straight off the map, then
            # SELECT only the winning k rows (avoids a full-table SQL fetch).
            denom = np.asarray(nrm) * qn
            denom[denom == 0] = 1.0
            scores_by_row = (mm @ q) / denom
            top_rows = np.argsort(-scores_by_row)[:k]
            marks = ",".join("?" * len(top_rows))
            got = {int(r): (i, t, m) for i, r, t, m in self._db.execute(
                f"SELECT id,row,text,meta FROM records WHERE row IN ({marks})",
                [int(r) for r in top_rows]).fetchall()}
            return [
                Hit(got[int(r)][0], float(scores_by_row[r]), got[int(r)][1],
                    json.loads(got[int(r)][2] or "{}"))
                for r in top_rows if int(r) in got
            ]
        # Filtered path — D6: SQL prefilter before touching vector bytes.
        cond, args = [], []
        if scope:
            cond.append("scope=?")
            args.append(scope)
        if collection:
            cond.append("collection=?")
            args.append(collection)
        cands = self._db.execute(
            "SELECT id,row,text,meta FROM records WHERE " + " AND ".join(cond), args
        ).fetchall()
        if not cands:
            return []
        rows = np.array([c[1] for c in cands], dtype=np.int64)
        mat = np.asarray(mm[rows])                  # gather only candidate rows
        denom = np.asarray(nrm[rows]) * qn
        denom[denom == 0] = 1.0
        scores = (mat @ q) / denom
        top = np.argsort(-scores)[:k]
        return [
            Hit(cands[i][0], float(scores[i]), cands[i][2], json.loads(cands[i][3] or "{}"))
            for i in top
        ]

    def evict(self, *, policy: str = "cold", max_keep: int | None = None) -> int:
        """Residency-only eviction: drop the vector file's pages from RAM (A2).

        Record deletion/compaction is a later slice; the bin file is append-only.
        """
        return 1 if _drop_file_cache(self.root / _VECTORS) else 0

    def prefetch(self) -> bool:
        return _prefetch_file(self.root / _VECTORS)

    def stats(self) -> dict:
        db_count = self._db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        vec_file = self.root / _VECTORS
        return {
            "count": int(db_count),
            "backend": "rawknn_mmap",
            "dim": int(self._manifest["dim"]),
            "bin_mb": round(vec_file.stat().st_size / 1e6, 2) if vec_file.is_file() else 0.0,
            "root": str(self.root),
        }

    def close(self) -> None:
        self._db.close()


# ── best-effort residency control (A2 finding; failure-silent) ─────────────
def _drop_file_cache(path: Path) -> bool:
    try:
        if sys.platform == "win32":
            # Map the file and VirtualUnlock the view — trims those pages from
            # the working set (Windows analog of MADV_DONTNEED per A2 finding).
            import mmap as _mmap
            with open(path, "rb") as f, _mmap.mmap(f.fileno(), 0, access=_mmap.ACCESS_READ) as m:
                buf = (ctypes.c_char * len(m)).from_buffer(m)
                try:
                    ctypes.windll.kernel32.VirtualUnlock(
                        ctypes.addressof(buf), ctypes.c_size_t(len(m)))
                finally:
                    del buf  # release buffer export before closing the mmap
            return True
        else:
            import mmap as _mmap
            with open(path, "rb") as f:
                m = _mmap.mmap(f.fileno(), 0, prot=_mmap.PROT_READ)
                try:
                    m.madvise(_mmap.MADV_DONTNEED)
                finally:
                    m.close()
            return True
    except Exception:
        return False


def _prefetch_file(path: Path) -> bool:
    try:
        if sys.platform == "win32":
            # PrefetchVirtualMemory needs a mapped view; cheap best-effort read-through.
            with open(path, "rb", buffering=1024 * 1024) as f:
                while f.read(8 * 1024 * 1024):
                    pass
            return True
        else:
            import mmap as _mmap
            with open(path, "rb") as f:
                m = _mmap.mmap(f.fileno(), 0, prot=_mmap.PROT_READ)
                try:
                    m.madvise(_mmap.MADV_WILLNEED)
                finally:
                    m.close()
            return True
    except Exception:
        return False
