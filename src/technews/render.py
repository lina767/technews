"""Render agent: turn an Edition into a dashboard, a markdown archive, and an email.

Templates stay dumb — this module flattens the Edition into a plain view dict so
Jinja only has to lay out values, never reach into dataclasses.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .models import Candidate, Edition, EditionItem

_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"

_RELATION_LABEL = {
    "supports": "supports",
    "contradicts": "contradicts",
    "extends": "extends",
    "neutral": "relates to",
}


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _item_view(item: EditionItem) -> dict:
    cand = item.candidate
    rep = item.item
    sources = sorted(cand.cluster.source_domains)
    thesis = None
    if cand.thesis:
        thesis = {
            "relation": cand.thesis.relation,
            "label": _RELATION_LABEL.get(cand.thesis.relation, "relates to"),
            "claim": cand.thesis.thesis_claim,
            "note": cand.thesis.note,
        }
    return {
        "rank": item.rank,
        "title": rep.title,
        "url": rep.url,
        "summary": rep.summary,
        "source_name": rep.source_name,
        "source_tier": rep.source_tier,
        "domain": rep.domain,
        "topics": [t.replace("-", " ") for t in cand.cluster.topics],
        "published": rep.published.strftime("%b %d, %Y") if rep.published else "",
        "scores": {
            "relevance": cand.score.relevance,
            "novelty": cand.score.novelty,
            "carousel": cand.score.carousel,
            "composite": cand.score.composite,
        },
        "composite_pct": round(cand.score.composite * 100),
        "novelty_pct": round((cand.score.novelty - 1) / 4 * 100),
        "sources": sources,
        "sources_count": len(sources),
        "is_everywhere": len(sources) >= 3,
        "why_it_matters": item.why_it_matters,
        "carousel_hook": item.carousel_hook,
        "key_takeaways": item.key_takeaways,
        "thesis": thesis,
        "rationale": cand.score.rationale,
        "scorer": cand.score.scorer,
    }


def _more_item_view(cand: Candidate) -> dict:
    rep = cand.cluster.representative
    return {
        "title": rep.title,
        "url": rep.url,
        "source_name": rep.source_name,
        "source_tier": rep.source_tier,
        "published": rep.published.strftime("%b %d, %Y") if rep.published else "",
        "topics": [t.replace("-", " ") for t in cand.cluster.topics],
        "composite_pct": round(cand.score.composite * 100),
        "sources_count": len(cand.cluster.source_domains),
    }


def edition_view(edition: Edition) -> dict:
    return {
        "date": edition.date,
        "generated_at": edition.generated_at.strftime("%Y-%m-%d %H:%M UTC"),
        "editor": edition.editor,
        "items": [_item_view(i) for i in edition.items],
        "more": [_more_item_view(c) for c in edition.more],
    }


def render_dashboard(edition: Edition) -> str:
    return _env().get_template("dashboard.html.j2").render(**edition_view(edition))


def render_markdown(edition: Edition) -> str:
    return _env().get_template("digest.md.j2").render(**edition_view(edition))


def render_email(edition: Edition) -> str:
    return _env().get_template("email.html.j2").render(**edition_view(edition))


def to_payload(edition: Edition) -> dict:
    """JSON-serializable snapshot for the store / archive."""
    return edition_view(edition)


def write_outputs(edition: Edition, output_dir: str | Path) -> dict[str, Path]:
    """Write the dashboard (as index.html + dashboard.html) and archive/<date>.md.

    ``index.html`` is what Vercel serves at ``/``; ``dashboard.html`` is a stable
    alias. Returns the written paths.
    """
    out = Path(output_dir)
    (out / "archive").mkdir(parents=True, exist_ok=True)
    html = render_dashboard(edition)
    index = out / "index.html"
    dashboard = out / "dashboard.html"
    archive = out / "archive" / f"{edition.date}.md"
    index.write_text(html, encoding="utf-8")
    dashboard.write_text(html, encoding="utf-8")
    archive.write_text(render_markdown(edition), encoding="utf-8")
    return {"index": index, "dashboard": dashboard, "archive": archive}
