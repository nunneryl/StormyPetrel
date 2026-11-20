"""Archive a month of a big table to Supabase Storage, check it, and restore it. DELETION IS OFF.

WHY. On 2026-11-19 the five tables below held about 3.1 GB and grew about 24 MB a day; without
retention the database reaches 8 GB around June 2027. The plan keeps 90 days of forecasts and
60 days of buoy readings and scoreboard capture in the database. THIS FILE ONLY COPIES: it has
no delete command, no delete call and no delete option. Deleting archived months is a later,
separate change (after the 23 Nov report and the winter re-measurement), through review.

WHAT IT DOES
  status            each table's months, its row count per month, and whether the month is
                    archived (its manifest.json is in the bucket).
  archive           one month of one table (or every complete month without a manifest, or every
                    table): reads it, writes gzipped CSV parts of PART_ROWS rows and a manifest.
                    DRY RUN BY DEFAULT: the whole month is read and the parts are built and
                    counted locally, nothing is uploaded. With --apply each part is uploaded,
                    downloaded again and checked (SHA-256, header, rows); the manifest goes up last,
                    so a month counts as archived only when every part checked out. Nothing in the
                    bucket is ever overwritten (x-upsert: false), and an archived month is skipped.
                    RETRY-SAFE: a run that stopped partway leaves parts and no manifest. The parts
                    are rebuilt byte for byte from the same rows (fixed order, gzip mtime 0), so a
                    part already in the bucket whose SHA-256, header and rows match this run's is
                    kept and not uploaded again; one that differs, or a leftover part this run
                    does not make, stops the run naming it, and nothing is overwritten. A dry run
                    says which parts are already there and whether they match. Each part's rows
                    and size are logged.
  restore           one month of one table back into the database: every part is downloaded and
                    checked against the manifest before anything is written. Dry run by default;
                    with --apply rows are inserted in RESTORE_BATCH batches, and rows already in
                    the table are skipped (on conflict on the table's unique key, do nothing). The
                    serial id of forecasts, buoy_observations and tide_predictions is left out on
                    restore, so a restored row gets a new one; nothing refers to those ids.

HOW IT READS (no database password: the REST API with SUPABASE_URL / SUPABASE_SERVICE_KEY).
One table's rows are read ONE UNIT AT A TIME (a spot, a buoy, a tide station), each request one
page of at most PAGE rows from offset 0, filtered to the month and ordered on the rest of the
table's unique key: the order of its (unit, time, ...) index. A request that fills a page is
split: the rows before the page's last time are kept and the next request starts AT that time
(keyset), so rows sharing a time are never cut in two and no request ever pages past its first
PAGE rows. The units are found the same way, one keyed request each (the next unit after the
last one, in index order): no DISTINCT, no deep paging.

THE CHECK THAT CATCHES A SHORT READ. Each unit's rows for the month are counted exactly in the
database just before and just after that unit is read (count=exact, the unit and the month in
the filter: an index range of at most a few thousand rows). The two counts and the rows read
must be equal for every unit, or nothing is uploaded: a server page cap below PAGE, or rows
written mid-read, fail here, loudly, naming the unit. Rows whose unit is NULL cannot be read by
unit, so they are counted too (unit IS NULL in the month) and must be none. The month's totals
in the manifest are the sums.

NEVER ONE COUNT OVER A WHOLE MONTH. On 2026-11-19 (retention run 1) `status` counted forecasts a
month at a time with one exact count each: April took 0.8 s, May 2.3, June 3.1, July 5.6, August
7.6, and September hit the API's statement timeout (57014) at 8.3 s, although forecasts has an
index on valid_time. Counting ~600k rows is the cost, not finding them. So every exact count is
per unit, and `status` shows the planner's estimate per month (count=planned, marked "~",
approximate) unless --exact asks for the per-unit sums. The log names the table and month
before each count. A month is read only once it ended MIN_AGE_DAYS ago, because
the pipeline can still rewrite the last hours of a month for a day or so.

THE FILES, in the private bucket retention-archive (created 2026-11-19, 50 MB a file, gzip and
JSON only, no storage policy: only the service key reaches it):
    <table>/<YYYY-MM>/part-0001.csv.gz ...    CSV, one header row, NULL written as \\N
    <table>/<YYYY-MM>/manifest.json           columns, each part's rows, bytes and SHA-256,
                                              the database counts, the query, the time read
Months are UTC calendar months on the table's time column (tide_predictions.predicted_at holds
station-local time labelled as UTC, so its months follow the stored value).

    python -m pipeline.retention status [--table T] [--exact]
    python -m pipeline.retention archive --table T|all --month YYYY-MM|all [--apply]
    python -m pipeline.retention restore --table T --month YYYY-MM [--apply]
    python -m pipeline.retention bucket [--table T]      every archived month, from the bucket alone
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import logging
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("retention")

BUCKET = "retention-archive"
PAGE = 1000               # rows per request: PostgREST's max_rows on this project
PART_ROWS = 100_000       # rows per gzipped CSV part (~5-10 MB of forecasts)
RESTORE_BATCH = 500
MIN_AGE_DAYS = 2          # a month is read only once it ended this long ago
MAX_UNITS = 20_000        # a runaway unit walk stops here
NULL = "\\N"


@dataclass(frozen=True)
class Spec:
    table: str
    unit: str                 # one request reads one of these at a time
    time: str                 # the month is taken on this column
    order: tuple              # the rest of the unique key after unit, time first
    conflict: str             # the unique key: restore's on_conflict
    surrogate: str | None     # a serial id, left out on restore


TABLES: dict[str, Spec] = {s.table: s for s in [
    Spec("forecasts", "spot_id", "valid_time", ("valid_time", "source"),
         "spot_id,valid_time,source", "id"),
    Spec("buoy_observations", "buoy_id", "observed_at", ("observed_at",),
         "buoy_id,observed_at", "id"),
    Spec("forecast_leads", "spot_id", "valid_time", ("valid_time", "lead_day"),
         "spot_id,valid_time,lead_day", None),
    Spec("model_at_buoys", "buoy_id", "valid_time", ("valid_time", "source", "lead_day"),
         "buoy_id,valid_time,source,lead_day", None),
    Spec("tide_predictions", "station_id", "predicted_at", ("predicted_at",),
         "station_id,predicted_at", "id"),
]}


class RetentionError(Exception):
    """A check failed: nothing more is uploaded or written."""


# --------------------------------------------------------------------------- #
# Times                                                                        #
# --------------------------------------------------------------------------- #
def month_bounds(month: str) -> tuple[datetime, datetime]:
    """[first instant, first instant of the next month) of 'YYYY-MM', UTC."""
    try:
        y, m = int(month[:4]), int(month[5:7])
        if len(month) != 7 or month[4] != "-" or not 1 <= m <= 12:
            raise ValueError
    except ValueError:
        raise RetentionError(f"month must be YYYY-MM, not {month!r}") from None
    start = datetime(y, m, 1, tzinfo=timezone.utc)
    end = datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=timezone.utc)
    return start, end


def next_month(month: str) -> str:
    end = month_bounds(month)[1]
    return f"{end.year:04d}-{end.month:02d}"


def parse_ts(v: str) -> datetime:
    """A timestamptz as PostgREST returns it ('2026-08-01T00:10:00+00:00', fractions allowed)."""
    s = v.strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    head, dot, rest = s.partition(".")
    if dot:
        # Python 3.9's fromisoformat takes exactly 3 or 6 fraction digits (3.11 takes any), so pad
        # or cut the fraction, the digits up to the offset only, to 6.
        n = 0
        while n < len(rest) and rest[n].isdigit():
            n += 1
        s = f"{head}.{(rest[:n] + '000000')[:6]}{rest[n:]}"
    dt = datetime.fromisoformat(s)
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def q(dt: datetime) -> str:
    """A filter value: UTC, microseconds kept, 'Z' (no '+' to escape in a URL)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def complete(month: str, now: datetime) -> bool:
    return month_bounds(month)[1] <= now - timedelta(days=MIN_AGE_DAYS)


