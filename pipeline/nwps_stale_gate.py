"""End a full pipeline run FAILED when NWPS is stale for many offices, so the failure issue opens.

WHY. NWPS v1.5 (NWS SCN 26-91) renamed every file from the 2026-11-09 12Z run. From the 23:23Z
run that day, 19 to 25 of the 25 offices read cycles up to 30 h behind, or none, run after run,
and every run passed: the only trace was log lines. nwps.fetch now writes one freshness row per
office (pipeline/forecast_data/nwps_freshness.json, migration 029), each with a stale_reason when
the office read no cycle, read one behind the newest NOMADS listed, or read one 24 h old or more.

THE RULE. STALE_OFFICES_TO_FAIL or more stale offices fail the run, with one line naming each
and why. Of the full-run logs read from 2026-10-25 to 11-10, the 18 before the rename had no
stale office, the 4 between the rename and the fix (#306) had 19 to 25, and the first after the
fix had none. One office alone stays a warning
(nwps.report_freshness): an office can skip a cycle without anything being wrong on our side.

NEVER STOPS THE DATA. The step runs after every upload and the revalidation, beside the column
check, so a failure here costs nothing but a red run and an issue. A missing or empty file fails
too: it means the NWPS fetch never finished (fetch_all logs a fetcher's crash and carries on).

    python -m pipeline.nwps_stale_gate            # exit 1 with one ::error:: line, or 0
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .config import NWPS_FRESHNESS_FILE

STALE_OFFICES_TO_FAIL = 5


def verdict(rows: list[dict], threshold: int = STALE_OFFICES_TO_FAIL) -> str | None:
    """The one line to fail with, or None when fewer than *threshold* offices are stale."""
    stale = [r for r in rows if r.get("stale_reason")]
    if len(stale) < threshold:
        return None
    named = "; ".join(f"{r.get('wfo')} {r['stale_reason']}" for r in stale)
    return (f"NWPS stale for {len(stale)} of {len(rows)} offices this run (forecast rows keep "
            f"older cycles): {named}")


def main(argv: list[str] | None = None, path: Path | None = None) -> int:
    path = Path(path) if path is not None else NWPS_FRESHNESS_FILE
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"::error::NWPS freshness file missing ({path}): the NWPS fetch did not finish "
              f"this run.")
        return 1
    except (OSError, ValueError) as e:
        print(f"::error::NWPS freshness file unreadable ({path}): {e}")
        return 1
    if not rows:
        print(f"::error::NWPS freshness file is empty ({path}): no office was fetched this run.")
        return 1
    line = verdict(rows)
    if line:
        print(f"::error::{line}")
        return 1
    stale = sum(1 for r in rows if r.get("stale_reason"))
    print(f"NWPS: {len(rows)} offices, {stale} stale (the run fails at {STALE_OFFICES_TO_FAIL}).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
