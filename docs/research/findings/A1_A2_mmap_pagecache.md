# A1 + A2 — Virtual Address Space, `mmap`, Page Cache, Eviction & Huge Pages

**Date:** 2026-07-18
**Gate:** Unblocks the **raw-mmap memory tier** (`netie.brain.stores.rawknn` residency control). P0 bundle item #1.
**Hardware target:** RTX 4070 12 GB · i5-13490F (16 threads) · 32 GB RAM · Windows 11 + Docker Desktop/WSL2 · Python 3.10. Personal target = this laptop class; business target = same host or $5 VPS / NAS.

> Scope: how `mmap` maps a file into virtual address space, lazy page-in, page-cache residency vs virtual reservation vs resident memory, forced prefetch/eviction, huge pages, residency probing, and the Windows equivalents. Precise API names only — no invented flags.

---

## 0. Mental model (the three memory numbers that matter)

When you `mmap` a `vectors.bin`, three separate quantities exist. Conflating them is the root of the "why is RAM still high" confusion:

1. **Virtual reservation (VSZ / commit / "address space").** `mmap(len)` (or `MapViewOfFile`) reserves a contiguous `len`-byte window in the process's virtual address space. This is **free** in the sense that it consumes *no physical RAM* — it is just page-table bookkeeping. On a 64-bit process you can reserve tens of GB of a file that lives entirely on disk.
2. **OS page cache.** When you actually *touch* a byte, the OS faults the enclosing page (4 KiB) in from disk into the **page cache** (a global, kernel-owned cache shared across processes). The page is now in physical RAM.
3. **Resident set (RSS / working set).** The subset of your mapping's pages currently mapped into *your* process and counted against *your* footprint.

Key consequence for us: **reading one 1536-dim float32 vector (6144 bytes) from the middle of a 40 GB file touches 2 pages (~8 KiB), not 40 GB.** The OS lazily pages in only what you read. Eviction = pushing pages 2/3 back down so idle RAM approaches 0. That is the entire raw-mmap KNN thesis, and it is sound.

---

## A1 — Virtual address space & `mmap`

### How `mmap` maps a file into VAS; lazy page-in
- `mmap()` establishes a mapping between a range of the process's virtual address space and a file (or anonymous memory). It does **not** read the file eagerly. Pages are **demand-paged**: the first access to a page triggers a **minor/major page fault**, and the kernel reads that page (with some read-ahead) from the backing file into the page cache, then maps it into the process. (`man mmap`.) [1]
- POSIX guarantees the mapping length may exceed the current object size, but **reading past the current end of the file raises `SIGBUS`** (see growing-file risks below). [3][1]

### OS page cache vs virtual reservation vs resident
- **Virtual reservation** = address space only; no RAM. **Page cache** = physical RAM holding file pages, globally managed and reclaimable by the kernel under pressure. **Resident (RSS)** = pages currently charged to your process.
- A mapped **read hits RAM (fast)** if the page is already in the page cache (from a prior touch, from another process, or from OS read-ahead). It **hits disk (slow, major fault)** if the page has been evicted or never loaded. `MADV_WILLNEED` warms the cache; `MADV_DONTNEED`/`MADV_COLD`/`MADV_PAGEOUT` cool it.

### Reading a row by byte offset without loading the whole file
- With a **fixed-width row layout**, `offset = row_id * dim * bytes_per_elem` (e.g. `dim=1536`, float32 → `row_id * 6144`). You index the mapping as a byte array / NumPy view at that offset; only the touched pages fault in.
- In Python: wrap the `mmap` in a zero-copy NumPy view — `np.frombuffer(mm, dtype=np.float32, count=dim, offset=row_id*dim*4)`. No `read()`, no copy of the whole file. `numpy.memmap` does the same with an ndarray façade.
- **Alignment reality:** vectors are not page-aligned, so one row can straddle a 4 KiB page boundary → touches 2 pages. Fine for correctness; matters only for eviction granularity (you can only evict whole pages).

### (a) Can we `mmap` a growing append-only `vectors.bin` safely? How?
**Yes, with an explicit grow-then-remap discipline. Do not rely on the mapping auto-extending.**

