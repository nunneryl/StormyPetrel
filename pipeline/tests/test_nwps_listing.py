"""The NWPS fetcher tells "not there" from "no answer", and never falls back on "no answer".

THE DEFECT. A listing request that failed and a folder with no cycle in it looked the same:
_get_text returned None, _list_wfo_cycles turned it into [] ("this office has not run today"),
lru_cache kept the [] for the whole run, and candidate_cycles fell back to yesterday. In the
7 days to 2026-09-23 that happened to sju four times — each a 60 s read timeout on the first
NOMADS request after sgx's seven-minute extraction — and yesterday's 18Z then overwrote about
five days of newer sju forecast.

THE RULE THESE TESTS HOLD.
  * 200 is LISTED. 403 and 404 are ABSENT: NOMADS answers 403, not 404, for a path that does
    not exist, so on either one falling back to the previous day is correct.
  * Everything else — a timeout, a connection error, a 5xx, any other status — is UNKNOWN. It
    is retried on a FRESH connection, never cached, and if still unknown the office is
    skipped for the run instead of being handed an older cycle.
  * Every hour a cycle yields is stamped with that cycle, for migration 019's trigger.

The sju incident is replayed end to end below. Every expected value is written by hand.
"""
from __future__ import annotations

import pytest
import requests

from pipeline.forecast import nwps
from pipeline.forecast.nwps import ABSENT, LISTED, UNKNOWN, CycleListing, Listing
from pipeline.tests.test_nwps_domain_guard import _grid, _run_fetch, _spot


# --------------------------------------------------------------------------- #
# A fake NOMADS: the shared session and every fresh one answer from queues     #
# --------------------------------------------------------------------------- #

class _Resp:
    def __init__(self, status, text=""):
        self.status_code = status
        self.text = text


class _Session:
    def __init__(self, answers, name):
        self.answers = list(answers)
        self.name = name
        self.calls = []
        self.headers = {}
        self.closed = False

    def get(self, url, timeout=None, allow_redirects=True):
        self.calls.append((url, timeout))
        a = self.answers.pop(0)
        if isinstance(a, BaseException):
            raise a
        return a

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False


@pytest.fixture
def nomads(monkeypatch):
    """shared: the pooled session nwps.session() returns. fresh: queued answers for each new
    requests.Session() a retry opens, in order. Returns (shared, opened, slept)."""
    state = {"shared": _Session([], "shared"), "fresh": [], "opened": [], "slept": []}
    state["shared"].headers = {"User-Agent": "StormyPetrel-Pipeline/test"}

    def new_session():
        s = _Session(state["fresh"].pop(0), f"fresh{len(state['opened']) + 1}")
        state["opened"].append(s)
        return s

    monkeypatch.setattr(nwps, "session", lambda: state["shared"])
    monkeypatch.setattr(nwps.requests, "Session", new_session)
    monkeypatch.setattr(nwps, "_sleep", lambda s: state["slept"].append(s))
    nwps._clear_listing_caches()
    yield state
    nwps._clear_listing_caches()


URL = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/nwps/prod/sr.20260922/sju/"
LISTING = '<a href="../">Parent</a> <a href="00/">00/</a> <a href="06/">06/</a> <a href="12/">12/</a>'


# --------------------------------------------------------------------------- #
# 1 — each answer is classified, and only UNKNOWN is retried                   #
# --------------------------------------------------------------------------- #

def test_a_200_is_listed_and_asked_once(nomads):
    nomads["shared"].answers = [_Resp(200, LISTING)]
    got = nwps._fetch_listing(URL)
    assert got == Listing(LISTED, LISTING)
    assert nomads["opened"] == [] and nomads["slept"] == []


@pytest.mark.parametrize("status", [403, 404])
def test_403_and_404_mean_the_folder_is_not_there_and_are_not_retried(nomads, status):
    nomads["shared"].answers = [_Resp(status)]
    got = nwps._fetch_listing(URL)
    assert (got.state, got.detail) == (ABSENT, f"HTTP {status}")
    assert nomads["opened"] == [] and nomads["slept"] == []


