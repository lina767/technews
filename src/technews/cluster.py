"""Novelty agent: cluster near-duplicate items and score how *new* each story is.

"Is this already everywhere?" is answered structurally: the more independent
domains carry the same story, the less novel it is. Novelty is tier-weighted so
that a story broken by a primary source stays novel even once the trade press
piles on — the primary break is the new thing, the pickups are amplification.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from rapidfuzz import fuzz

from .models import Cluster, Item

# Title similarity at/above this (0..100) merges two items into one story.
_SIMILARITY_THRESHOLD = 82
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "with", "as",
    "is", "are", "be", "by", "at", "from", "new", "says", "amid", "over", "its",
}


def _norm(title: str) -> str:
    tokens = [t for t in _TOKEN_RE.findall(title.lower()) if t not in _STOP]
    return " ".join(tokens)


def cluster_items(items: list[Item]) -> list[Cluster]:
    """Greedy single-pass clustering by normalized-title similarity."""
    clusters: list[tuple[str, Cluster]] = []  # (normalized rep title, cluster)
    for item in items:
        key = _norm(item.title)
        if not key:
            clusters.append((key, Cluster(items=[item])))
            continue
        best: Cluster | None = None
        best_score = 0.0
        for rep_key, cluster in clusters:
            score = fuzz.token_set_ratio(key, rep_key)
            if score > best_score:
                best_score, best = score, cluster
        if best is not None and best_score >= _SIMILARITY_THRESHOLD:
            best.items.append(item)
        else:
            clusters.append((key, Cluster(items=[item])))
    return [c for _, c in clusters]


def score_novelty(
    clusters: list[Cluster],
    first_seen: dict[str, datetime],
    recency_days: int = 7,
) -> None:
    """Set ``cluster.novelty`` in place (0..1)."""
    now = datetime.now(timezone.utc)
    for cluster in clusters:
        rep = cluster.representative
        # Recency: use the earliest first_seen across the cluster's items.
        seens = [first_seen.get(i.id) for i in cluster.items]
        earliest = min((s for s in seens if s), default=rep.published or rep.fetched_at)
        age_days = max(0.0, (now - earliest).total_seconds() / 86400.0)
        recency = max(0.0, 1.0 - age_days / max(1, recency_days))

        # Spread: independent domains beyond the origin = amplification.
        amplification = max(0, len(cluster.source_domains) - 1)
        origin_weight = rep.tier_weight
        spread = origin_weight / (origin_weight + 0.5 * amplification)

        cluster.novelty = round(max(0.0, min(1.0, recency * spread)), 3)
