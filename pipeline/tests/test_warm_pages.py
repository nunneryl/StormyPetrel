"""pipeline/warm_pages.py loads each refreshed page once, a few at a time, and never fails the run.

On 2026-11-19 Kanaha Beach and Hoʻokipa showed "Model run 31 h ago" to a visitor: the full runs
had refreshed the pages, but a refreshed page is rebuilt on its next request and that request
still gets the old copy. These tests run the module against a local HTTP server and check, by
hand-written expectations: each path is asked for exactly once, at most 4 at a time, a page that
errors is named but the exit is 0, and revalidate.py writes the paths only once the webhook
accepts them.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pipeline import revalidate as R
from pipeline import warm_pages as W


class Site:
    """A local server that records each GET, answers 500 for /broken, and holds each request
    for a moment so the concurrency is visible."""

    def __init__(self, hold_s=0.05):
        self.seen: list[str] = []
        self.agents: set[str] = set()
        self.live = 0
        self.peak = 0
        lock = threading.Lock()
        site = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                with lock:
                    site.seen.append(self.path)
                    site.agents.add(self.headers.get("User-Agent"))
                    site.live += 1
                    site.peak = max(site.peak, site.live)
                time.sleep(hold_s)
                with lock:
                    site.live -= 1
                code = 500 if self.path == "/broken" else 200
                self.send_response(code)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html>ok</html>")

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


PATHS = ["/", "/map", "/regions", "/region/hawaii", "/spot/kanaha-beach", "/spot/ho-okipa",
         "/spot/sharp-park", "/spot/scarborough-beach", "/spot/dillon-beach", "/spot/banzai-pipeline"]


def test_each_path_is_loaded_exactly_once_and_at_most_four_at_a_time(tmp_path):
    site = Site()
    try:
        f = tmp_path / "paths.json"
        f.write_text(json.dumps(PATHS))
        assert W.main([str(f)], env={"WARM_BASE_URL": site.base}) == 0
    finally:
        site.close()
    assert sorted(site.seen) == sorted(PATHS)            # 10 paths, 10 requests, none twice
    assert len(site.seen) == 10
    assert 1 < site.peak <= 4                             # in parallel, but never more than 4
    assert site.agents == {"StormyPetrel-Pipeline/0.1 (+https://stormypetrel.surf)"}


def test_a_page_that_errors_is_named_and_the_run_still_passes(tmp_path, capsys):
    site = Site(hold_s=0)
    try:
        f = tmp_path / "paths.json"
        f.write_text(json.dumps(["/spot/kanaha-beach", "/broken", "/spot/ho-okipa"]))
        assert W.main([str(f)], env={"WARM_BASE_URL": site.base}) == 0
    finally:
        site.close()
    out = capsys.readouterr().out
    assert "::warning::warm_pages: 1 of 3 pages did not load" in out
    assert "/broken HTTP 500" in out


def test_warm_counts_ok_and_failed_by_hand():
    answers = {"/a": None, "/b": "HTTP 503", "/c": None, "/d": "TimeoutError: timed out"}
    got = W.warm("https://x.invalid", list(answers), workers=2,
                 get=lambda url: answers[url.removeprefix("https://x.invalid")])
    assert got == {"ok": 2, "failed": [("/b", "HTTP 503"), ("/d", "TimeoutError: timed out")]}


def test_a_path_is_quoted_but_its_slashes_kept():
    asked = []
    W.warm("https://x.invalid", ["/spot/ho okipa"], get=lambda url: asked.append(url))
    assert asked == ["https://x.invalid/spot/ho%20okipa"]


def test_the_site_is_the_webhooks_scheme_and_host():
    assert W.base_url({"REVALIDATE_URL": "https://stormypetrel.surf/api/revalidate"}) == \
        "https://stormypetrel.surf"
    assert W.base_url({"REVALIDATE_URL": "https://stormypetrel.surf/api/revalidate",
                       "WARM_BASE_URL": "https://preview.example/"}) == "https://preview.example"
    assert W.base_url({"REVALIDATE_URL": "not a url"}) is None
    assert W.base_url({}) is None


def test_a_missing_paths_file_or_site_is_a_warning_not_a_failure(tmp_path, capsys):
    assert W.main([str(tmp_path / "none.json")], env={"WARM_BASE_URL": "http://127.0.0.1:9"}) == 0
    assert "::warning::warm_pages: could not read" in capsys.readouterr().out
    f = tmp_path / "paths.json"
    f.write_text("[\"/\"]")
    assert W.main([str(f)], env={}) == 0
    assert "neither WARM_BASE_URL nor REVALIDATE_URL is set" in capsys.readouterr().out


def test_an_unreachable_site_is_named_and_the_run_still_passes(tmp_path, capsys):
    f = tmp_path / "paths.json"
    f.write_text("[\"/\", \"/map\"]")
    # Port 9 (discard) on loopback: nothing listens, so each request is refused at once.
    assert W.main([str(f)], env={"WARM_BASE_URL": "http://127.0.0.1:9"}) == 0
    assert "::warning::warm_pages: 2 of 2 pages did not load" in capsys.readouterr().out


# ---- revalidate.py --paths-out ------------------------------------------------------------- #
META = {1: {"slug": "kanaha-beach", "state": "hawaii"}, 2: {"slug": "ho-okipa", "state": "hawaii"}}
WRITTEN = ["/", "/map", "/regions", "/region/hawaii", "/spot/ho-okipa", "/spot/kanaha-beach"]


def _revalidate(monkeypatch, tmp_path, answer):
    monkeypatch.setenv("REVALIDATE_URL", "https://example.invalid/api/revalidate")
    monkeypatch.setenv("REVALIDATE_SECRET", "x")
    monkeypatch.setattr(R, "get_client", lambda: object())
    monkeypatch.setattr(R, "fetch_current_hour_ratings", lambda client: {})
    monkeypatch.setattr(R, "fetch_spot_meta", lambda client: META)
    monkeypatch.setattr(R, "post_revalidate", lambda url, secret, paths: answer)
    out = tmp_path / "sub" / "paths.json"
    code = R.main(["--scope", "full", "--diff-from", str(tmp_path / "none.json"),
                   "--paths-out", str(out)])
    return code, out


def test_revalidate_writes_the_paths_once_the_webhook_takes_them(monkeypatch, tmp_path):
    code, out = _revalidate(monkeypatch, tmp_path, {"ok": True})
    assert code == 0
    assert json.loads(out.read_text()) == WRITTEN


def test_revalidate_writes_no_paths_when_the_webhook_refuses(monkeypatch, tmp_path):
    code, out = _revalidate(monkeypatch, tmp_path, {"ok": False, "error": "bad secret"})
    assert code == 1
    assert not out.exists()
