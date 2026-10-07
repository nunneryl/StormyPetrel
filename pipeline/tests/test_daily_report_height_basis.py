"""The daily report tells Claude what each height is, and no longer that every one is CDIP's.

THE DEFECT. SYSTEM_PROMPT said every height it was given was "significant wave height measured
offshore of the surf zone" — CDIP MOP's, by the comment above it. That was true of none of them.
505 of the 646 rated spots publish an uncalibrated model estimate, boosted for long-period swell
and about 1.6 times MOP's where the two were compared; the 141 calibrated spots are model heights
scaled to CDIP; and MOP's own rows end before the hours the report reads. So the report could
call a model estimate a measurement, in the same breath as the site labelling it a model estimate.

WHAT IS HELD HERE.
  1. height_basis gives every row in frontend/lib/heightBasis.cases.json the basis written there
     — the same table heightBasis.test.mts holds the site's labels to — so the page and the
     report cannot describe one row two ways.
  2. The prompt Claude receives no longer claims the heights are measured or CDIP's, defines the
     three tags, and forbids calling any height measured or a model estimate calibrated.
  3. Each spot's line carries its own tag: a calibrated spot whose factor is below 1
     (point-arena), a MOP-tier spot's model row, a MOP-fed row, an uncalibrated spot.
  4. THE GUARD: across every swell_source the pipeline writes and a sweep of heights, a model-
     estimate row is tagged "model estimate" and its line never says calibrated or CDIP.
  5. The fetch selects the two columns height_basis reads. Without them every row would quietly
     come back a model estimate, which is safe but wrong at the 141 calibrated spots.
  6. WHAT IS STORED. daily_reports.top_spots gains ONE field, height_basis, read off the same row
     as face_ft, and every field it already carried is written exactly as before — same keys, same
     values, from the same row. The report cards print that basis; reports stored before it have
     no key, and the frontend shows them no tag (heightBasis.test.mts holds that side).

NO EXPECTED VALUE COMES FROM THE CODE UNDER TEST: every basis, tag and phrase is a literal here or
in the cases file.

Run: python -m pytest pipeline/tests/test_daily_report_height_basis.py
"""
from __future__ import annotations

import json
import os
import re

from pipeline import daily_report as D

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CASES = os.path.join(ROOT, "frontend", "lib", "heightBasis.cases.json")

# Every swell_source value the pipeline writes: rate_spot (ww3, nwps_swell, buoy, nwps_total,
# none), apply_nwps_overrides (nwps_height_ww3_dir, nwps) and apply_mop_overrides (cdip_mop).
NOT_MOP_SOURCES = ["ww3", "nwps_swell", "buoy", "nwps_total", "none", "nwps_height_ww3_dir", "nwps",
                   None, "", "CDIP_MOP", "cdip", "cdip_mop ", " cdip_mop", "mop"]


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _FakeAnthropic:
    """Records what generate_summary SENDS — the system prompt and the user message — so the
    assertions are on the request Claude would receive, not on a string rebuilt beside it."""

    def __init__(self):
        self.sent = []
        self.messages = self

    def create(self, **kwargs):
        self.sent.append(kwargs)
        return type("Msg", (), {"content": [_Block("A report.")]})()


def _row(face, raw, source, stars=3.0):
    return {"face_ft": face, "face_ft_raw": raw, "swell_source": source, "stars": stars,
            "swell_tp": 13.0, "swell_dp": 290.0, "wind_speed": 3.0, "wind_dir": 90.0}


def _spot(name, latest):
    return {"name": name, "state": "California", "offshore_wind_deg": 90.0,
            "latest": latest, "plus24": dict(latest)}


def _sent(top, region="Northern California"):
    fake = _FakeAnthropic()
    D.generate_summary(fake, region, "steady", top)
    assert len(fake.sent) == 1, fake.sent
    request = fake.sent[0]
    return request["system"], request["messages"][0]["content"]


def _line(user_prompt, name):
    found = [ln for ln in user_prompt.splitlines() if ln.startswith(f"- {name} (")]
    assert len(found) == 1, (name, user_prompt)
    return found[0]


