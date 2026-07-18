"""Thesis agent: match each candidate against the user's own arguments.

Every surfaced item is checked against `theses.yaml` — does it support,
contradict, extend, or stay neutral to one of the user's standing claims? This
is what turns a generic feed into a personal one: the item that speaks to a
thesis you're already pushing is the one worth a carousel.

HeuristicMatcher (keyword overlap) always works; ClaudeMatcher adds judgement
when a key is present and degrades to the heuristic on error.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Protocol

import yaml

from .models import Cluster, ThesisMatch

log = logging.getLogger("technews.thesis")

_WORD_RE = re.compile(r"[a-z0-9]+")


def load_theses(path: str | Path) -> list[dict]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return data.get("theses", [])


class Matcher(Protocol):
    def match(self, cluster: Cluster) -> ThesisMatch | None: ...


class HeuristicMatcher:
    """Pick the thesis with the most keyword overlap; relation stays neutral.

    Keyword overlap can tell us a thesis is *relevant*, but not whether an item
    supports or contradicts it — so the offline matcher honestly reports
    ``neutral`` and leaves the stance to the Claude matcher.
    """

    def __init__(self, theses: list[dict]) -> None:
        self.theses = theses

    def match(self, cluster: Cluster) -> ThesisMatch | None:
        rep = cluster.representative
        haystack = f"{rep.title} {rep.summary}".lower()
        best: dict | None = None
        best_hits = 0
        for thesis in self.theses:
            keywords = [k.lower() for k in thesis.get("keywords", [])]
            hits = sum(1 for kw in keywords if kw in haystack)
            if hits > best_hits:
                best_hits, best = hits, thesis
        if not best or best_hits == 0:
            return None
        return ThesisMatch(
            thesis_id=best["id"],
            thesis_claim=_short(best["claim"]),
            relation="neutral",
            note=f"Keyword overlap ({best_hits}); stance not assessed offline.",
            paper_url=best.get("paper_url", ""),
        )


_SYSTEM_PROMPT = """You match tech-politics news against a user's standing theses. \
Given the item and a numbered list of theses, pick the single most relevant thesis \
and judge how the item relates to it:
- supports: the item is evidence FOR the thesis
- contradicts: the item is evidence AGAINST the thesis
- extends: related and enriches it, without clearly confirming or refuting
- neutral: touches the topic but does not bear on the claim
If no thesis is relevant, return index 0.

Reply with ONLY JSON: {"index": n, "relation": "supports|contradicts|extends|neutral", \
"note": "one short sentence"}. index is 1-based, or 0 for none."""


class ClaudeMatcher:
    def __init__(self, settings: dict, theses: list[dict]) -> None:
        import anthropic

        self.theses = theses
        self.model = settings["models"]["scorer"]
        self.client = anthropic.Anthropic()
        self.fallback = HeuristicMatcher(theses)
        self._thesis_block = "\n".join(
            f"{i}. {t['claim']}" for i, t in enumerate(theses, start=1)
        )

    def match(self, cluster: Cluster) -> ThesisMatch | None:
        if not self.theses:
            return None
        rep = cluster.representative
        user = (
            f"Theses:\n{self._thesis_block}\n\n"
            f"Item title: {rep.title}\nSummary: {rep.summary or '(none)'}"
        )
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=200,
                system=[{"type": "text", "text": _SYSTEM_PROMPT,
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
            )
            data = _extract_json(resp.content)
            idx = int(data.get("index", 0))
            if idx < 1 or idx > len(self.theses):
                return None
            thesis = self.theses[idx - 1]
            relation = str(data.get("relation", "neutral")).lower()
            if relation not in {"supports", "contradicts", "extends", "neutral"}:
                relation = "neutral"
            return ThesisMatch(
                thesis_id=thesis["id"],
                thesis_claim=_short(thesis["claim"]),
                relation=relation,
                note=str(data.get("note", "")),
                paper_url=thesis.get("paper_url", ""),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Claude thesis match failed (%s); using heuristic", exc)
            return self.fallback.match(cluster)


def _short(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _extract_json(content: list) -> dict:
    text = "".join(getattr(b, "text", "") for b in content)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return json.loads(match.group(0) if match else text)


def get_matcher(settings: dict, theses: list[dict]) -> Matcher:
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return ClaudeMatcher(settings, theses)
        except Exception as exc:  # noqa: BLE001
            log.warning("Claude unavailable (%s); using heuristic matcher", exc)
    return HeuristicMatcher(theses)
