"""pipeline/best_months.py's scoring (held from the descriptions until the archive covers a year).
Every expected value is worked out by hand."""
from __future__ import annotations

from pipeline import best_months as B


def _day(date, stars, hours=range(6, 19)):
    """Hours on *date* in Los Angeles (UTC-8 in winter, UTC-7 in summer) at *stars* each."""
    off = 7 if date[5:7] in ("04", "05", "06", "07", "08", "09", "10") else 8
    out = []
    for h in hours:
        utc = h + off
        day = int(date[8:10]) + utc // 24
        out.append((f"{date[:8]}{day:02d}T{utc % 24:02d}:00:00Z", stars))
    return out


def test_a_day_needs_eight_daylight_hours_and_is_good_at_three_stars():
    rows = (_day("2026-10-01", 3.0) + _day("2026-10-02", 2.5)
            + _day("2026-10-03", 4.0, hours=range(6, 13)))       # 7 hours: not counted
    assert B.month_table(rows, "America/Los_Angeles") == {"2026-10": {"days": 2, "good": 1}}


def test_night_hours_do_not_count():
    rows = _day("2026-10-01", 1.0) + [("2026-10-02T05:00:00Z", 5.0)]   # 22:00 local on the 1st
    assert B.month_table(rows, "America/Los_Angeles") == {"2026-10": {"days": 1, "good": 0}}


def test_the_best_months():
    months = {"2026-08": {"days": 31, "good": 3},     # 0.10: under the 0.2 floor
              "2026-09": {"days": 30, "good": 9},     # 0.30
              "2026-10": {"days": 31, "good": 16},    # 0.52
              "2026-11": {"days": 19, "good": 19}}    # 19 days: not counted
    assert B.best_of(months) == [{"month": "2026-10", "good": 16, "days": 31},
                                 {"month": "2026-09", "good": 9, "days": 30}]
    assert B.best_of({"2026-08": {"days": 31, "good": 3}}) == []