# --------------------------------------------------------------------------- #
# Reading                                                                      #
# --------------------------------------------------------------------------- #
def units(client, spec: Spec) -> list:
    """Every value of the unit column, ascending, one keyed request each (the next after the
    last, in index order; never a DISTINCT, never an offset)."""
    out: list = []
    while True:
        qb = client.table(spec.table).select(spec.unit)
        if out:
            qb = qb.gt(spec.unit, out[-1])
        rows = _run(qb.order(spec.unit).range(0, 0),
                    f"{spec.table}: next {spec.unit} after {out[-1] if out else 'none'}").data or []
        if not rows or rows[0].get(spec.unit) is None:
            return out
        out.append(rows[0][spec.unit])
        if len(out) > MAX_UNITS:
            raise RetentionError(f"{spec.table}: more than {MAX_UNITS} {spec.unit} values")


def read_unit(client, spec: Spec, unit, start: datetime, end: datetime):
    """One unit's rows with start <= time < end, in key order. Each request is one page from
    offset 0; a full page is split at its last time (keyset), which is read again in full."""
    lo = start
    requests = 0
    while True:
        qb = (client.table(spec.table).select("*").eq(spec.unit, unit)
              .gte(spec.time, q(lo)).lt(spec.time, q(end)))
        for col in spec.order:
            qb = qb.order(col)
        rows = _run(qb.range(0, PAGE - 1),
                    f"{spec.table} {spec.unit}={unit}: read from {q(lo)}").data or []
        requests += 1
        if len(rows) < PAGE:
            yield from rows
            return
        last = rows[-1][spec.time]
        keep = [r for r in rows if r[spec.time] != last]
        if not keep:
            raise RetentionError(f"{spec.table} {spec.unit}={unit}: {PAGE} rows share "
                                 f"{spec.time} {last}; a page cannot be split")
        yield from keep
        lo = parse_ts(last)


