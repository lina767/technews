from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from technews.models import Item

NOW = datetime.now(timezone.utc)


def make_item(title, url, tier="secondary", topics=None, summary="", age_hours=2):
    return Item(
        source_name=f"src-{tier}",
        source_tier=tier,
        title=title,
        url=url,
        summary=summary,
        published=NOW - timedelta(hours=age_hours),
        topics=topics or [],
    )


@pytest.fixture
def settings():
    return {
        "top_n": 3,
        "max_items_per_day": 50,
        "recency_days": 7,
        "weights": {"relevance": 0.45, "novelty": 0.35, "carousel": 0.20},
        "topics": {
            "eu-chips-act": ["chips act", "fab", "foundry"],
            "semiconductor-industry": ["semiconductor", "tsmc", "asml"],
            "us-tech-policy": ["export control", "white house", "commerce"],
        },
        "models": {"scorer": "claude-haiku-4-5", "editor": "claude-sonnet-5"},
        "email": {"from": "x@resend.dev", "to": "me@example.com",
                  "subject_prefix": "Test"},
    }


@pytest.fixture
def theses():
    return [
        {"id": "sovereignty", "claim": "EU sovereignty is underfunded.",
         "keywords": ["chips act", "subsidy", "funding"]},
        {"id": "decoupling", "claim": "Export controls accelerate decoupling.",
         "keywords": ["export control", "china", "decoupling"]},
    ]