- A mapping has a **fixed length**. Appending bytes to the file does **not** enlarge an existing mapping, and touching addresses beyond the mapped/EOF region raises **`SIGBUS`** (POSIX/Linux) or an access violation (Windows). [3][1]
- Safe append pattern (Linux):
  1. Grow the file on disk first — `ftruncate(fd, new_size)` or, preferred to avoid fragmentation, `fallocate(fd, 0, 0, new_size)` (glibc example uses `ftruncate` + remap). [5][2]
  2. Re-establish the mapping over the new size: either `munmap()` + `mmap()`, or on Linux use **`mremap(old, old_len, new_len, MREMAP_MAYMOVE)`** to resize in place (avoids re-faulting untouched pages). `mremap` is **Linux-only**. [2]
  3. Because remap **may return a new base address**, never cache raw pointers across a grow; recompute row pointers as `base + offset`.
- **Preferred production pattern — reserve-once, commit-as-you-grow:** reserve a large VA window up front with `mmap(HUGE, PROT_NONE, MAP_PRIVATE|MAP_ANONYMOUS)`, then `ftruncate`/`fallocate` the file and `mmap(..., MAP_FIXED)` the newly-grown region into the reserved window, or flip protections with `mprotect`. This guarantees the base address never moves, so appends never invalidate existing row pointers. [2]
- Use **`MAP_SHARED`** (writes go to the file, visible to other mappers; required if a writer process appends and readers see it) — **not `MAP_PRIVATE`** (copy-on-write, changes not persisted). For our append-only vectors, a single writer with `MAP_SHARED` + readers `mmap` read-only (`PROT_READ`) is the clean model. [3]
- **Windows caveat (Python `mmap`):** `mmap.resize()` fails with `OSError` if any *other* map is held against the same named file; a Windows section object **cannot grow** — growing requires closing the section and creating a new one. So on Windows the "reserve big + remap the grown region" pattern, or "close/reopen the mapping after each grow," is mandatory. [7][8]

### (b) Force-evict pages after a query to free RAM
**Linux:** `madvise(addr, len, MADV_DONTNEED)`.
- After a successful `MADV_DONTNEED` on a **file-backed shared mapping**, subsequent access **repopulates from the up-to-date file contents** (data not lost), and **RSS is reduced immediately**. The kernel may delay actually freeing the physical pages, but your process's resident footprint drops right away. [1][3]
- `addr` must be **page-aligned**; `len` is rounded up to whole pages. So evict on page boundaries — you cannot evict a sub-page slice of a single vector. [1]
- Alternatives with different semantics (all sourced from `madvise(2)`):
  - **`MADV_COLD`** (Linux 5.4+): non-destructive; marks pages inactive so they are reclaimed *first under pressure*, but leaves them resident until then. Good for "probably done, reclaim if needed." [1][4]
  - **`MADV_PAGEOUT`** (Linux 5.4+): reclaim *immediately*; file-backed dirty pages are written back, then pages are evicted. Closest to "force it out now." [1]
  - **`MADV_FREE`** (Linux 4.5+): **private anonymous pages only** — **NOT valid on our `MAP_SHARED` file mapping** (returns `EINVAL`). Do not use for `vectors.bin`. [1]
- Constraint: `MADV_DONTNEED`/`MADV_COLD`/`MADV_PAGEOUT` **cannot be applied to locked pages, Huge TLB pages, or `VM_PFNMAP` pages** (`EINVAL`). [1]

**Windows:** `VirtualUnlock(addr, len)` on the mapped range.
- Despite the name, calling `VirtualUnlock` on a range (without a prior `VirtualLock`) **removes those pages from the process working set** while leaving them committed — the pages stay available in the system cache but stop counting against your footprint. It commonly returns `FALSE` with `ERROR_NOT_LOCKED (0x9E)` yet still trims the working set. This is the documented community-verified evict primitive for MMF ranges. [4-win][5-win]
- Coarser: `SetProcessWorkingSetSize(hProc, (SIZE_T)-1, (SIZE_T)-1)` / `SetProcessWorkingSetSizeEx` and `EmptyWorkingSet` trim the whole process working set (blunt instrument). [4b-win][5b-win]