def _run(qb, what: str):
    """Execute, turning any API error (a statement timeout is 57014) into one naming *what*."""
    try:
        return qb.execute()
    except RetentionError:
        raise
    except Exception as e:  # noqa: BLE001  postgrest's APIError, or the network
        raise RetentionError(f"{what}: {type(e).__name__}: {e}") from e


def _count(qb, what: str) -> int:
    res = _run(qb, what)
    if res.count is None:
        raise RetentionError(f"{what}: the database gave no count")
    return int(res.count)


def count_unit(client, spec: Spec, unit, start: datetime, end: datetime, label: str) -> int:
    """Exact count of one unit's rows in [start, end): one index range on (unit, time)."""
    return _count(client.table(spec.table).select(spec.unit, count="exact", head=True)
                  .eq(spec.unit, unit).gte(spec.time, q(start)).lt(spec.time, q(end)),
                  f"{spec.table} {label} {spec.unit}={unit}: exact count")


def count_null_unit(client, spec: Spec, start: datetime, end: datetime, label: str) -> int:
    """Rows in [start, end) whose unit is NULL: they cannot be read by unit, so must be none."""
    return _count(client.table(spec.table).select(spec.time, count="exact", head=True)
                  .is_(spec.unit, "null").gte(spec.time, q(start)).lt(spec.time, q(end)),
                  f"{spec.table} {label} {spec.unit} IS NULL: exact count")


def counts_by_unit(client, spec: Spec, unit_list: list, start: datetime, end: datetime,
                   label: str) -> dict:
    """{unit: exact rows in [start, end)}, one count per unit, never one count over the whole
    month. Rows with a NULL unit fail it."""
    log.info("%s %s: exact count, per %s (%d requests)", spec.table, label, spec.unit,
             len(unit_list) + 1)
    nulls = count_null_unit(client, spec, start, end, label)
    if nulls:
        raise RetentionError(f"{spec.table} {label}: {nulls} rows with {spec.unit} NULL, which "
                             "cannot be read by unit")
    return {u: count_unit(client, spec, u, start, end, label) for u in unit_list}


def count_keyed(client, spec: Spec, unit_list: list, start: datetime, end: datetime,
                label: str) -> int:
    """The month's exact row count as the sum of per-unit exact counts."""
    return sum(counts_by_unit(client, spec, unit_list, start, end, label).values())


def count_planned(client, spec: Spec, start: datetime, end: datetime, label: str) -> int:
    """The planner's estimate of the month's rows (count=planned): no rows are counted, so it
    cannot time out. Approximate."""
    log.info("%s %s: approximate count (planner's estimate)", spec.table, label)
    return _count(client.table(spec.table).select(spec.unit, count="planned", head=True)
                  .gte(spec.time, q(start)).lt(spec.time, q(end)),
                  f"{spec.table} {label}: planned count")


def earliest(client, spec: Spec, unit_list: list) -> datetime | None:
    """The table's earliest time: each unit's first row (an index lookup on (unit, time)), the
    least of them. Never an ORDER BY time over the whole table (buoy_observations and
    tide_predictions have no index on time alone)."""
    log.info("%s: earliest %s, per %s (%d requests)", spec.table, spec.time, spec.unit,
             len(unit_list))
    first = None
    for u in unit_list:
        rows = _run(client.table(spec.table).select(spec.time).eq(spec.unit, u)
                    .order(spec.time).range(0, 0), f"{spec.table} {spec.unit}={u}: first row").data
        if rows:
            t = parse_ts(rows[0][spec.time])
            first = t if first is None or t < first else first
    return first


# --------------------------------------------------------------------------- #
# The CSV parts                                                                #
# --------------------------------------------------------------------------- #
def cell(value, col: str, json_cols: set) -> str:
    if value is None:
        return NULL
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        json_cols.add(col)
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    s = str(value)
    if s == NULL:
        raise RetentionError(f"column {col} holds the text {NULL!r}, the NULL marker")
    return s