# --------------------------------------------------------------------------- #
# 1 — the same rule as the site, case for case                                 #
# --------------------------------------------------------------------------- #
def test_every_shared_case_gets_the_basis_written_for_it():
    with open(CASES, encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    assert len(cases) >= 15, len(cases)
    wrong = [(c["name"], D.height_basis(c["row"]), c["basis"]) for c in cases
             if D.height_basis(c["row"]) != c["basis"]]
    assert not wrong, wrong


def test_the_named_cases():
    # point-arena: factor 0.9313 in the 2026-09-01 file, so 3.1 / 0.9313 = 3.3287 -> 3.33 is LARGER
    # than the raw 3.1. (Its factor in the 2026-10-06 file, 0.812, is further below 1 still.)
    assert D.height_basis({"face_ft": 3.33, "face_ft_raw": 3.1, "swell_source": "nwps_height_ww3_dir"}) \
        == "calibrated"
    # A MOP-tier spot's model row: never corrected (raw == face), not MOP-fed.
    assert D.height_basis({"face_ft": 5.12, "face_ft_raw": 5.12, "swell_source": "ww3"}) == "model"
    # A MOP-fed row.
    assert D.height_basis({"face_ft": 3.28, "face_ft_raw": 3.28, "swell_source": "cdip_mop"}) == "cdip"
    # No row, and a row with no height.
    assert D.height_basis(None) is None
    assert D.height_basis({"face_ft": None, "face_ft_raw": None, "swell_source": "none"}) is None
    # A raw value that is not a finite number is no evidence of calibration.
    assert D.height_basis({"face_ft": 4.0, "face_ft_raw": float("nan"), "swell_source": "ww3"}) == "model"
    assert D.height_basis({"face_ft": 4.0, "face_ft_raw": float("inf"), "swell_source": "ww3"}) == "model"


# --------------------------------------------------------------------------- #
# 2 — what the system prompt now says, and no longer says                      #
# --------------------------------------------------------------------------- #
def test_the_prompt_no_longer_says_every_height_is_a_cdip_measurement():
    system, _ = _sent([_spot("Uncalibrated", _row(4.4, 4.4, "nwps_height_ww3_dir"))])
    # The old claim, in the words it was made in.
    assert "measured offshore of the surf zone" not in system, system
    assert "significant wave height" not in system, system
    assert "MOP" not in system, system
    # "measured" survives only inside the instruction NOT to say it.
    assert [m.start() for m in re.finditer(r"measured", system)] == \
        [system.index("never describe a height as measured or observed")
         + len("never describe a height as ")], system


def test_the_prompt_forbids_calling_a_model_estimate_measured_or_calibrated():
    system, _ = _sent([_spot("Uncalibrated", _row(4.4, 4.4, "nwps_height_ww3_dir"))])
    assert "None of them is a measurement" in system, system
    assert "never describe a height as measured or observed" in system, system
    assert "never describe a model estimate as calibrated or as CDIP's" in system, system
    # Each tag the payload can carry is defined, in quotes, in the words the payload uses.
    for tag in ("'calibrated to CDIP'", "'CDIP nearshore height'", "'model estimate'"):
        assert tag in system, (tag, system)
    # And the face rule it already carried is still there.
    assert "Never call it face height" in system, system


# --------------------------------------------------------------------------- #
# 3 — each spot's line carries its own basis                                   #
# --------------------------------------------------------------------------- #
def test_each_spot_line_carries_its_own_tag():
    top = [
        _spot("Point Arena", _row(3.33, 3.1, "nwps_height_ww3_dir")),          # factor 0.9313 (09-01 file)
        _spot("Steamer Lane", _row(2.31, 4.62, "nwps_height_ww3_dir")),        # factor 2.0
        _spot("Asilomar State Beach", _row(5.12, 5.12, "ww3")),                # MOP tier, model row
        _spot("A MOP hour", _row(3.28, 3.28, "cdip_mop")),                     # MOP-fed
        _spot("Rockaway", _row(4.4, 4.4, "nwps_height_ww3_dir")),              # uncalibrated
        _spot("Old row", _row(4.0, None, "nwps_height_ww3_dir")),              # before migration 017
        _spot("Unrateable", _row(None, None, "none", stars=0.0)),
    ]
    _, user = _sent(top)
    assert "· 3.3ft swell height (calibrated to CDIP) @" in _line(user, "Point Arena")
    assert "· 2.3ft swell height (calibrated to CDIP) @" in _line(user, "Steamer Lane")
    assert "· 5.1ft swell height (model estimate) @" in _line(user, "Asilomar State Beach")
    assert "· 3.3ft swell height (CDIP nearshore height) @" in _line(user, "A MOP hour")
    assert "· 4.4ft swell height (model estimate) @" in _line(user, "Rockaway")
    assert "· 4.0ft swell height (model estimate) @" in _line(user, "Old row")
    assert "· — @" in _line(user, "Unrateable")
    # The trend line no longer calls the height a face.
    assert "TREND (next 24h, avg swell height change across top spots): steady" in user, user
    assert "face" not in user.lower(), user


def test_the_tag_reaches_the_prompt_through_build_region_report():
    """The whole path the workflow runs: region matching, ranking, then the request. The rows
    are shaped exactly as fetch_forecasts_window returns them, projected through _FCAST_COLS."""
    cols = [c.strip() for c in D._FCAST_COLS.split(",")]
    project = lambda r: {c: r.get(c) for c in cols}      # noqa: E731
    spots = [{"id": 1, "slug": "point-arena", "name": "Point Arena", "state": "California",
              "lat": 38.9, "lng": -123.7, "offshore_wind_deg": 90.0},
             {"id": 2, "slug": "rockaway", "name": "Rockaway", "state": "New York",
              "lat": 40.6, "lng": -73.8, "offshore_wind_deg": 0.0}]
    arena = project({**_row(3.33, 3.1, "nwps_height_ww3_dir"), "spot_id": 1})
    forecasts = {1: {"latest": arena, "plus24": arena}}
    norcal = next(r for r in D.REGIONS if r.key == "norcal")
    fake = _FakeAnthropic()
    row = D.build_region_report(norcal, spots, forecasts, fake)
    assert row is not None and row["top_spots"][0]["slug"] == "point-arena", row
    user = fake.sent[0]["messages"][0]["content"]
    assert "· 3.3ft swell height (calibrated to CDIP) @" in _line(user, "Point Arena"), user
    # What is STORED is pinned field by field in section 6.


# --------------------------------------------------------------------------- #
# 4 — THE GUARD: a model estimate is never tagged calibrated or CDIP           #
# --------------------------------------------------------------------------- #
def test_no_model_estimate_row_is_ever_tagged_calibrated_or_cdip():
    heights = [0.0, 0.01, 0.2, 0.5, 1.0, 2.44, 3.28, 4.0, 5.12, 9.99, 12.3, 30.0]
    checked = 0
    for source in NOT_MOP_SOURCES:
        for h in heights:
            for raw in (h, None, float("nan"), float("inf")):
                row = {"face_ft": h, "face_ft_raw": raw, "swell_source": source}
                assert D.height_basis(row) == "model", row
                _, user = _sent([_spot("S", {**_row(h, raw, source)})])
                line = _line(user, "S")
                assert f"{h:.1f}ft swell height (model estimate)" in line, line
                assert "calibrated" not in line.lower() and "cdip" not in line.lower(), line
                checked += 1
    assert checked == len(NOT_MOP_SOURCES) * len(heights) * 4


# --------------------------------------------------------------------------- #
# 5 — the fetch asks for what height_basis reads                               #
# --------------------------------------------------------------------------- #
def test_the_fetch_selects_the_columns_the_basis_is_read_from():
    cols = [c.strip() for c in D._FCAST_COLS.split(",")]
    for col in ("face_ft", "face_ft_raw", "swell_source"):
        assert cols.count(col) == 1, (col, cols)


# --------------------------------------------------------------------------- #
# 6 — what is STORED: top_spots gains one field and keeps the other five       #
# --------------------------------------------------------------------------- #
# The five keys every top_spots entry carried before height_basis, in the order they are written.
TOP_SPOT_KEYS_BEFORE = ("name", "slug", "state", "stars", "face_ft")


def test_top_spots_store_the_basis_and_keep_every_existing_field_unchanged():
    """daily_reports.top_spots is stored, and read back by the report cards. The basis is ONE
    added key; the five before it are written exactly as they were — the same keys, the same
    values, off the same `latest` row.

    Six spots with distinct stars, so the ranking is fixed. Every spot's `plus24` row differs from
    its `latest` in height, stars AND basis, so a field read off the wrong row fails here.
    """
    cols = [c.strip() for c in D._FCAST_COLS.split(",")]
    project = lambda r: {c: r.get(c) for c in cols}      # noqa: E731

    def spot(i, slug, name):
        return {"id": i, "slug": slug, "name": name, "state": "California",
                "lat": 36.0 + i / 10.0, "lng": -122.0, "offshore_wind_deg": 90.0}

    spots = [spot(1, "point-arena", "Point Arena"), spot(2, "steamer-lane", "Steamer Lane"),
             spot(3, "asilomar-state-beach", "Asilomar State Beach"), spot(4, "a-mop-hour", "A MOP hour"),
             spot(5, "old-row", "Old row"), spot(6, "unrateable", "Unrateable")]
    latest = {
        1: _row(3.33, 3.1, "nwps_height_ww3_dir", stars=4.0),    # factor 0.9313 (09-01 file): calibrated, larger
        2: _row(2.31, 4.62, "nwps_height_ww3_dir", stars=3.5),   # factor 2.0: calibrated, smaller
        3: _row(5.12, 5.12, "ww3", stars=3.0),                   # MOP tier, model row: model
        4: _row(3.28, 3.28, "cdip_mop", stars=2.5),              # MOP-fed: cdip
        5: _row(4.0, None, "nwps_height_ww3_dir", stars=2.0),    # before migration 017: model
        6: _row(None, None, "none", stars=0.0),                  # no height: no basis
    }
    plus24 = _row(9.99, 9.99, "cdip_mop", stars=1.0)
    forecasts = {i: {"latest": project({**r, "spot_id": i}), "plus24": project({**plus24, "spot_id": i})}
                 for i, r in latest.items()}
    norcal = next(r for r in D.REGIONS if r.key == "norcal")
    row = D.build_region_report(norcal, spots, forecasts, _FakeAnthropic())
    stored = row["top_spots"]

    # EVERY EXISTING FIELD UNCHANGED — the five keys and their values, written out.
    assert [{k: e[k] for k in TOP_SPOT_KEYS_BEFORE} for e in stored] == [
        {"name": "Point Arena", "slug": "point-arena", "state": "California", "stars": 4.0, "face_ft": 3.33},
        {"name": "Steamer Lane", "slug": "steamer-lane", "state": "California", "stars": 3.5, "face_ft": 2.31},
        {"name": "Asilomar State Beach", "slug": "asilomar-state-beach", "state": "California",
         "stars": 3.0, "face_ft": 5.12},
        {"name": "A MOP hour", "slug": "a-mop-hour", "state": "California", "stars": 2.5, "face_ft": 3.28},
        {"name": "Old row", "slug": "old-row", "state": "California", "stars": 2.0, "face_ft": 4.0},
        {"name": "Unrateable", "slug": "unrateable", "state": "California", "stars": 0.0, "face_ft": None},
    ], stored
    # ONE NEW FIELD, after them, and nothing else.
    assert [list(e) for e in stored] == [[*TOP_SPOT_KEYS_BEFORE, "height_basis"]] * 6, stored
    # ... holding the basis of the row face_ft came from: the strings the frontend accepts, or
    # None where there is no height to describe.
    assert [e["height_basis"] for e in stored] == \
        ["calibrated", "calibrated", "model", "cdip", "model", None], stored
    # It survives the trip through JSON (the column is JSONB) unchanged.
    assert json.loads(json.dumps(stored)) == stored
    # And the upserted row's own keys are the same seven.
    assert sorted(row) == ["generated_at", "region", "region_label", "report_date", "summary",
                           "top_spots", "trend"], sorted(row)


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} daily-report height-basis checks passed")


if __name__ == "__main__":
    _run_all()
