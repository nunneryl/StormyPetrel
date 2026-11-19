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

THE CHECK THAT CATCHES A SHORT READ. Before and after reading, the month's rows are counted in
the database (count=exact). The two counts and the rows read must be equal, or nothing is
uploaded: a server page cap below PAGE, a unit the walk missed (a NULL unit), or rows written
mid-read all fail here, loudly. A month is read only once it ended MIN_AGE_DAYS ago, because
the pipeline can still rewrite the last hours of a month for a day or so.

THE FILES, in the private bucket retention-archive (created 2026-11-19, 50 MB a file, gzip and
JSON only, no storage policy: only the service key reaches it):
    <table>/<YYYY-MM>/part-0001.csv.gz ...    CSV, one header row, NULL written as \\N
    <table>/<YYYY-MM>/manifest.json           columns, each part's rows, bytes and SHA-256,
                                              the database counts, the query, the time read
Months are UTC calendar months on the table's time column (tide_predictions.predicted_at holds
station-local time labelled as UTC, so its months follow the stored value).

    python -m pipeline.retention status [--table T]
    python -m pipeline.retention archive --table T|all --month YYYY-MM|all [--apply]
    python -m pipeline.retention restore --table T --month YYYY-MM [--apply]
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
    head, _, frac_tz = s.partition(".")
    if frac_tz:                                   # pad or cut fractions to 6 digits (Python 3.9)
        digits = "".join(ch for ch in frac_tz if ch.isdigit())
        s = f"{head}.{(digits + '000000')[:6]}{frac_tz[len(digits):]}"
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
        rows = qb.order(spec.unit).range(0, 0).execute().data or []
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
        rows = qb.range(0, PAGE - 1).execute().data or []
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


def count(client, spec: Spec, start: datetime, end: datetime) -> int:
    res = (client.table(spec.table).select(spec.unit, count="exact", head=True)
           .gte(spec.time, q(start)).lt(spec.time, q(end)).execute())
    if res.count is None:
        raise RetentionError(f"{spec.table}: the database gave no count")
    return int(res.count)


def earliest(client, spec: Spec) -> datetime | None:
    rows = (client.table(spec.table).select(spec.time).order(spec.time)
            .range(0, 0).execute().data or [])
    return parse_ts(rows[0][spec.time]) if rows else None


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
    """Read one month of one table into parts. Returns columns, JSON columns, rows, parts."""
    start, end = month_bounds(month)
    columns: list[str] | None = None
    json_cols: set = set()
    parts: Parts | None = None
    n = 0
    for u in unit_list:
        for row in read_unit(client, spec, u, start, end):
            if columns is None:
                columns = list(row.keys())
                parts = Parts(outdir, columns)
            if list(row.keys()) != columns:
                raise RetentionError(f"{spec.table}: a row's columns differ from the first row's")
            parts.write([cell(row[c], c, json_cols) for c in columns])
            n += 1
    return {"columns": columns or [], "json_columns": sorted(json_cols), "rows": n,
            "parts": parts.close() if parts else []}


# --------------------------------------------------------------------------- #
# Storage                                                                      #
# --------------------------------------------------------------------------- #
class NotFound(Exception):
    pass