class Parts:
    """Gzipped CSV parts of at most PART_ROWS rows, each with the header. gzip mtime is 0, so
    the same rows always give the same bytes and the same SHA-256."""

    def __init__(self, outdir: Path, columns: list[str]):
        self.outdir, self.columns = outdir, columns
        self.done: list[dict] = []
        self._fh = self._gz = self._txt = self._w = None
        self._n = 0

    def _open(self):
        name = f"part-{len(self.done) + 1:04d}.csv.gz"
        self._path = self.outdir / name
        self._fh = open(self._path, "wb")
        self._gz = gzip.GzipFile(filename="", mode="wb", fileobj=self._fh, mtime=0)
        self._txt = io.TextIOWrapper(self._gz, encoding="utf-8", newline="")
        self._w = csv.writer(self._txt, lineterminator="\n")
        self._w.writerow(self.columns)
        self._n = 0

    def write(self, cells: list[str]):
        if self._w is None:
            self._open()
        self._w.writerow(cells)
        self._n += 1
        if self._n >= PART_ROWS:
            self._close()

    def _close(self):
        if self._w is None:
            return
        self._txt.flush()
        self._txt.detach()
        self._gz.close()
        self._fh.close()
        data = self._path.read_bytes()
        self.done.append({"name": self._path.name, "rows": self._n, "bytes": len(data),
                          "sha256": hashlib.sha256(data).hexdigest(), "local": str(self._path)})
        self._w = None

    def close(self) -> list[dict]:
        self._close()
        return self.done


def read_part(data: bytes) -> tuple[list[str], list[list[str]]]:
    """(header, rows) of one gzipped CSV part."""
    text = gzip.decompress(data).decode("utf-8")
    rows = list(csv.reader(io.StringIO(text, newline="")))
    if not rows:
        raise RetentionError("a part has no header")
    return rows[0], rows[1:]


def export(client, spec: Spec, month: str, unit_list: list, outdir: Path) -> dict:
    """Read one month of one table into parts, each unit counted exactly just before and just
    after it is read; the three must agree. Returns columns, JSON columns, rows, the summed
    counts and the parts."""
    start, end = month_bounds(month)
    log.info("%s %s: reading %d %s values, each counted exactly before and after", spec.table,
             month, len(unit_list), spec.unit)
    nulls = count_null_unit(client, spec, start, end, month)
    if nulls:
        raise RetentionError(f"{spec.table} {month}: {nulls} rows with {spec.unit} NULL, which "
                             "cannot be read by unit; nothing uploaded")
    columns: list[str] | None = None
    json_cols: set = set()
    parts: Parts | None = None
    n = before = after = 0
    for k, u in enumerate(unit_list, 1):
        b = count_unit(client, spec, u, start, end, month)
        got = 0
        for row in read_unit(client, spec, u, start, end):
            if columns is None:
                columns = list(row.keys())
                parts = Parts(outdir, columns)
            if list(row.keys()) != columns:
                raise RetentionError(f"{spec.table}: a row's columns differ from the first row's")
            parts.write([cell(row[c], c, json_cols) for c in columns])
            got += 1
        a = count_unit(client, spec, u, start, end, month)
        if not b == got == a:
            raise RetentionError(f"{spec.table} {month} {spec.unit}={u}: counted {b} rows before "
                                 f"reading, read {got}, counted {a} after; nothing uploaded")
        n, before, after = n + got, before + b, after + a
        if k % 100 == 0:
            log.info("%s %s: %d of %d %s values read, %d rows", spec.table, month, k,
                     len(unit_list), spec.unit, n)
    return {"columns": columns or [], "json_columns": sorted(json_cols), "rows": n,
            "before": before, "after": after, "parts": parts.close() if parts else []}


# --------------------------------------------------------------------------- #
# Storage                                                                      #
# --------------------------------------------------------------------------- #
class NotFound(Exception):
    pass


