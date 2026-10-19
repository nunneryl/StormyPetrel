"""Swell windows held at their stored values: the ray-cast does not write these spots.

The list lives in pipeline/data/spot_swell_window_holds.json (SPOT_SWELL_WINDOW_HOLDS_FILE),
each entry the arcs and optimal_swell_dir the spot carried when it was held, with the reason
in the file's note. Both writers of a ray-cast window apply it: sw1_raycast's full run
(_apply_raycast_result) and enrich (Algo 2a), so a held spot keeps its window through a full
ray-cast and through a re-enrich alike.

A hold is a decision about ten spots' ratings, so a file that cannot be read is an error, not
"no holds": letting a full run go ahead without it would quietly open every held window.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..config import SPOT_SWELL_WINDOW_HOLDS_FILE

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slug(name: str | None) -> str:
    """The roster slug, as pipeline/db_import._slugify makes it."""
    return _SLUG_RE.sub("-", (name or "").lower()).strip("-")


def load_holds(path: Path = SPOT_SWELL_WINDOW_HOLDS_FILE) -> dict[str, dict]:
    """{slug: {"name", "swell_window_arcs", "optimal_swell_dir"}}; {} when the file is absent.

    Raises ValueError on a file that does not parse or an entry without arcs or an optimal."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{path}: not valid JSON ({e}); the held windows cannot be applied") from e
    holds = data.get("holds") or {}
    for key, rec in holds.items():
        arcs = rec.get("swell_window_arcs") if isinstance(rec, dict) else None
        if not arcs or not isinstance(rec.get("optimal_swell_dir"), (int, float)):
            raise ValueError(f"{path}: hold {key!r} needs swell_window_arcs and optimal_swell_dir")
    return holds


def apply_hold(spot: dict, holds: dict[str, dict]) -> bool:
    """Write the held arcs and optimal onto *spot* in place when it is held; True if it was."""
    rec = holds.get(slug(spot.get("name")))
    if rec is None:
        return False
    spot["swell_window_arcs"] = [dict(a) for a in rec["swell_window_arcs"]]
    spot["optimal_swell_dir"] = rec["optimal_swell_dir"]
    return True