### (c) Windows equivalents of the mmap/madvise surface
| Purpose | Linux | Windows (Win32) |
|---|---|---|
| Create mapping object | (implicit in `mmap`) | `CreateFileMapping` / `CreateFileMappingA/W` (or `CreateFileMapping2`) [1-win][2-win] |
| Map view into VAS | `mmap(fd, ...)` | `MapViewOfFile` / `MapViewOfFileEx` (fixed base) [1-win][3-win] |
| Unmap | `munmap` | `UnmapViewOfFile` + `CloseHandle` on the section [2-win] |
| Flush dirty pages to disk | `msync` | `FlushViewOfFile` [3-win] |
| Prefetch / warm | `madvise(MADV_WILLNEED)` / `readahead` | `PrefetchVirtualMemory` (+ `WIN32_MEMORY_RANGE_ENTRY`), Win8+ [6-win][3b-win] |
| Evict / drop residency | `madvise(MADV_DONTNEED / COLD / PAGEOUT)` | `VirtualUnlock(range)` (working-set trim); `EmptyWorkingSet`; `SetProcessWorkingSetSizeEx` [4-win][5-win] |
| Lock resident (pin) | `mlock` / `mlock2` | `VirtualLock` [5-win] |
| Residency probe | `mincore` | `QueryWorkingSetEx` (`PSAPI_WORKING_SET_EX_INFORMATION`) |

**Windows-specific gotchas:**
- **Allocation granularity, not page size, governs offsets.** `MapViewOfFile`'s file offset must be a multiple of the **system allocation granularity (64 KiB on x64)**, obtained from `GetSystemInfo`/`SYSTEM_INFO.dwAllocationGranularity`. (Linux `mmap` offset only needs page (4 KiB) alignment.) [3-win]
- Prefetched memory is **not added to the working set** until touched; `PrefetchVirtualMemory` is purely advisory and may be ignored. There is **no completion event** for when pages become resident. [6-win]

---

## A2 — Page cache, eviction, `madvise`, huge pages, residency

### `MADV_WILLNEED` (prefetch) vs `DONTNEED` / `COLD` / `PAGEOUT` (evict)
| Advice | Effect | Destructive? | Valid on our `MAP_SHARED` file map? |
|---|---|---|---|
| `MADV_WILLNEED` | Kernel schedules read-ahead so future access is a cache hit (warm the page cache). | No | Yes [1] |
| `MADV_SEQUENTIAL` / `MADV_RANDOM` | Tune read-ahead aggressiveness. `RANDOM` disables read-ahead (good for scattered KNN row reads); `SEQUENTIAL` maximizes it (good for full scans). | No | Yes [1] |
| `MADV_DONTNEED` | Drop pages now; **RSS drops immediately**; refault repopulates from file. | No (shared file map: reloads from file) | Yes [1][3] |
| `MADV_COLD` (5.4+) | Mark inactive → reclaimed first under pressure; stays resident until then. | No | Yes [1][4] |
| `MADV_PAGEOUT` (5.4+) | Reclaim immediately (write back dirty, then evict). | No (contents preserved on disk) | Yes [1] |
| `MADV_FREE` (4.5+) | Lazy free. | Yes (stale data lost) | **No** — private anon only [1] |

**Prefetch-then-evict cycle for a query (Linux, the rawknn hot path):**
1. `madvise(rows_region, MADV_WILLNEED)` (or `PrefetchVirtualMemory` on Windows) to warm the candidate vectors.
2. Run brute-force dot/cosine over the now-resident rows.
3. `madvise(rows_region, MADV_DONTNEED)` (or `VirtualUnlock` on Windows) to return idle RAM ≈ 0.