class Storage:
    """The Storage REST calls this needs, with the service key: list, download, upload. Nothing
    else: no delete.

    ROBUST, after retention run 6 (2026-11-20): checking whether forecasts/2026-06's first part
    was already in the bucket downloaded it, the object did not exist, and that GET hung for the
    whole 300 s read timeout (requests.ReadTimeout), ending the run. Now:
      - whether a file exists is read from one listing of its month's folder (POST
        /object/list), never by downloading it;
      - each call waits at most TIMEOUT (10 s to connect, 60 s for each read);
      - a timeout, a dropped connection, or a 429 / 5xx answer is retried up to ATTEMPTS times,
        BACKOFF_S apart, each retry logged with its cause; then the call fails naming the file;
      - an upload retried after a timeout may find its own first attempt landed: a "duplicate"
        answer then is taken as "there", and the caller's download and SHA-256 check decides.
        A duplicate on the first attempt is someone else's file, and is never overwritten."""

    TIMEOUT = (10, 60)
    ATTEMPTS = 4
    BACKOFF_S = (2, 5, 10)
    RETRY_STATUS = (429, 500, 502, 503, 504)

    def __init__(self, url: str, key: str, bucket: str = BUCKET, session=None, timeout=None,
                 attempts=None, backoff=None, sleep=time.sleep):
        import requests
        self.base = url.rstrip("/") + "/storage/v1/object"
        self.key, self.bucket = key, bucket
        self.timeout = timeout or self.TIMEOUT
        self.attempts = attempts or self.ATTEMPTS
        self.backoff = backoff if backoff is not None else self.BACKOFF_S
        self.sleep = sleep
        self.http = session or requests.Session()

    def _headers(self, extra=None) -> dict:
        return {"Authorization": f"Bearer {self.key}", "apikey": self.key, **(extra or {})}

    def _call(self, what: str, send, accept=lambda r: False):
        """send() up to self.attempts times. Returns (response, retried). A timeout, a dropped
        connection or a RETRY_STATUS answer is retried, unless accept(response) says the answer
        is final; anything else is returned at once."""
        import requests
        cause = None
        for k in range(1, self.attempts + 1):
            try:
                r = send()
                if r.status_code not in self.RETRY_STATUS or accept(r):
                    return r, k > 1
                cause = f"HTTP {r.status_code} {(r.text or '')[:120]}"
            except (requests.Timeout, requests.ConnectionError) as e:
                cause = f"{type(e).__name__}: {e}"
            if k < self.attempts:
                wait = self.backoff[min(k - 1, len(self.backoff) - 1)] if self.backoff else 0
                log.warning("storage: %s: attempt %d of %d failed (%s); retrying in %s s",
                            what, k, self.attempts, cause, wait)
                self.sleep(wait)
        raise RetentionError(f"storage: {what}: gave up after {self.attempts} attempts: {cause}")

    def _list(self, prefix: str) -> list:
        r, _ = self._call(f"list {prefix or '/'}", lambda: self.http.post(
            f"{self.base}/list/{self.bucket}", timeout=self.timeout,
            headers=self._headers({"Content-Type": "application/json"}),
            json={"prefix": prefix, "limit": 1000, "offset": 0,
                  "sortBy": {"column": "name", "order": "asc"}}))
        if r.status_code != 200:
            raise RetentionError(f"list {prefix}: HTTP {r.status_code} {(r.text or '')[:300]}")
        return list(r.json() or [])

    def names(self, prefix: str) -> set:
        """The files directly in the folder *prefix* (not its sub-folders)."""
        return {e["name"] for e in self._list(prefix) if e.get("id") is not None}

    def folders(self, prefix: str) -> list:
        """The sub-folders directly in *prefix*, sorted."""
        return sorted(e["name"] for e in self._list(prefix) if e.get("id") is None)

    def exists(self, path: str) -> bool:
        folder, _, name = path.rpartition("/")
        return name in self.names(folder)

    def download(self, path: str) -> bytes:
        r, _ = self._call(f"download {path}", lambda: self.http.get(
            f"{self.base}/{self.bucket}/{path}", headers=self._headers(), timeout=self.timeout))
        if r.status_code == 200:
            return r.content
        body = (r.text or "")[:300]
        if r.status_code == 404 or (r.status_code == 400 and "not found" in body.lower().replace("_", " ")):
            raise NotFound(path)
        raise RetentionError(f"download {path}: HTTP {r.status_code} {body}")

    @staticmethod
    def _duplicate(r) -> bool:
        body = (r.text or "").lower()
        return r.status_code in (400, 409) and ("duplicate" in body or "already exists" in body)

    def upload(self, path: str, data: bytes, content_type: str) -> None:
        r, retried = self._call(f"upload {path}", lambda: self.http.post(
            f"{self.base}/{self.bucket}/{path}", data=data, timeout=self.timeout,
            headers=self._headers({"Content-Type": content_type, "x-upsert": "false"})))
        if r.status_code == 200:
            return
        if retried and self._duplicate(r):
            log.warning("storage: upload %s: there after a failed attempt (it may have landed); "
                        "its download and SHA-256 decide", path)
            return
        raise RetentionError(f"upload {path}: HTTP {r.status_code} {(r.text or '')[:300]} "
                             "(an existing file is never overwritten)")


