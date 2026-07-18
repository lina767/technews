from __future__ import annotations

from technews.models import Cluster
from technews.thesis import HeuristicMatcher

from .conftest import make_item


def test_matches_relevant_thesis(theses):
    matcher = HeuristicMatcher(theses)
    cl = Cluster(items=[make_item(
        "New US export control targets China chip imports",
        "https://x.com/1", summary="export control decoupling china")])
    match = matcher.match(cl)
    assert match is not None
    assert match.thesis_id == "decoupling"
    assert match.relation == "neutral"  # offline matcher does not assess stance


def test_no_match_returns_none(theses):
    matcher = HeuristicMatcher(theses)
    cl = Cluster(items=[make_item("Unrelated sports headline", "https://x.com/2")])
    assert matcher.match(cl) is None