### Huge pages (2 MiB) for large contiguous vector arrays
- **Transparent Huge Pages (THP):** `madvise(addr, len, MADV_HUGEPAGE)` (Linux 2.6.38+) hints the kernel to back the region with 2 MiB pages, reducing TLB misses on large scans. Most distro kernels enable THP by default, so `MADV_HUGEPAGE` is *usually unnecessary* and mainly for embedded/opt-in configs. Requires `CONFIG_TRANSPARENT_HUGEPAGE`; **file/shmem THP needs `CONFIG_READ_ONLY_THP_FOR_FS`**, so for a file-backed `MAP_SHARED` mapping THP support is limited/read-only. [1]
- **Trade-off for us:** huge pages help sequential full-index scans (fewer TLB misses) but **hurt fine-grained eviction** — `MADV_DONTNEED`/`COLD`/`PAGEOUT` **cannot target Huge TLB / locked pages** (`EINVAL`), and evicting is all-or-nothing at 2 MiB granularity. For a "keep idle RAM near 0" design that evicts small candidate sets, **default to 4 KiB pages**; reserve huge pages for a pinned, always-hot index only. [1]
- Explicit HugeTLB (`MAP_HUGETLB`, `hugetlbfs`) is a separate, heavier mechanism (pre-reserved pool via `/sys/kernel/mm/hugepages`), generally not worth it for our evictable tier. [huge]
- **Windows** has Large Pages (`MEM_LARGE_PAGES` via `VirtualAlloc`, `GetLargePageMinimum`), but they require the *SeLockMemoryPrivilege* and are **not applicable to file-backed section views** — not a path for our MMF tier.

### Residency measurement (`mincore` / `QueryWorkingSetEx`)
- **Linux `mincore(addr, len, vec)`:** returns a byte vector where the **LSB of each byte = 1 if that page is resident** in RAM. `addr` must be page-aligned; `vec` must be `(len + PAGE_SIZE-1)/PAGE_SIZE` bytes; `PAGE_SIZE` from `sysconf(_SC_PAGESIZE)`. Result is a **snapshot** — pages can come/go immediately unless locked. Use it to verify eviction actually happened and to measure warm-cache hit rate in `bench/tiering.py`. [mincore1][mincore2]
- `mincore` **does not report page size** on Linux (only bit 0 = resident); a THP-backed region reports all its 4 KiB sub-pages as resident. To confirm THP backing you must parse `/proc/self/pagemap` + `/proc/kpageflags` (KPF_THP). [mincore-thp]
- **Windows:** `QueryWorkingSetEx` with `PSAPI_WORKING_SET_EX_INFORMATION` reports whether each address is `Valid` (resident) — the residency probe equivalent.

### Working-set trimming (Windows)
- The OS transparently promotes committed-but-paged-out MMF pages into the working set on access, and trims them under low memory. You can steer this:
  - `SetProcessWorkingSetSize` / `SetProcessWorkingSetSizeEx(hProc, min, max, flags)` — set min/max resident bounds. By default not hard limits unless `QUOTA_LIMITS_HARDWS_MIN_ENABLE` / `QUOTA_LIMITS_HARDWS_MAX_ENABLE` flags are set. Passing `(SIZE_T)-1, (SIZE_T)-1` trims aggressively. [4b-win][5b-win]
  - `EmptyWorkingSet(hProc)` — flush the whole working set (blunt).
  - `VirtualUnlock(range)` — surgical per-range trim (preferred; see A1(b)). [4-win]

---

## Python `mmap` module notes (Windows + Linux differences)

Source: Python `mmap` docs (3.10/3.11/3.14). [py-mmap]

- **Constructor:** `mmap.mmap(fileno, length, tagname=None, access=ACCESS_DEFAULT, offset=0, *, trackfd=True)`.
  - **`length=0`** → **Unix:** map the whole file at call time. **Windows:** map current file size, but **raises if the file is empty** (Windows cannot create an empty mapping). Always create `vectors.bin` with ≥1 page before mapping on Windows. [py-mmap]
  - **Windows auto-extends:** if `length` > current file size, Windows **extends the file** to `length`. Unix does not. [py-mmap]
  - `offset` must be a multiple of `ALLOCATIONGRANULARITY` (`mmap.ALLOCATIONGRANULARITY`, 64 KiB on Windows; page size on Unix).
