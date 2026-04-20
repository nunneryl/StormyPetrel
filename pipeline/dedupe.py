"""Two-pass deduplication: QID match, then spatial proximity + fuzzy name match."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from rapidfuzz import fuzz, utils

from .config import DEDUPE_DISTANCE_M, DEDUPE_NAME_SCORE, SOURCE_PRIORITY
from .geo import haversine_m

log = logging.getLogger(__name__)


@dataclass
class DedupeStats:
    candidates_in: int = 0
    clusters_out: int = 0
    merges_by_qid: int = 0
    merges_by_proximity: int = 0
    per_source: dict[str, int] = field(default_factory=dict)


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1
        return True


def _source_rank(source: str) -> int:
    try:
        return SOURCE_PRIORITY.index(source)
    except ValueError:
        return len(SOURCE_PRIORITY)


def _pick_name(cluster: list[dict]) -> str:
    sorted_c = sorted(cluster, key=lambda r: (_source_rank(r["source"]), -len(r.get("name") or "")))
    for r in sorted_c:
        name = (r.get("name") or "").strip()
        if name:
            return name
    return ""


def _pick_coords(cluster: list[dict]) -> tuple[float, float]:
    sorted_c = sorted(cluster, key=lambda r: _source_rank(r["source"]))
    best = sorted_c[0]
    return float(best["lat"]), float(best["lng"])


def _merge_sources(cluster: list[dict]) -> dict:
    merged = {"osm_id": None, "wikidata_id": None, "wikipedia_url": None}
    for r in cluster:
        for k, v in (r.get("source_ids") or {}).items():
            if v and not merged.get(k):
                merged[k] = v
    return merged


def _merge_tags(cluster: list[dict]) -> dict:
    out: dict = {}
    for r in sorted(cluster, key=lambda r: _source_rank(r["source"])):
        for k, v in (r.get("tags") or {}).items():
            if v is None or v == "":
                continue
            if k not in out:
                out[k] = v
            elif out[k] != v:
                # Namespace conflicts per-source so nothing is lost.
                out[f"{r['source']}_{k}"] = v
    return out


def _merge_region(cluster: list[dict]) -> str | None:
    for r in sorted(cluster, key=lambda r: _source_rank(r["source"])):
        region = r.get("region_hint")
        if region:
            return region
    return None


def _merge_cluster(cluster: list[dict]) -> dict:
    lat, lng = _pick_coords(cluster)
    return {
        "name": _pick_name(cluster),
        "lat": lat,
        "lng": lng,
        "sources": _merge_sources(cluster),
        "tags": _merge_tags(cluster),
        "region_hint": _merge_region(cluster),
    }


def merge(candidates: list[dict]) -> tuple[list[dict], DedupeStats]:
    stats = DedupeStats(candidates_in=len(candidates))
    for c in candidates:
        stats.per_source[c["source"]] = stats.per_source.get(c["source"], 0) + 1

    n = len(candidates)
    uf = _UnionFind(n)

    # Pass 1: shared Wikidata QID.
    qid_to_idx: dict[str, int] = {}
    for i, c in enumerate(candidates):
        qid = (c.get("source_ids") or {}).get("wikidata_id")
        if not qid:
            continue
        if qid in qid_to_idx:
            if uf.union(qid_to_idx[qid], i):
                stats.merges_by_qid += 1
        else:
            qid_to_idx[qid] = i

    # Pass 2: spatial + fuzzy, cross-source only, short-circuit on distance.
    for i in range(n):
        ci = candidates[i]
        for j in range(i + 1, n):
            cj = candidates[j]
            if ci["source"] == cj["source"]:
                continue
            if uf.find(i) == uf.find(j):
                continue
            d = haversine_m(ci["lat"], ci["lng"], cj["lat"], cj["lng"])
            if d >= DEDUPE_DISTANCE_M:
                continue
            score = fuzz.token_set_ratio(
                ci.get("name") or "",
                cj.get("name") or "",
                processor=utils.default_process,
            )
            if score >= DEDUPE_NAME_SCORE:
                if uf.union(i, j):
                    stats.merges_by_proximity += 1

    # Collect clusters.
    clusters: dict[int, list[dict]] = {}
    for i, c in enumerate(candidates):
        root = uf.find(i)
        clusters.setdefault(root, []).append(c)

    merged = [_merge_cluster(group) for group in clusters.values()]
    stats.clusters_out = len(merged)
    log.info(
        "Dedupe: %d candidates → %d clusters (qid=%d, proximity=%d)",
        stats.candidates_in,
        stats.clusters_out,
        stats.merges_by_qid,
        stats.merges_by_proximity,
    )
    return merged, stats
