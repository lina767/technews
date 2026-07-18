from __future__ import annotations

from technews.cluster import cluster_items, score_novelty
from technews.scoring import HeuristicScorer, compute_composite
from technews.models import Cluster

from .conftest import NOW, make_item


def _cluster(item):
    cl = Cluster(items=[item])
    score_novelty([cl], {item.id: NOW}, recency_days=7)
    return cl


def test_heuristic_is_deterministic(settings):
    scorer = HeuristicScorer(settings)
    item = make_item("TSMC delays fab amid export control dispute; $5 billion at stake",
                     "https://x.com/1", topics=["semiconductor-industry"])
    a = scorer.score(_cluster(item))
    b = scorer.score(_cluster(item))
    assert (a.relevance, a.novelty, a.carousel, a.composite) == \
           (b.relevance, b.novelty, b.carousel, b.composite)
    assert a.scorer == "heuristic"


def test_on_topic_scores_higher_than_off_topic(settings):
    scorer = HeuristicScorer(settings)
    on = scorer.score(_cluster(make_item(
        "Commerce Department tightens semiconductor export control on TSMC",
        "https://x.com/on", topics=["semiconductor-industry"])))
    off = scorer.score(_cluster(make_item(
        "Local bakery wins award for sourdough", "https://x.com/off")))
    assert on.relevance > off.relevance
    assert on.composite > off.composite


def test_conflict_and_numbers_raise_carousel(settings):
    scorer = HeuristicScorer(settings)
    punchy = scorer.score(_cluster(make_item(
        "EU fines chipmaker €800 million in sanctions crackdown",
        "https://x.com/p")))
    dull = scorer.score(_cluster(make_item(
        "Committee to hold routine meeting next week", "https://x.com/d")))
    assert punchy.carousel > dull.carousel


def test_composite_bounds():
    assert compute_composite(1, 1, 1, {"relevance": 1, "novelty": 1, "carousel": 1}) == 0.0
    assert compute_composite(5, 5, 5, {"relevance": 1, "novelty": 1, "carousel": 1}) == 1.0