- **Access modes:** `ACCESS_READ` (assignment → `TypeError`), `ACCESS_WRITE` (write-through to file; Windows default if unspecified), `ACCESS_COPY` (copy-on-write, not persisted), `ACCESS_DEFAULT`. For read-only KNN readers use `ACCESS_READ`; for the single appender use `ACCESS_WRITE`. [py-mmap]
- **`resize(newsize)`:** resizes map *and* file. `TypeError` if map is `ACCESS_READ`/`ACCESS_COPY`; `ValueError` if `trackfd=False`. **On Windows, `resize()` raises `OSError` if any other map is held against the same named file** (Python ≥3.11 correctly fails instead of silently mis-sizing). This is the core reason growing MMFs on Windows must close/reopen or use a reserve-big pattern. [py-mmap][py-issue]
- **`madvise(option[, start[, length]])`:** **Unix only, since Python 3.8.** Exposes `mmap.MADV_WILLNEED`, `MADV_DONTNEED`, `MADV_COLD` (3.10+, Linux 5.4+), `MADV_PAGEOUT`, `MADV_RANDOM`, `MADV_SEQUENTIAL`, `MADV_HUGEPAGE`, etc. **No `madvise` on Windows.** [py-mmap]
- **`flush([offset, size])`** = `msync`/`FlushViewOfFile`. **No `mincore`, no `PrefetchVirtualMemory`, no `VirtualUnlock` in the stdlib** — those need `ctypes` (Windows) or a small `ctypes`/`cffi` binding to `libc.mincore` (Linux).
- **Cross-platform residency control therefore requires a thin native shim:** stdlib gives you map/flush/resize + Unix `madvise`; everything else (Windows prefetch/evict, `mincore`) is `ctypes`.

---

## Risks & gotchas for append-only growing files

1. **`SIGBUS` past EOF.** Reading any mapped address beyond the current file length crashes with `SIGBUS` (Linux) / access violation (Windows). Always `ftruncate`/`fallocate` **before** exposing new rows to readers, and gate reads on a metadata `row_count` that is only advanced after the byte range is durable. [3][1]
2. **Mapping does not auto-grow.** Appending to the file never extends an existing mapping — you must remap (`mremap` Linux, close/reopen Windows). [2][7]
3. **Base address can move on remap** (`munmap`+`mmap`, or `mremap` without a reserved window) → invalidates cached pointers. Use the **reserve-large-VA + `MAP_FIXED`/`mprotect`** pattern to pin the base, or always recompute `base+offset`. [2]
4. **Windows `resize()` fails with concurrent maps** (`OSError`) and sections can't grow — the single biggest platform divergence. Design the appender to hold the *only* writable map, or use reserve-big. [7][8]
5. **`MADV_FREE` is a trap for shared file maps** — it's private-anon only and would `EINVAL` (or if misapplied to anon, lose data). Use `MADV_DONTNEED`/`COLD`/`PAGEOUT`. [1]
6. **Eviction is page-granular (4 KiB).** You cannot evict a sub-page vector; a vector straddling a page boundary pins 2 pages. Consider **page-aligning row groups** (pad rows to a 4 KiB boundary block) if precise eviction of individual candidate sets matters.
7. **Huge pages block fine-grained eviction** and can't be `DONTNEED`/`COLD`/`PAGEOUT`'d — keep the evictable tier on 4 KiB pages. [1]
8. **Fragmentation from sparse `ftruncate` holes** — prefer `fallocate`/writing zeros when pre-extending (FreeBSD/glibc guidance). [freebsd][5]
9. **Torn reads across processes.** With a separate writer, a reader can observe a half-written vector unless writes are page/row-atomic and `row_count` is published after `flush`/`msync`. Publish-after-durable ordering is mandatory.
10. **`mincore`/`QueryWorkingSetEx` are snapshots** — never assume a page stays resident after the probe unless it's `mlock`/`VirtualLock`-pinned. [mincore1]

---

## Concrete API surface → `netie.brain.stores.rawknn` residency control

