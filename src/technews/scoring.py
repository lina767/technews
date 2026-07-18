"""Scoring agent.

A ``Scorer`` turns a cluster into a :class:`~technews.models.Score` on three
1..5 dimensions — relevance, novelty, carousel-worthiness — plus a normalized
composite. Two implementations share one interface:

* :class:`HeuristicScorer` — deterministic, keyword/signal based, needs no key.
* :class:`ClaudeScorer`   — Claude-backed, richer judgement, used when
  ``ANTHROPIC_API_KEY`` is set. Falls back to the heuristic on any error.

Per the research, LLMs over-rate absolute scores but rank well, so the pipeline
ranks by the composite rather than thresholding — the scorer just needs to be
*consistent*, and the heuristic is exactly that.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Protocol

from .models import Cluster, Score

log = logging.getLogger("technews.scoring")

_NUM_RE = re.compile(r"(\$|€|£|\d+\s?%|\b\d[\d,.]*\b|\b(?:billion|million|trillion)\b)")
_ENTITY_RE = re.compile(r"\b[A-Z][A-Za-z0-9.&-]+\b")
_CONFLICT = {
    "ban", "banned", "block", "blocks", "sanction", "sanctions", "fine", "fined",
    "curb", "curbs", "restrict", "restriction", "dispute", "clash", "crackdown",
    "probe", "lawsuit", "war", "threat", "retaliation", "tariff", "tariffs",
    "veto", "reject", "warns", "warning", "breach", "violation",
}


def compute_composite(relevance: float, novelty: float, carousel: float,
                      weights: dict) -> float:
    """Weighted sum of the three 1..5 dimensions, normalized to 0..1."""
    total = weights["relevance"] + weights["novelty"] + weights["carousel"]
    raw = (
        weights["relevance"] * relevance
        + weights["novelty"] * novelty
        + weights["carousel"] * carousel
    ) / max(total, 1e-9)
    return round((raw - 1.0) / 4.0, 4)  # map 1..5 -> 0..1


class Scorer(Protocol):
    def score(self, cluster: Cluster) -> Score: ...


class HeuristicScorer:
    """Deterministic scoring — the always-available baseline."""

    def __init__(self, settings: dict) -> None:
        self.weights = settings["weights"]
        # Flatten topic -> keywords for relevance matching.
        self.topic_keywords: dict[str, list[str]] = {
            topic: [k.lower() for k in kws]
            for topic, kws in settings.get("topics", {}).items()
        }

    def score(self, cluster: Cluster) -> Score:
        rep = cluster.representative
        text = f"{rep.title} {rep.summary}".lower()

        # Relevance: keyword hits across topics + breadth of topics matched.
        hits = 0
        topics_matched = 0
        for keywords in self.topic_keywords.values():
            topic_hits = sum(text.count(kw) for kw in keywords)
            if topic_hits:
                topics_matched += 1
                hits += topic_hits
        relevance = 1.0 + min(3.0, 0.75 * hits) + min(1.0, 0.5 * topics_matched)
        if rep.source_tier == "primary":
            relevance += 0.3
        relevance = _clamp(relevance)

        # Carousel-worthiness: concrete numbers, named entities, conflict.
        numbers = len(_NUM_RE.findall(text))
        entities = len(set(_ENTITY_RE.findall(rep.title)))
        conflict = sum(1 for w in _CONFLICT if w in text)
        carousel = _clamp(
            1.0
            + min(2.0, 0.5 * numbers)
            + min(1.0, 0.25 * entities)
            + min(1.5, 0.75 * conflict)
        )

        # Novelty: structural cluster novelty (0..1) mapped onto 1..5.
        novelty = _clamp(1.0 + 4.0 * cluster.novelty)

        composite = compute_composite(relevance, novelty, carousel, self.weights)
        rationale = (
            f"{topics_matched} topic(s), {numbers} figure(s), {conflict} conflict "
            f"cue(s); {len(cluster.source_domains)} source(s)."
        )
        return Score(relevance, novelty, carousel, composite, rationale, "heuristic")


_SYSTEM_PROMPT = """You score tech-politics news items for a creator who publishes \
social-media carousels. Rate each item 1-5 on three axes, using these journalistic \
news values: timeliness, impact, controversy, and generalizability (understandable \
to a general audience).

- relevance: fit to EU/US tech policy, semiconductors, AI regulation & industrial \
policy, and digital sovereignty. 5 = squarely on-topic and consequential.
- novelty: is this genuinely new, or already everywhere? Consider that it is carried \
by the given number of independent sources. 5 = a fresh break; 1 = saturated coverage.
- carousel: is it carousel-worthy — concrete numbers, named actors, a clear conflict \
or stakes that carry a visual story? 5 = a strong hook.

Reply with ONLY a JSON object: {"relevance": n, "novelty": n, "carousel": n, \
"rationale": "one short sentence"}."""


class ClaudeScorer:
    """Claude-backed scorer; blends the model's novelty with structural novelty."""

    def __init__(self, settings: dict) -> None:
        import anthropic  # local import: only needed on this path

        self.settings = settings
        self.weights = settings["weights"]
        self.model = settings["models"]["scorer"]
        self.client = anthropic.Anthropic()
        self.fallback = HeuristicScorer(settings)

    def score(self, cluster: Cluster) -> Score:
        rep = cluster.representative
        user = (
            f"Item title: {rep.title}\n"
            f"Summary: {rep.summary or '(none)'}\n"
            f"Topics: {', '.join(cluster.topics) or '(untagged)'}\n"
            f"Independent sources covering it: {len(cluster.source_domains)}\n"
            f"Structural novelty (0-1): {cluster.novelty}"
        )
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=256,
                system=[{"type": "text", "text": _SYSTEM_PROMPT,
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
            )
            data = _extract_json(resp.content)
            relevance = _clamp(float(data["relevance"]))
            carousel = _clamp(float(data["carousel"]))
            # Blend model novelty with the structural signal 50/50.
            model_novelty = _clamp(float(data["novelty"]))
            structural = 1.0 + 4.0 * cluster.novelty
            novelty = round((model_novelty + structural) / 2.0, 2)
            composite = compute_composite(relevance, novelty, carousel, self.weights)
            return Score(relevance, novelty, carousel, composite,
                         str(data.get("rationale", "")), "claude")
        except Exception as exc:  # noqa: BLE001 — degrade to heuristic, never crash
            log.warning("Claude scoring failed (%s); using heuristic", exc)
            return self.fallback.score(cluster)


def _clamp(value: float, lo: float = 1.0, hi: float = 5.0) -> float:
    return round(max(lo, min(hi, value)), 2)


def _extract_json(content: list) -> dict:
    text = "".join(getattr(b, "text", "") for b in content)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return json.loads(match.group(0) if match else text)


def get_scorer(settings: dict) -> Scorer:
    """ClaudeScorer when a key is available and importable, else HeuristicScorer."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return ClaudeScorer(settings)
        except Exception as exc:  # noqa: BLE001
            log.warning("Claude unavailable (%s); using heuristic scorer", exc)
    return HeuristicScorer(settings)
