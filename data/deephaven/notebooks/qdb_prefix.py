"""
Stable row positions for a QuestDB table read through Deephaven's TableDataService.

TableDataService addresses rows by position: it is told a size and later asks for rows
[offset, offset + n) of each column, separately and at different moments, and may re-read a
page it dropped from its (soft-referenced) cache at any time. The size may only grow. So the
row at position p must be the same row forever.

A QuestDB table ordered by its designated timestamp does not give that: a row that arrives
late with an older timestamp is inserted in the middle and every later position shifts. The
classes here expose only a settled *prefix* of the table instead:

    rows with  start <= timestamp < watermark,  ordered by (timestamp, tiebreak columns)

The watermark trails the data, `min(max(timestamp), now) - settle`, and never decreases, so a
row is published once nothing older is expected to arrive any more. The prefix is cut into
segments [B_k, B_k+1) whose row counts are frozen when they are published; a page is served
by fetching the segments it overlaps, ordered, and taking exactly the frozen count from each
segment. Consequences:

  - positions below the published size never change while no row arrives later than
    `settle` (measured; see create_live_table) and nothing is deleted;
  - a row that does arrive later than `settle` (or a row dropped by TTL) is detected by
    re-counting the published range on every poll (`late_rows` / `missing_rows`), and can only
    change rows inside its own segment (about one second of data), never shift later ones.
    Deephaven cannot retract rows, so such a row is not added to the table; a vanished row
    is served as nulls.

Pure logic, no Deephaven or QuestDB imports: queries are injected (unit tested).
"""

import bisect
import time

TICKS_PER_SECOND = {"ns": 1_000_000_000, "us": 1_000_000}
DEFAULT_SEGMENT_SECONDS = 1.0