Recommended abstraction (platform-dispatched behind one interface):

```python
class Residency(Protocol):
    def prefetch(self, offset: int, length: int) -> None: ...   # warm page cache
    def evict(self, offset: int, length: int) -> None: ...      # drop to disk, free RAM
    def is_resident(self, offset: int, length: int) -> float: ...# 0..1 fraction resident
    def pin(self, offset: int, length: int) -> None: ...         # optional: lock hot index
    def flush(self, offset: int = 0, length: int = 0) -> None: ...
```

| Operation | Linux impl | Windows impl |
|---|---|---|
| `prefetch` | `mm.madvise(MADV_WILLNEED, start, len)` (stdlib) | `PrefetchVirtualMemory` via `ctypes` (Win8+); fall back to touch-loop |
| `evict` | `mm.madvise(MADV_DONTNEED, start, len)` (idle→0); `MADV_COLD` for soft | `VirtualUnlock(base+off, len)` via `ctypes` (ignore `ERROR_NOT_LOCKED`) |
| `is_resident` | `ctypes` → `libc.mincore(addr, len, vec)`; count LSBs | `QueryWorkingSetEx` → count `Valid` bits |
| `pin` (hot index) | `mm.madvise(MADV_WILLNEED)` + optional `mlock` | `VirtualLock` |
| `flush` | `mm.flush()` (`msync`) | `mm.flush()` (`FlushViewOfFile`) |
| grow file | `os.ftruncate`/`fallocate` then `mremap`/remap | pre-extend + close/reopen mapping (reserve-big) |

- Read path: read-only `mmap(ACCESS_READ)` + `np.frombuffer(mm, np.float32, count=dim, offset=row_id*dim*4)` zero-copy view.
- Default page mode: **4 KiB** (evictable). Only a pinned always-hot index may opt into `MADV_HUGEPAGE`.
- Set `MADV_RANDOM` on the vectors mapping (KNN reads are scattered) to suppress wasteful read-ahead; use `MADV_SEQUENTIAL`/`MADV_WILLNEED` only for full-index rebuild scans.

---

## Back to builder

