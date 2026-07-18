from __future__ import annotations

from datetime import timedelta

from technews.cluster import cluster_items, score_novelty
from technews.models import Item

from .conftest import NOW, make_item


def test_near_duplicate_titles_merge():
    items = [
        make_item("EU unveils Chips Act 2.0 to boost semiconductor output",
                  "https://a.eu/1", tier="primary"),
        make_item("EU Unveils Chips Act 2.0 To Boost Semiconductor Output",
                  "https://b.com/2", tier="secondary"),
        make_item("Totally different story about cloud sovereignty",
                  "https://c.com/3", tier="secondary"),
    ]
    clusters = cluster_items(items)
    assert len(clusters) == 2
    big = max(clusters, key=lambda c: len(c.items))
    assert len(big.items) == 2


def test_representative_prefers_primary_and_earliest():
    primary = make_item("Chips Act 2.0 proposal", "https://ec.europa.eu/x",
                        tier="primary", age_hours=5)
    secondary = make_item("Chips Act 2.0 proposal", "https://news.com/x",
                          tier="secondary", age_hours=1)
    clusters = cluster_items([secondary, primary])
    assert clusters[0].representative.source_tier == "primary"


def test_primary_origin_stays_more_novel_than_secondary_spread():
    first_seen = {}
    # Primary story carried by 3 domains.
    p_items = [
        make_item("Story A about chips", f"https://p{i}.eu/a", tier="primary")
        for i in range(1)
    ] + [
        make_item("Story A about chips", f"https://s{i}.com/a", tier="secondary")
        for i in range(2)
    ]
    # Secondary-only story carried by 3 domains.
    s_items = [
        make_item("Story B about chips", f"https://x{i}.com/b", tier="secondary")
        for i in range(3)
    ]
    primary_cluster = cluster_items(p_items)[0]
    secondary_cluster = cluster_items(s_items)[0]
    for it in p_items + s_items:
        first_seen[it.id] = NOW
    score_novelty([primary_cluster, secondary_cluster], first_seen, recency_days=7)
    assert primary_cluster.novelty > secondary_cluster.novelty


def test_older_stories_are_less_novel():
    fresh = cluster_items([make_item("X", "https://a.com/x")])[0]
    stale = cluster_items([make_item("Y", "https://a.com/y")])[0]
    first_seen = {
        fresh.items[0].id: NOW,
        stale.items[0].id: NOW - timedelta(days=6),
    }
    score_novelty([fresh, stale], first_seen, recency_days=7)
    assert fresh.novelty > stale.novelty