class StablePrefix:
    """Published size and segment index of the settled prefix of one table.

    count(lo, hi) -> number of rows with lo <= ts < hi        (ticks, table's time unit)
    max_ts() -> max(timestamp) in ticks, or None for an empty table
    buckets(lo, hi) -> [(bucket_start, n), ...] sorted, for lo <= ts < hi (e.g. SAMPLE BY 1s);
        used once to index the history so a page query never has to sort all of it
    now() -> wall clock in ticks
    """

    def __init__(
        self,
        count,
        max_ts,
        buckets,
        start,
        settle_seconds,
        unit="ns",
        now=None,
        segment_seconds=DEFAULT_SEGMENT_SECONDS,
        log=print,
    ):
        self._count = count
        self._max_ts = max_ts
        self._buckets = buckets
        self.ticks_per_second = TICKS_PER_SECOND[unit]
        self._now = now or (lambda: time.time_ns() * self.ticks_per_second // 1_000_000_000)
        self._settle = int(settle_seconds * self.ticks_per_second)
        self._segment = int(segment_seconds * self.ticks_per_second)
        # micro-second aligned boundaries: exact in any unit and in microsecond clients
        self._align = self.ticks_per_second // 1_000_000
        self.start = self._floor(start)
        self._log = log
        # boundaries B[0..m] and cumulative counts C[0..m]: segment k is [B[k], B[k+1])
        # holding C[k+1] - C[k] rows. B[-1] is the published watermark, C[-1] the size.
        self.bounds = [self.start]
        self.cum = [0]
        self._observed = 0  # rows currently in QuestDB below the watermark (last poll)
        self.late_rows = 0  # rows that appeared below the published watermark
        self.missing_rows = 0  # published rows that disappeared (TTL, deletes)
        self.violations = []  # (watermark, drift) of each detected change, newest last
        self.initialized = False

    # ---- state
    @property
    def size(self):
        return self.cum[-1]

    @property
    def watermark(self):
        return self.bounds[-1]

    def _floor(self, ticks):
        return ticks - ticks % self._align

    def target(self):
        """Watermark the data allows now (never below the published one)."""
        newest = self._max_ts()
        if newest is None:
            return self.watermark
        w = self._floor(min(newest, self._now()) - self._settle)
        return max(w, self.watermark)

    # ---- publishing
    def initialize(self):
        """Index the history [start, watermark) from per-bucket counts. Returns the size."""
        w = self.target()
        if w > self.start:
            buckets = list(self._buckets(self.start, w))
            # bucket k covers [its start, next bucket's start); the first one starts at `start`
            # (its SAMPLE BY bucket may begin earlier) and the last one ends at the watermark
            starts = [self.start] + [self._floor(b) for b, _ in buckets[1:]]
            for b, (_, n) in zip(starts[1:], buckets):
                self.bounds.append(b)
                self.cum.append(self.cum[-1] + n)
            self.bounds.append(w)
            self.cum.append(self.cum[-1] + (buckets[-1][1] if buckets else 0))
        self._observed = self.size
        self.initialized = True
        return self.size

    def poll(self):
        """Advance the watermark. Returns the new size if it grew, else None.

        Also re-counts the published range [start, watermark): a change means a row arrived
        later than settle (positive drift) or rows were removed (negative drift).
        """
        w_new = self.target()
        below = self._count(self.start, self.watermark) if self.watermark > self.start else 0
        drift = below - self._observed
        if drift:
            if drift > 0:
                self.late_rows += drift
            else:
                self.missing_rows -= drift
            self.violations.append((self.watermark, drift))
            del self.violations[:-100]
            self._log(
                f"[StablePrefix] {drift:+d} row(s) below the published watermark "
                f"(late_rows={self.late_rows}, missing_rows={self.missing_rows}); "
                f"positions after the affected segment are unchanged"
            )
        self._observed = below
        if w_new <= self.watermark:
            return None
        n = self._count(self.watermark, w_new)
        self._observed += n
        self._append(w_new, n)
        return self.size if n else None

    def _append(self, w_new, n):
        k = len(self.bounds) - 1
        # merge into the last segment while it is shorter than segment_seconds (the merged
        # segment is the union of two published ones, so every position stays the same)
        if k >= 1 and self.bounds[k] - self.bounds[k - 1] < self._segment:
            self.bounds[k] = w_new
            self.cum[k] += n
        else:
            self.bounds.append(w_new)
            self.cum.append(self.cum[-1] + n)

    # ---- paging
    def locate(self, lo, hi):
        """Segments covering positions [lo, hi): (first segment, its first position, bounds
        B[i..j+1], per-segment counts)."""
        assert 0 <= lo < hi <= self.size, (lo, hi, self.size)
        i = bisect.bisect_right(self.cum, lo) - 1
        j = bisect.bisect_right(self.cum, hi - 1) - 1
        bounds = self.bounds[i : j + 2]
        counts = [self.cum[k + 1] - self.cum[k] for k in range(i, j + 1)]
        return self.cum[i], bounds, counts


def take_indices(ticks, bounds, counts):
    """Row indices into an ordered fetch that reproduce the published segments.

    ticks: timestamps of the fetched rows (ordered); bounds: B[i..j+1]; counts: frozen rows
    per segment. Each segment contributes exactly its frozen count: its first rows in order,
    then None (a null row) for any that are gone. Returns (indices, extra, missing) where
    extra/missing count rows above/below the frozen counts.
    """
    out, extra, missing = [], 0, 0
    for k, n in enumerate(counts):
        a = bisect.bisect_left(ticks, bounds[k])
        b = bisect.bisect_left(ticks, bounds[k + 1])
        have = b - a
        take = min(have, n)
        out.extend(range(a, a + take))
        if have < n:
            out.extend([None] * (n - have))
            missing += n - have
        else:
            extra += have - n
    return out, extra, missing


def tie_order(keys, tiebreak):
    """Permutation that orders runs of equal `keys` (already sorted) by `tiebreak(i)`.

    Returns None when no run has more than one row (nothing to reorder). Deterministic for a
    given set of rows, which is all positional paging needs.
    """
    perm, n, i, changed = list(range(len(keys))), len(keys), 0, False
    while i < n:
        j = i + 1
        while j < n and keys[j] == keys[i]:
            j += 1
        if j - i > 1:
            perm[i:j] = sorted(range(i, j), key=tiebreak)
            changed = True
        i = j
    return perm if changed else None


def order_by(columns, upsert_keys, ts_column="timestamp", array_columns=()):
    """A total order for the table: timestamp, the upsert keys, then every other column.

    Many rows share a timestamp (QuestDB keeps them in arrival order, which differs between
    a row's first and a later read once out-of-order rows were merged in), so the timestamp
    alone does not fix positions. Rows that tie on every column are identical, so their
    order does not matter. Array columns come last (the backend breaks ties on them, see
    tie_order).
    """
    keys = [ts_column] + [c for c in upsert_keys if c != ts_column]
    rest = [c for c in columns if c not in keys and c not in array_columns]
    return keys + rest + [c for c in columns if c in array_columns and c not in keys]
