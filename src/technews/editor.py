"""Editor agent — the stage with delegated autonomy.

Given the scored, thesis-matched candidates, the editor selects the daily
Top N, enforces topic diversity, and writes a short "why it matters" plus a
suggested carousel hook per pick. It is *bounded*: it works from the composite
ranking and never surfaces two items of the same story.

HeuristicEditor runs offline; ClaudeEditor delegates the judgement call to a
stronger model and falls back to the heuristic on any error.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Protocol

from .models import Candidate, EditionItem

log = logging.getLogger("technews.editor")

_RELATION_VERB = {
    "supports": "backs",
    "contradicts": "cuts against",
    "extends": "extends",
    "neutral": "touches",
}


def _lead_topic(candidate: Candidate) -> str:
    topics = candidate.cluster.topics
    return topics[0] if topics else "general"


def _pretty_topic(topic: str) -> str:
    return topic.replace("-", " ")


class Editor(Protocol):
    def select(self, candidates: list[Candidate], top_n: int) -> list[EditionItem]: ...


def _diversify(candidates: list[Candidate], top_n: int) -> list[Candidate]:
    """Rank by composite, then greedily prefer unused lead topics."""
    ranked = sorted(candidates, key=lambda c: c.score.composite, reverse=True)
    chosen: list[Candidate] = []
    used: set[str] = set()
    for cand in ranked:
        if len(chosen) >= top_n:
            break
        topic = _lead_topic(cand)
        if topic not in used:
            chosen.append(cand)
            used.add(topic)
    if len(chosen) < top_n:  # backfill from the rest, still by composite
        for cand in ranked:
            if len(chosen) >= top_n:
                break
            if cand not in chosen:
                chosen.append(cand)
    return chosen[:top_n]


class HeuristicEditor:
    def select(self, candidates: list[Candidate], top_n: int) -> list[EditionItem]:
        picks = _diversify(candidates, top_n)
        edition: list[EditionItem] = []
        for rank, cand in enumerate(picks, start=1):
            edition.append(
                EditionItem(
                    rank=rank,
                    candidate=cand,
                    why_it_matters=self._why(cand),
                    carousel_hook=self._hook(cand),
                )
            )
        return edition

    def _why(self, cand: Candidate) -> str:
        rep = cand.cluster.representative
        n = len(cand.cluster.source_domains)
        parts = [
            f"{rep.source_tier.capitalize()} source on {_pretty_topic(_lead_topic(cand))}",
            f"{n} independent source{'s' if n != 1 else ''}",
        ]
        if cand.thesis:
            verb = _RELATION_VERB.get(cand.thesis.relation, "touches")
            parts.append(f"{verb} your thesis “{cand.thesis.thesis_claim}”")
        return "; ".join(parts) + "."

    def _hook(self, cand: Candidate) -> str:
        rep = cand.cluster.representative
        return f"{rep.title} — why it matters for {_pretty_topic(_lead_topic(cand))}."


_SYSTEM_PROMPT = """You are the editor of a daily tech-politics brief for a creator \
who publishes social-media carousels. From the scored candidates, choose exactly the \
top {top_n}. Rules:
- Respect the composite ranking, but ensure topic diversity — avoid multiple picks on \
the same narrow topic unless one is clearly dominant.
- For each pick write "why_it_matters" (one crisp sentence on the stakes) and a \
"carousel_hook" (a punchy opening line for a carousel slide, <=90 chars).

Reply with ONLY a JSON array of exactly {top_n} objects, best first:
[{{"index": n, "why_it_matters": "...", "carousel_hook": "..."}}]. index is 1-based."""


class ClaudeEditor:
    def __init__(self, settings: dict) -> None:
        import anthropic

        self.model = settings["models"]["editor"]
        self.client = anthropic.Anthropic()
        self.fallback = HeuristicEditor()

    def select(self, candidates: list[Candidate], top_n: int) -> list[EditionItem]:
        # Hand the editor a shortlist: the strongest ~3x by composite.
        shortlist = sorted(candidates, key=lambda c: c.score.composite, reverse=True)
        shortlist = shortlist[: max(top_n * 3, top_n)]
        if not shortlist:
            return []
        listing = "\n".join(self._describe(i, c) for i, c in enumerate(shortlist, 1))
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=1200,
                system=[{"type": "text",
                         "text": _SYSTEM_PROMPT.format(top_n=top_n),
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": f"Candidates:\n{listing}"}],
            )
            picks = _extract_json_array(resp.content)
            edition: list[EditionItem] = []
            for rank, pick in enumerate(picks[:top_n], start=1):
                idx = int(pick["index"])
                if not 1 <= idx <= len(shortlist):
                    continue
                edition.append(
                    EditionItem(
                        rank=rank,
                        candidate=shortlist[idx - 1],
                        why_it_matters=str(pick.get("why_it_matters", "")).strip(),
                        carousel_hook=str(pick.get("carousel_hook", "")).strip(),
                    )
                )
            if edition:
                for rank, item in enumerate(edition, start=1):
                    item.rank = rank
                return edition
            raise ValueError("editor returned no usable picks")
        except Exception as exc:  # noqa: BLE001
            log.warning("Claude editor failed (%s); using heuristic", exc)
            return self.fallback.select(candidates, top_n)

    def _describe(self, idx: int, cand: Candidate) -> str:
        rep = cand.cluster.representative
        s = cand.score
        thesis = ""
        if cand.thesis:
            thesis = (f" | thesis: {cand.thesis.relation} — "
                      f"{cand.thesis.thesis_claim}")
        return (
            f"{idx}. [{rep.source_tier}] {rep.title} "
            f"(topics: {', '.join(cand.cluster.topics) or 'n/a'}; "
            f"rel {s.relevance}, nov {s.novelty}, car {s.carousel}, "
            f"score {s.composite}; {len(cand.cluster.source_domains)} src){thesis}"
        )


def _extract_json_array(content: list) -> list:
    text = "".join(getattr(b, "text", "") for b in content)
    match = re.search(r"\[.*\]", text, re.DOTALL)
    return json.loads(match.group(0) if match else text)


def get_editor(settings: dict) -> Editor:
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return ClaudeEditor(settings)
        except Exception as exc:  # noqa: BLE001
            log.warning("Claude unavailable (%s); using heuristic editor", exc)
    return HeuristicEditor()