# --------------------------------------------------------------------------- #
# The commands                                                                 #
# --------------------------------------------------------------------------- #
def archive(client, storage, spec: Spec, month: str, apply: bool, now: datetime,
            unit_list: list | None = None, workdir: Path | None = None) -> dict:
    """Archive one month. Returns a summary; raises RetentionError when a check fails."""
    start, end = month_bounds(month)
    if not complete(month, now):
        raise RetentionError(f"{spec.table} {month}: the month must have ended {MIN_AGE_DAYS} "
                             f"days ago ({end:%Y-%m-%d} + {MIN_AGE_DAYS} d)")
    prefix = f"{spec.table}/{month}"
    if storage.exists(f"{prefix}/manifest.json"):
        log.info("%s %s: already archived (manifest.json present); skipped", spec.table, month)
        return {"table": spec.table, "month": month, "status": "already archived"}

    t0 = time.monotonic()
    if unit_list is None:
        unit_list = units(client, spec)
    tmp = Path(tempfile.mkdtemp(prefix=f"retention-{spec.table}-{month}-", dir=workdir))
    got = export(client, spec, month, unit_list, tmp)
    before, after = got["before"], got["after"]
    took = time.monotonic() - t0
    manifest = {
        "table": spec.table, "month": month, "time_column": spec.time,
        "from": q(start), "to": q(end), "unit_column": spec.unit, "order": list(spec.order),
        "unique_key": spec.conflict, "columns": got["columns"],
        "json_columns": got["json_columns"], "null": NULL,
        "format": "CSV, UTF-8, one header row per part, gzip; NULL written as \\N",
        "rows": got["rows"], "db_count_before": before, "db_count_after": after,
        "db_count": f"exact, per {spec.unit}, just before and after each was read, summed",
        "units": len(unit_list),
        "parts": [{k: p[k] for k in ("name", "rows", "bytes", "sha256")} for p in got["parts"]],
        "archived_at": q(now), "tool": "pipeline/retention.py",
    }
    mb = sum(p["bytes"] for p in got["parts"]) / 1e6
    summary = {"table": spec.table, "month": month, "rows": got["rows"],
               "parts": len(got["parts"]), "mb": round(mb, 1), "read_s": round(took)}
    # What a stopped run left: one listing of the month's folder, then each part already there is
    # downloaded and compared with this run's.
    present = storage.names(prefix)
    already = {p["name"]: (_in_bucket(storage, f"{prefix}/{p['name']}", p, got["columns"])
                           if p["name"] in present else None) for p in got["parts"]}
    leftover = f"part-{len(got['parts']) + 1:04d}.csv.gz"
    extra = leftover in present
    for p in got["parts"]:
        state = already[p["name"]]
        log.info("%s %s %s: %d rows, %.2f MB, sha256 %s%s", spec.table, month, p["name"],
                 p["rows"], p["bytes"] / 1e6, p["sha256"][:12],
                 "" if state is None else ("; already in the bucket and matches" if state == "match"
                                           else f"; already in the bucket and DIFFERS ({state})"))
    if extra:
        log.info("%s %s %s: in the bucket, but this run makes only %d part(s)", spec.table, month,
                 leftover, len(got["parts"]))
    if not apply:
        log.info("DRY RUN %s %s: %d rows (database %d before, %d after) in %d part(s), %.1f MB, "
                 "read in %.0f s over %d %s values; %d part(s) already in the bucket and "
                 "matching; nothing uploaded",
                 spec.table, month, got["rows"], before, after, len(got["parts"]), mb, took,
                 len(unit_list), spec.unit, sum(v == "match" for v in already.values()))
        return {**summary, "status": "dry run", "reusable": sorted(
            k for k, v in already.items() if v == "match")}

    bad = [k for k, v in already.items() if v not in (None, "match")]
    if bad:
        raise RetentionError(f"{prefix}/{bad[0]} is already in the bucket and differs from this "
                             f"run's part ({already[bad[0]]}); it is never overwritten. Nothing "
                             "uploaded, no manifest.")
    if extra:
        raise RetentionError(f"{prefix}/{leftover} is in the bucket but this run makes only "
                             f"{len(got['parts'])} part(s); a manifest would not list it. Nothing "
                             "uploaded, no manifest.")
    reused = 0
    for p in got["parts"]:
        path = f"{prefix}/{p['name']}"
        if already[p["name"]] == "match":
            reused += 1
            log.info("%s: already in the bucket and matches; kept, not uploaded again", path)
            continue
        storage.upload(path, Path(p["local"]).read_bytes(), "application/gzip")
        back = storage.download(path)
        if hashlib.sha256(back).hexdigest() != p["sha256"]:
            raise RetentionError(f"{path}: the downloaded copy's SHA-256 differs; no manifest")
        header, rows = read_part(back)
        if header != got["columns"] or len(rows) != p["rows"]:
            raise RetentionError(f"{path}: the downloaded copy has {len(rows)} rows, not "
                                 f"{p['rows']}, or another header; no manifest")
        log.info("%s: uploaded, downloaded again and checked", path)
    body = json.dumps(manifest, indent=1).encode("utf-8")
    storage.upload(f"{prefix}/manifest.json", body, "application/json")
    if storage.download(f"{prefix}/manifest.json") != body:
        raise RetentionError(f"{prefix}/manifest.json: the downloaded copy differs")
    log.info("ARCHIVED %s %s: %d rows in %d part(s), %.1f MB, each downloaded and checked "
             "(%d uploaded, %d kept from an earlier run)", spec.table, month, got["rows"],
             len(got["parts"]), mb, len(got["parts"]) - reused, reused)
    return {**summary, "status": "archived", "reused": reused}


