"""Core data structures for the pipeline.

The pipeline is a chain of named agents; each stage enriches a small set of
plain dataclasses. Keeping these dumb and serializable makes the store, the
renderer, and the tests trivial.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

# Tier -> default credibility/novelty weight. Primary sources break stories;
# secondary sources add breadth and confirmation but must not out-weigh them.
TIER_WEIGHTS: dict[str, float] = {
    "primary": 1.0,
    "institute": 0.85,
    "secondary": 0.6,
    "newsletter": 0.7,
}
TIER_RANK: dict[str, int] = {"primary": 0, "institute": 1, "newsletter": 2, "secondary": 3}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def domain_of(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


@dataclass
class Item:
    """A single article/document fetched from one source."""

    source_name: str
    source_tier: str
    title: str
    url: str
    summary: str = ""
    published: datetime | None = None
    topics: list[str] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=_now)

    @property
    def id(self) -> str:
        return hashlib.sha1(self.url.encode("utf-8")).hexdigest()[:16]

    @property
    def domain(self) -> str:
        return domain_of(self.url)

    @property
    def tier_weight(self) -> float:
        return TIER_WEIGHTS.get(self.source_tier, 0.5)


@dataclass
class Cluster:
    """A group of items covering the same story across sources.

    The representative item is the highest-tier, earliest one — that is the
    original break of the story, which is what "novelty" should reward.
    """

    items: list[Item]
    novelty: float = 0.0  # 0..1, computed by the Novelty agent

    @property
    def id(self) -> str:
        return min(item.id for item in self.items)

    @property
    def representative(self) -> Item:
        return min(
            self.items,
            key=lambda i: (
                TIER_RANK.get(i.source_tier, 9),
                i.published or i.fetched_at,
            ),
        )

    @property
    def source_domains(self) -> set[str]:
        return {i.domain for i in self.items}

    @property
    def tiers(self) -> set[str]:
        return {i.source_tier for i in self.items}

    @property
    def topics(self) -> list[str]:
        seen: dict[str, None] = {}
        for item in self.items:
            for topic in item.topics:
                seen.setdefault(topic, None)
        return list(seen)


@dataclass
class Score:
    """1..5 newsworthiness scores plus a normalized composite (0..1)."""

    relevance: float
    novelty: float
    carousel: float
    composite: float = 0.0
    rationale: str = ""
    scorer: str = "heuristic"  # "claude" | "heuristic"


@dataclass
class ThesisMatch:
    thesis_id: str
    thesis_claim: str
    relation: str  # supports | contradicts | extends | neutral
    note: str = ""


@dataclass
class Candidate:
    """A cluster carried through scoring + thesis matching."""

    cluster: Cluster
    score: Score
    thesis: ThesisMatch | None = None


@dataclass
class EditionItem:
    rank: int
    candidate: Candidate
    why_it_matters: str = ""
    carousel_hook: str = ""

    @property
    def item(self) -> Item:
        return self.candidate.cluster.representative


@dataclass
class Edition:
    date: str  # YYYY-MM-DD
    items: list[EditionItem]
    generated_at: datetime = field(default_factory=_now)
    editor: str = "heuristic"  # which editor produced the selection
