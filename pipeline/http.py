"""Shared HTTP session with retry/backoff for all API calls."""
from __future__ import annotations

import logging
from typing import Any

import requests
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import USER_AGENT

log = logging.getLogger(__name__)

_session: requests.Session | None = None


def session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"})
        _session = s
    return _session


class RetryableHTTPError(Exception):
    """Raised for HTTP responses worth retrying (429 / 5xx)."""


_RETRY = retry(
    reraise=True,
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    retry=retry_if_exception_type((RetryableHTTPError, requests.ConnectionError, requests.Timeout)),
    before_sleep=before_sleep_log(log, logging.WARNING),
)


@_RETRY
def request(method: str, url: str, **kwargs: Any) -> requests.Response:
    """Perform an HTTP request, raising RetryableHTTPError on 429/5xx so tenacity retries."""
    kwargs.setdefault("timeout", 180)
    resp = session().request(method, url, **kwargs)
    if resp.status_code == 429 or 500 <= resp.status_code < 600:
        raise RetryableHTTPError(f"{resp.status_code} on {url}: {resp.text[:200]}")
    resp.raise_for_status()
    return resp


def get(url: str, **kwargs: Any) -> requests.Response:
    return request("GET", url, **kwargs)


def get_once(url: str, **kwargs: Any) -> requests.Response:
    """SINGLE-attempt GET — no retry, no backoff (bypasses _RETRY). For endpoints where a slow or
    dead upstream must fast-fail on an explicit short timeout instead of stalling through the
    4x-exponential-backoff path (e.g. the tide stage's per-station cap — see forecast/tides.py, and
    the pattern documented in .github/workflows/reverify-trust-accumulate.yml). Caller SHOULD pass a
    short `timeout`. Does not raise on 4xx/5xx (returns the Response so the caller can classify a 5xx
    as an outage vs a 4xx as a data error); raises requests transport exceptions directly."""
    kwargs.setdefault("timeout", 30)
    return session().request("GET", url, **kwargs)


def post(url: str, **kwargs: Any) -> requests.Response:
    return request("POST", url, **kwargs)