def _in_bucket(storage, path: str, part: dict, columns: list) -> str | None:
    """None when *path* is not in the bucket; "match" when it is this run's part (same SHA-256,
    header and rows); otherwise a few words on how it differs."""
    try:
        data = storage.download(path)
    except NotFound:
        return None
    sha = hashlib.sha256(data).hexdigest()
    if sha != part["sha256"]:
        return f"SHA-256 {sha[:12]}, this run's {part['sha256'][:12]}"
    header, rows = read_part(data)
    if header != columns or len(rows) != part["rows"]:
        return f"{len(rows)} rows or another header, this run's {part['rows']}"
    return "match"


def decode(value: str, col: str, json_cols: set):
    if value == NULL:
        return None
    if col in json_cols and value[:1] in "[{":
        return json.loads(value)
    return value


def restore(client, storage, spec: Spec, month: str, apply: bool) -> dict:
    """Check one archived month, and with apply insert its rows that are not in the table."""
    start, end = month_bounds(month)
    prefix = f"{spec.table}/{month}"
    try:
        manifest = json.loads(storage.download(f"{prefix}/manifest.json"))
    except NotFound:
        raise RetentionError(f"{prefix}: no manifest.json, so the month is not archived") from None
    if manifest.get("table") != spec.table or manifest.get("month") != month:
        raise RetentionError(f"{prefix}/manifest.json names {manifest.get('table')} "
                             f"{manifest.get('month')}")
    columns = manifest["columns"]
    json_cols = set(manifest.get("json_columns") or [])
    parts = []
    for p in manifest["parts"]:
        data = storage.download(f"{prefix}/{p['name']}")
        if hashlib.sha256(data).hexdigest() != p["sha256"]:
            raise RetentionError(f"{prefix}/{p['name']}: SHA-256 differs from the manifest")
        header, rows = read_part(data)
        if header != columns or len(rows) != p["rows"]:
            raise RetentionError(f"{prefix}/{p['name']}: {len(rows)} rows or its header differ "
                                 "from the manifest")
        parts.append(rows)
    total = sum(len(r) for r in parts)
    if total != manifest["rows"]:
        raise RetentionError(f"{prefix}: parts hold {total} rows, the manifest says "
                             f"{manifest['rows']}")
    for p in manifest["parts"]:
        log.info("%s/%s: %d rows, %.2f MB, SHA-256 matches the manifest", prefix, p["name"],
                 p["rows"], p["bytes"] / 1e6)
    unit_list = units(client, spec)
    db_by_unit = counts_by_unit(client, spec, unit_list, start, end, month)
    in_db = sum(db_by_unit.values())
    # Per unit, the archive's rows against the table's (CSV holds text; compare as text).
    ui = columns.index(spec.unit)
    arch_by_unit: dict = {}
    for rows in parts:
        for r in rows:
            arch_by_unit[r[ui]] = arch_by_unit.get(r[ui], 0) + 1
    db_text = {str(u): n for u, n in db_by_unit.items()}
    short = {u: (n, db_text.get(u, 0)) for u, n in arch_by_unit.items() if db_text.get(u, 0) < n}
    if not apply:
        if short:
            missing = sum(a - d for a, d in short.values())
            log.info("DRY RUN restore %s %s: archive checked, %d rows in %d part(s); the table "
                     "holds %d rows for the month; %d %s value(s) hold fewer rows than the "
                     "archive, %d rows short in all (an apply inserts at least these); nothing "
                     "written", spec.table, month, total, len(parts), in_db, len(short),
                     spec.unit, missing)
        else:
            log.info("DRY RUN restore %s %s: archive checked, %d rows in %d part(s); the table "
                     "holds %d rows for the month, and every %s holds at least as many rows as "
                     "the archive, so an apply would find them all present; nothing written",
                     spec.table, month, total, len(parts), in_db, spec.unit)
        return {"table": spec.table, "month": month, "rows": total, "in_db": in_db,
                "short_units": len(short), "short_rows": sum(a - d for a, d in short.values()),
                "status": "dry run"}

    keep = [c for c in columns if c != spec.surrogate]
    inserted = 0
    batch: list[dict] = []

    def flush():
        nonlocal inserted, batch
        if batch:
            res = (client.table(spec.table)
                   .upsert(batch, on_conflict=spec.conflict, ignore_duplicates=True).execute())
            inserted += len(res.data or [])
            batch = []

    for rows in parts:
        for r in rows:
            vals = dict(zip(columns, r))
            batch.append({c: decode(vals[c], c, json_cols) for c in keep})
            if len(batch) >= RESTORE_BATCH:
                flush()
    flush()
    after = count_keyed(client, spec, units(client, spec), start, end, month)
    log.info("RESTORED %s %s: %d of %d archived rows inserted, %d already there; the table "
             "holds %d rows for the month (was %d)", spec.table, month, inserted, total,
             total - inserted, after, in_db)
    return {"table": spec.table, "month": month, "rows": total, "inserted": inserted,
            "skipped": total - inserted, "in_db_before": in_db, "in_db_after": after,
            "status": "restored"}