class Storage:
    """The three Storage REST calls this needs, with the service key. Nothing else: no delete."""

    def __init__(self, url: str, key: str, bucket: str = BUCKET, session=None, timeout=300):
        import requests
        self.base = url.rstrip("/") + "/storage/v1/object"
        self.key, self.bucket, self.timeout = key, bucket, timeout
        self.http = session or requests.Session()

    def _headers(self, extra=None) -> dict:
        return {"Authorization": f"Bearer {self.key}", "apikey": self.key, **(extra or {})}

    def download(self, path: str) -> bytes:
        r = self.http.get(f"{self.base}/{self.bucket}/{path}", headers=self._headers(),
                          timeout=self.timeout)
        if r.status_code == 200:
            return r.content
        body = (r.text or "")[:300]
        if r.status_code == 404 or (r.status_code == 400 and "not found" in body.lower().replace("_", " ")):
            raise NotFound(path)
        raise RetentionError(f"download {path}: HTTP {r.status_code} {body}")

    def exists(self, path: str) -> bool:
        try:
            self.download(path)
            return True
        except NotFound:
            return False

    def upload(self, path: str, data: bytes, content_type: str) -> None:
        r = self.http.post(f"{self.base}/{self.bucket}/{path}", data=data, timeout=self.timeout,
                           headers=self._headers({"Content-Type": content_type,
                                                  "x-upsert": "false"}))
        if r.status_code != 200:
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
    before = count(client, spec, start, end)
    if unit_list is None:
        unit_list = units(client, spec)
    tmp = Path(tempfile.mkdtemp(prefix=f"retention-{spec.table}-{month}-", dir=workdir))
    got = export(client, spec, month, unit_list, tmp)
    after = count(client, spec, start, end)
    took = time.monotonic() - t0
    if not before == got["rows"] == after:
        raise RetentionError(f"{spec.table} {month}: counted {before} rows before reading, read "
                             f"{got['rows']}, counted {after} after; nothing uploaded")
    manifest = {
        "table": spec.table, "month": month, "time_column": spec.time,
        "from": q(start), "to": q(end), "unit_column": spec.unit, "order": list(spec.order),
        "unique_key": spec.conflict, "columns": got["columns"],
        "json_columns": got["json_columns"], "null": NULL,
        "format": "CSV, UTF-8, one header row per part, gzip; NULL written as \\N",
        "rows": got["rows"], "db_count_before": before, "db_count_after": after,
        "units": len(unit_list),
        "parts": [{k: p[k] for k in ("name", "rows", "bytes", "sha256")} for p in got["parts"]],
        "archived_at": q(now), "tool": "pipeline/retention.py",
    }
    mb = sum(p["bytes"] for p in got["parts"]) / 1e6
    summary = {"table": spec.table, "month": month, "rows": got["rows"],
               "parts": len(got["parts"]), "mb": round(mb, 1), "read_s": round(took)}
    if not apply:
        log.info("DRY RUN %s %s: %d rows (database %d before, %d after) in %d part(s), %.1f MB, "
                 "read in %.0f s over %d %s values; nothing uploaded",
                 spec.table, month, got["rows"], before, after, len(got["parts"]), mb, took,
                 len(unit_list), spec.unit)
        return {**summary, "status": "dry run"}

    for p in got["parts"]:
        path = f"{prefix}/{p['name']}"
        storage.upload(path, Path(p["local"]).read_bytes(), "application/gzip")
        back = storage.download(path)
        if hashlib.sha256(back).hexdigest() != p["sha256"]:
            raise RetentionError(f"{path}: the downloaded copy's SHA-256 differs; no manifest")
        header, rows = read_part(back)
        if header != got["columns"] or len(rows) != p["rows"]:
            raise RetentionError(f"{path}: the downloaded copy has {len(rows)} rows, not "
                                 f"{p['rows']}, or another header; no manifest")
    body = json.dumps(manifest, indent=1).encode("utf-8")
    storage.upload(f"{prefix}/manifest.json", body, "application/json")
    if storage.download(f"{prefix}/manifest.json") != body:
        raise RetentionError(f"{prefix}/manifest.json: the downloaded copy differs")
    log.info("ARCHIVED %s %s: %d rows in %d part(s), %.1f MB, each downloaded and checked",
             spec.table, month, got["rows"], len(got["parts"]), mb)
    return {**summary, "status": "archived"}


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
    in_db = count(client, spec, start, end)
    if not apply:
        log.info("DRY RUN restore %s %s: archive checked, %d rows in %d part(s); the table holds "
                 "%d rows for the month now; nothing written", spec.table, month, total,
                 len(parts), in_db)
        return {"table": spec.table, "month": month, "rows": total, "in_db": in_db,
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
    after = count(client, spec, start, end)
    log.info("RESTORED %s %s: %d of %d archived rows inserted, %d already there; the table "
             "holds %d rows for the month (was %d)", spec.table, month, inserted, total,
             total - inserted, after, in_db)
    return {"table": spec.table, "month": month, "rows": total, "inserted": inserted,
            "skipped": total - inserted, "in_db_before": in_db, "in_db_after": after,
            "status": "restored"}


def months_of(client, spec: Spec, now: datetime) -> list[str]:
    """Every complete month from the table's earliest row."""
    first = earliest(client, spec)
    if first is None:
        return []
    out, m = [], f"{first.year:04d}-{first.month:02d}"
    while complete(m, now):
        out.append(m)
        m = next_month(m)
    return out


def status(client, storage, spec: Spec, now: datetime) -> list[dict]:
    out = []
    for m in months_of(client, spec, now):
        start, end = month_bounds(m)
        out.append({"table": spec.table, "month": m, "rows": count(client, spec, start, end),
                    "archived": storage.exists(f"{spec.table}/{m}/manifest.json")})
    for r in out:
        log.info("%-18s %s %9d rows  %s", r["table"], r["month"], r["rows"],
                 "archived" if r["archived"] else "not archived")
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
    ap.add_argument("action", choices=["status", "archive", "restore"])
    ap.add_argument("--table", default="all", choices=["all", *TABLES])
    ap.add_argument("--month", default="all", help="YYYY-MM, or all (archive: every complete "
                                                   "month without a manifest)")
    ap.add_argument("--apply", action="store_true",
                    help="archive: upload; restore: insert. Without it, a dry run.")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    now = now or datetime.now(timezone.utc)
    try:
        client = client or get_client()
        storage = storage or Storage(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
        specs = list(TABLES.values()) if args.table == "all" else [TABLES[args.table]]
        if args.action == "status":
            for s in specs:
                status(client, storage, s, now)
            return 0
        if args.action == "restore":
            if args.table == "all" or args.month == "all":
                raise RetentionError("restore takes one --table and one --month")
            restore(client, storage, specs[0], args.month, args.apply)
            return 0
        for s in specs:
            months = months_of(client, s, now) if args.month == "all" else [args.month]
            unit_list = None
            for m in months:
                if args.month == "all" and storage.exists(f"{s.table}/{m}/manifest.json"):
                    log.info("%s %s: already archived; skipped", s.table, m)
                    continue
                if unit_list is None:
                    unit_list = units(client, s)
                archive(client, storage, s, m, args.apply, now, unit_list=unit_list)
        return 0
    except (RetentionError, KeyError) as e:
        print(f"::error title=retention::{e}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