def test_a_timeout_is_retried_on_fresh_connections_until_it_answers(nomads):
    nomads["shared"].answers = [requests.ReadTimeout("read timed out")]
    nomads["fresh"] = [[requests.ConnectionError("reset")], [_Resp(200, LISTING)]]
    got = nwps._fetch_listing(URL)
    assert got.state == LISTED
    assert [s.name for s in nomads["opened"]] == ["fresh1", "fresh2"]
    assert all(s.closed for s in nomads["opened"]), "a fresh session must be closed after use"
    assert nomads["opened"][0].headers == {"User-Agent": "StormyPetrel-Pipeline/test"}
    assert nomads["slept"] == [5.0, 15.0]
    assert len(nomads["shared"].calls) == 1, "a retry must never reuse the pooled session"


@pytest.mark.parametrize("answer", [_Resp(503), _Resp(500), _Resp(429), _Resp(401),
                                    requests.ReadTimeout("read timed out")])
def test_anything_but_200_403_404_is_unknown_and_stays_unknown(nomads, answer):
    nomads["shared"].answers = [answer]
    nomads["fresh"] = [[answer], [answer]]
    got = nwps._fetch_listing(URL)
    assert got.state == UNKNOWN
    assert len(nomads["opened"]) == 2          # three attempts in all
    assert nomads["slept"] == [5.0, 15.0]


# --------------------------------------------------------------------------- #
# 2 — nothing but a 200 is remembered                                          #
# --------------------------------------------------------------------------- #

def test_a_failed_office_listing_is_asked_again_and_a_200_is_kept(nomads):
    t = requests.ReadTimeout("read timed out")
    nomads["shared"].answers = [t, t, _Resp(200, LISTING)]
    nomads["fresh"] = [[t], [t], [t], [t]]
    first = nwps._list_wfo_cycles("sr", "20260922", "sju")
    second = nwps._list_wfo_cycles("sr", "20260922", "sju")
    assert (first.state, second.state) == (UNKNOWN, UNKNOWN)
    assert len(nomads["shared"].calls) == 2, "the first failure was cached"
    third = nwps._list_wfo_cycles("sr", "20260922", "sju")
    fourth = nwps._list_wfo_cycles("sr", "20260922", "sju")
    assert third == fourth == CycleListing(LISTED, ("12", "06", "00"))
    assert len(nomads["shared"].calls) == 3, "a LISTED answer must be served from the cache"


def test_an_absent_folder_is_not_cached_either(nomads):
    nomads["shared"].answers = [_Resp(403), _Resp(200, LISTING)]
    assert nwps._list_wfo_cycles("sr", "20260923", "sju").state == ABSENT
    assert nwps._list_wfo_cycles("sr", "20260923", "sju").cycles == ("12", "06", "00")


def test_an_unread_root_index_is_none_and_is_asked_again(nomads):
    t = requests.ReadTimeout("read timed out")
    root = '<a href="sr.20260922/">sr.20260922/</a> <a href="sr.20260923/">sr.20260923/</a>'
    nomads["shared"].answers = [t, _Resp(403), _Resp(200, root)]
    nomads["fresh"] = [[t], [t]]
    assert nwps._list_root_dates() is None                 # unknown after three attempts
    assert nwps._list_root_dates() is None                 # a root that answers 403 is wrong
    assert nwps._list_root_dates() == {"sr": ["20260923", "20260922"]}


# --------------------------------------------------------------------------- #
# 3 — the decision: fall back on ABSENT, skip on UNKNOWN                       #
# --------------------------------------------------------------------------- #

TODAY, YESTERDAY, BEFORE = "20260923", "20260922", "20260921"


@pytest.fixture
def listings(monkeypatch):
    """Drive candidate_cycles from a table of (date -> CycleListing), recording lookups."""
    table = {}
    asked = []

    def fake(region, date_ymd, wfo):
        asked.append(date_ymd)
        return table[date_ymd]

    monkeypatch.setattr(nwps, "_list_root_dates", lambda: {"sr": [TODAY, YESTERDAY, BEFORE]})
    monkeypatch.setattr(nwps, "_list_wfo_cycles", fake)
    return table, asked


def full(*hhs):
    return CycleListing(LISTED, tuple(hhs))


def test_today_listed_gives_todays_cycles_newest_first(listings):
    table, _ = listings
    table.update({TODAY: full("12", "06", "00"), YESTERDAY: full("18", "12", "06", "00")})
    assert nwps.candidate_cycles("sju") == [(TODAY, "12"), (TODAY, "06"), (TODAY, "00"),
                                            (YESTERDAY, "18")]


