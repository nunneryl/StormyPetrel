"""--scope full refreshes EVERY spot page and every state's region page, changed or not.

On 2026-11-18 a full run refreshed only the 190 of 646 spot pages whose rating moved; the other
456 kept their last copy until someone visited, and that visitor was served it (Dillon Beach and
Scarborough Beach showed a cycle 30-36 h old while the pipeline had written today's). The diff is
still made, for the log. Expected paths are written out by hand.
"""
from __future__ import annotations

import json

from pipeline import revalidate as R

META = {
    1: {"slug": "dillon-beach", "state": "california"},
    2: {"slug": "sharp-park", "state": "california"},
    3: {"slug": "scarborough-beach", "state": "maine"},
    4: {"slug": None, "state": "oregon"},          # no slug: no path; its state still has a page
}
# Spot 1 moved a full star; spot 2 moved 0.1 ft (under the 0.3 ft tolerance); spot 3 has no
# current-hour rating at all.
NEW = {1: {"stars": 3.0, "face_ft": 4.0}, 2: {"stars": 2.0, "face_ft": 3.1}}
OLD = {"1": {"stars": 2.0, "face_ft": 4.0}, "2": {"stars": 2.0, "face_ft": 3.0}}

ALL = ["/", "/map", "/regions", "/region/california", "/region/maine", "/region/oregon",
       "/spot/dillon-beach", "/spot/scarborough-beach", "/spot/sharp-park"]


def _run(monkeypatch, tmp_path, snapshot=True):
    monkeypatch.setattr(R, "fetch_current_hour_ratings", lambda client: NEW)
    monkeypatch.setattr(R, "fetch_spot_meta", lambda client: META)
    path = tmp_path / "snap.json"
    if snapshot:
        path.write_text(json.dumps(OLD))
    return R.build_full_paths(object(), path)


def test_every_spot_and_region_page_is_refreshed(monkeypatch, tmp_path):
    paths, stats = _run(monkeypatch, tmp_path)
    assert paths == ALL
    # The diff still counts what moved: spot 1 changed, spot 2 did not; spot 3 has no rating.
    assert (stats["changed_spots"], stats["unchanged_spots"], stats["total_spots"]) == (1, 1, 2)
    assert stats["spot_paths"] == 3 and stats["fell_back_to_all"] is False


def test_an_unchanged_spot_is_refreshed_too(monkeypatch, tmp_path):
    paths, _ = _run(monkeypatch, tmp_path)
    assert "/spot/sharp-park" in paths            # its rating moved 0.1 ft: unchanged
    assert "/spot/scarborough-beach" in paths     # no current-hour rating at all


def test_no_snapshot_gives_the_same_paths(monkeypatch, tmp_path):
    paths, stats = _run(monkeypatch, tmp_path, snapshot=False)
    assert paths == ALL and stats["fell_back_to_all"] is True


def test_the_log_line_says_all_spot_pages(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("REVALIDATE_URL", "https://example.invalid/api/revalidate")
    monkeypatch.setenv("REVALIDATE_SECRET", "x")
    monkeypatch.setattr(R, "get_client", lambda: object())
    monkeypatch.setattr(R, "fetch_current_hour_ratings", lambda client: NEW)
    monkeypatch.setattr(R, "fetch_spot_meta", lambda client: META)
    sent = {}
    monkeypatch.setattr(R, "post_revalidate", lambda url, secret, paths: sent.update(paths=paths) or {"ok": True})
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps(OLD))
    caplog.set_level("INFO")
    assert R.cmd_revalidate("full", snap) == 0
    assert sent["paths"] == ALL
    assert "Full diff: 1/2 spots changed (1 unchanged), 1 states affected; refreshing all 3 spot pages" in caplog.text