**(a) Can we mmap a growing append-only `vectors.bin` safely?**
Yes. Use **`MAP_SHARED`**, one writer + read-only (`PROT_READ`/`ACCESS_READ`) readers. The mapping is fixed-length and does **not** auto-grow, so on append: `ftruncate`/`fallocate` the file first, then **`mremap(MREMAP_MAYMOVE)`** (Linux) or **close/reopen the section** (Windows — `resize()` throws `OSError` if other maps exist and sections can't grow). Best pattern: **reserve a large VA window once (`PROT_NONE`) and map grown regions with `MAP_FIXED`/`mprotect`** so the base never moves and row pointers never invalidate. Guard against `SIGBUS`: only expose rows after the byte range is durable and `row_count` is published.

**(b) Force-evict pages after a query to free RAM?**
**Linux:** `madvise(addr, len, MADV_DONTNEED)` — RSS drops immediately, refault reloads from the file (non-destructive for shared file maps). Use `MADV_COLD` (reclaim-under-pressure) or `MADV_PAGEOUT` (reclaim-now) for softer/harder variants. **Never `MADV_FREE`** (anon-only). `addr` must be page-aligned. Python exposes this as `mmap.madvise(...)` (Unix, 3.8+).
**Windows:** `VirtualUnlock(range)` trims the range from the working set (leaves it committed/cached) — the documented evict primitive; expect `ERROR_NOT_LOCKED` return, memory still frees. Coarser: `EmptyWorkingSet` / `SetProcessWorkingSetSizeEx`.

**(c) Windows equivalents of madvise/mmap?**
`CreateFileMapping` + `MapViewOfFile`/`MapViewOfFileEx` (map) · `UnmapViewOfFile` + `CloseHandle` (unmap) · `FlushViewOfFile` (msync) · **`PrefetchVirtualMemory` + `WIN32_MEMORY_RANGE_ENTRY`** (= `MADV_WILLNEED`, Win8+, advisory, not added to working set until touched) · **`VirtualUnlock`** (= `MADV_DONTNEED` evict) · `VirtualLock` (= `mlock`) · `QueryWorkingSetEx` (= `mincore`) · `SetProcessWorkingSetSizeEx`/`EmptyWorkingSet` (working-set trimming). **Offsets align to 64 KiB allocation granularity** (`GetSystemInfo`), not 4 KiB. None of prefetch/evict/mincore are in Python stdlib → thin `ctypes` shim required; stdlib `mmap` covers map/flush/resize + Unix `madvise` only.

**Recommended rawknn API surface:** `Residency` protocol (`prefetch` / `evict` / `is_resident` / `pin` / `flush`) with a Linux (`mmap.madvise` + `ctypes libc.mincore`) backend and a Windows (`ctypes` `PrefetchVirtualMemory` / `VirtualUnlock` / `QueryWorkingSetEx`) backend. Default 4 KiB evictable pages, `MADV_RANDOM` on the vectors map, zero-copy `np.frombuffer` row reads at `row_id*dim*4`. This directly satisfies the "evict hot vectors → idle RAM ≈ 0" design.

---

## Primary sources
- `madvise(2)` — https://man7.org/linux/man-pages/man2/madvise.2.html [1]
- `madvise(2)` (die.net) — https://linux.die.net/man/2/madvise
- `MADV_COLD`/`MADV_PAGEOUT` intro (Linux 5.4) — https://kernelnewbies.org/Linux_5.4 [4]
- `MADV_COLD` commit — https://github.com/torvalds/linux (mm: introduce MADV_COLD)
- `mmap(2)` — https://man7.org/linux/man-pages/man2/mmap.2.html [1]
- POSIX `mmap` (SIGBUS past EOF) — https://pubs.opengroup.org/onlinepubs/9799919799.2024edition/functions/mmap.html [3]
- `mincore(2)` — https://man7.org/linux/man-pages/man2/mincore.2.html [mincore1]
- glibc File Size / `ftruncate`+`mmap` remap example — https://sourceware.org/glibc/manual/2.39/html_node/File-Size.html [5]
- Portably extend an mmap'd file (reserve-big / mremap) — https://stackoverflow.com/questions/15684771/how-to-portably-extend-a-file-accessed-using-mmap [2]
- FreeBSD `mmap` (pre-allocate to avoid fragmentation) — https://man.freebsd.org/mmap [freebsd]
- THP / huge page discovery — https://unix.stackexchange.com/questions/364523/discover-huge-page-support-on-posix-or-linux [huge]
- Python `mmap` module — https://docs.python.org/3/library/mmap.html [py-mmap]
- Python `mmap.resize()` Windows issues — https://github.com/python/cpython/issues/85092 [py-issue]
- `CreateFileMapping` — https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-createfilemappinga [2-win]
- `MapViewOfFile` — https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-mapviewoffile [1-win]
- Creating a File View (`MapViewOfFileEx`, allocation granularity, `FlushViewOfFile`) — https://learn.microsoft.com/en-us/windows/win32/memory/creating-a-file-view [3-win]
- `PrefetchVirtualMemory` — https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-prefetchvirtualmemory [6-win]
- `WIN32_MEMORY_RANGE_ENTRY` — https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/ns-memoryapi-win32_memory_range_entry [3b-win]
- `SetProcessWorkingSetSize` — https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-setprocessworkingsetsize [4b-win]
- `SetProcessWorkingSetSizeEx` — https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-setprocessworkingsetsizeex [5b-win]
- `VirtualUnlock` to trim working set for MMF ranges — https://stackoverflow.com/questions/1880714/createfilemapping-mapviewoffile-how-to-avoid-holding-up-the-system-memory [4-win]
- Windows MMF IO (VirtualUnlock evict, PrefetchVirtualMemory hint) — https://jeremyong.com/winapi/io/2024/11/03/windows-memory-mapped-file-io/ [5-win]
- Preload MMF chunk with PrefetchVirtualMemory — https://devblogs.microsoft.com/oldnewthing/20160225-00/?p=93091