@pytest.mark.parametrize("today", [CycleListing(ABSENT, detail="HTTP 403"),
                                   CycleListing(ABSENT, detail="HTTP 404"),
                                   CycleListing(LISTED, ())])
def test_a_folder_not_there_or_empty_falls_back_to_yesterday_which_is_correct(listings, today):
    table, _ = listings
    table.update({TODAY: today, YESTERDAY: full("18", "12", "06", "00")})
    assert nwps.candidate_cycles("sju") == [(YESTERDAY, "18"), (YESTERDAY, "12"),
                                            (YESTERDAY, "06"), (YESTERDAY, "00")]


def test_an_unanswered_today_skips_the_office_and_never_asks_about_yesterday(listings):
    table, asked = listings
    table.update({TODAY: CycleListing(UNKNOWN, detail="ReadTimeout"),
                  YESTERDAY: full("18", "12", "06", "00")})
    assert nwps.candidate_cycles("sju") is None
    assert asked == [TODAY]


def test_an_unanswered_day_after_a_folder_not_there_still_skips(listings):
    table, _ = listings
    table.update({TODAY: CycleListing(ABSENT, detail="HTTP 403"),
                  YESTERDAY: CycleListing(UNKNOWN, detail="HTTP 503")})
    assert nwps.candidate_cycles("sju") is None


def test_an_unanswered_older_day_after_a_newer_cycle_keeps_the_newer_cycle(listings):
    table, asked = listings
    table.update({TODAY: full("00"), YESTERDAY: CycleListing(UNKNOWN, detail="HTTP 503")})
    assert nwps.candidate_cycles("sju") == [(TODAY, "00")]
    assert asked == [TODAY, YESTERDAY]


def test_an_unread_root_skips_and_a_region_nomads_does_not_list_is_empty(monkeypatch):
    monkeypatch.setattr(nwps, "_list_root_dates", lambda: None)
    assert nwps.candidate_cycles("sju") is None
    monkeypatch.setattr(nwps, "_list_root_dates", lambda: {"er": [TODAY]})
    assert nwps.candidate_cycles("sju") == []


def test_a_skipped_office_downloads_nothing(monkeypatch):
    downloads = []
    monkeypatch.setattr(nwps, "candidate_cycles", lambda wfo: None)
    monkeypatch.setattr(nwps, "_download_filtered", lambda *a: downloads.append(a) or True)
    assert nwps._locate_cycle("sju", use_cache=False) is None
    assert downloads == []


def test_the_sju_incident_now_gets_todays_cycle_not_yesterdays_18z(nomads, monkeypatch):
    """09-22 22:43: the sju listing timed out and the run used 09-21 18Z. Replayed: the same
    timeout, then the retry on a fresh connection answers, and the newest candidate is the
    day's own 12Z. 09-21 18Z stays in the list only as the last download fallback."""
    root = '<a href="sr.20260922/">x</a> <a href="sr.20260921/">x</a>'
    nomads["shared"].answers = [_Resp(200, root), requests.ReadTimeout("read timed out"),
                                _Resp(200, '<a href="18/">18/</a>')]
    nomads["fresh"] = [[_Resp(200, LISTING)]]
    got = nwps.candidate_cycles("sju")
    assert got == [("20260922", "12"), ("20260922", "06"), ("20260922", "00"),
                   ("20260921", "18")], got


# --------------------------------------------------------------------------- #
# 4 — every hour carries its cycle                                             #
# --------------------------------------------------------------------------- #

def test_the_cycle_timestamp_is_the_nominal_time():
    assert nwps.cycle_timestamp("20260922", "12") == "2026-09-22T12:00:00Z"
    assert nwps.cycle_timestamp("20261231", "18") == "2026-12-31T18:00:00Z"


def test_fetch_stamps_every_extracted_hour_with_its_cycle():
    """_run_fetch stubs _locate_cycle to (…, "20260824", "12"); everything else runs."""
    spot = _spot("Avila Beach", 35.10, -120.82, baked=(35.15, -120.78))
    result, _seen, _cap = _run_fetch([spot], "stamp.json", _grid())
    hours = result["Avila Beach"]
    assert hours and all(h["nwps_cycle"] == "2026-08-24T12:00:00Z" for h in hours), hours