def months_of(client, spec: Spec, now: datetime, unit_list: list) -> list[str]:
    """Every complete month from the table's earliest row."""
    first = earliest(client, spec, unit_list)
    if first is None:
        return []
    out, m = [], f"{first.year:04d}-{first.month:02d}"
    while complete(m, now):
        out.append(m)
        m = next_month(m)
    return out


def status(client, storage, spec: Spec, now: datetime, exact: bool = False) -> list[dict]:
    """Each complete month: its rows (the planner's estimate, approximate; or with *exact* the
    sum of per-unit exact counts) and whether it is archived."""
    unit_list = units(client, spec)
    out = []
    for m in months_of(client, spec, now, unit_list):
        start, end = month_bounds(m)
        rows = (count_keyed(client, spec, unit_list, start, end, m) if exact
                else count_planned(client, spec, start, end, m))
        out.append({"table": spec.table, "month": m, "rows": rows, "exact": exact,
                    "archived": storage.exists(f"{spec.table}/{m}/manifest.json")})
    for r in out:
        log.info("%-18s %s %s%9d rows  %s", r["table"], r["month"], " " if exact else "~",
                 r["rows"], "archived" if r["archived"] else "not archived")
    if not exact and out:
        log.info("%s: ~ marks the planner's estimate, approximate; --exact counts per %s",
                 spec.table, spec.unit)
    return out


def bucket(storage, specs: list) -> list[dict]:
    """Every month in the bucket, from the files alone (no database): its manifest's rows, parts
    and size, or the files of a month with no manifest (a run that stopped partway)."""
    out = []
    for spec in specs:
        for m in storage.folders(spec.table):
            files = storage.names(f"{spec.table}/{m}")
            if "manifest.json" in files:
                man = json.loads(storage.download(f"{spec.table}/{m}/manifest.json"))
                row = {"table": spec.table, "month": m, "archived": True, "rows": man["rows"],
                       "parts": len(man["parts"]),
                       "mb": round(sum(p["bytes"] for p in man["parts"]) / 1e6, 1)}
                log.info("%-18s %s ARCHIVED %9d rows in %d part(s), %.1f MB", spec.table, m,
                         row["rows"], row["parts"], row["mb"])
            else:
                row = {"table": spec.table, "month": m, "archived": False,
                       "files": sorted(files)}
                log.info("%-18s %s NO MANIFEST: %d file(s) from a run that stopped: %s",
                         spec.table, m, len(files), ", ".join(sorted(files)) or "none")
            out.append(row)
        if not any(r["table"] == spec.table for r in out):
            log.info("%-18s nothing in the bucket", spec.table)
    return out


def get_client():
    from supabase import create_client
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise RetentionError("SUPABASE_URL and SUPABASE_SERVICE_KEY are required")
    return create_client(url, key)


def main(argv: list[str] | None = None, client=None, storage=None,
         now: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=["status", "archive", "restore", "bucket"])
    ap.add_argument("--table", default="all", choices=["all", *TABLES])
    ap.add_argument("--month", default="all", help="YYYY-MM, or all (archive: every complete "
                                                   "month without a manifest)")
    ap.add_argument("--apply", action="store_true",
                    help="archive: upload; restore: insert. Without it, a dry run.")
    ap.add_argument("--exact", action="store_true",
                    help="status: exact counts, per unit and summed, instead of the estimate")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # One line per request would be thousands a month; this file logs its own progress.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    now = now or datetime.now(timezone.utc)
    try:
        storage = storage or Storage(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
        specs = list(TABLES.values()) if args.table == "all" else [TABLES[args.table]]
        if args.action == "bucket":
            bucket(storage, specs)
            return 0
        client = client or get_client()
        if args.action == "status":
            for s in specs:
                status(client, storage, s, now, exact=args.exact)
            return 0
        if args.action == "restore":
            if args.table == "all" or args.month == "all":
                raise RetentionError("restore takes one --table and one --month")
            restore(client, storage, specs[0], args.month, args.apply)
            return 0
        for s in specs:
            unit_list = units(client, s)
            months = months_of(client, s, now, unit_list) if args.month == "all" else [args.month]
            for m in months:
                if args.month == "all" and storage.exists(f"{s.table}/{m}/manifest.json"):
                    log.info("%s %s: already archived; skipped", s.table, m)
                    continue
                archive(client, storage, s, m, args.apply, now, unit_list=unit_list)
        return 0
    except (RetentionError, KeyError) as e:
        print(f"::error title=retention::{e}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
