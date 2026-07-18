from __future__ import annotations

from technews.pipeline import build_edition
from technews.render import render_dashboard, render_email, render_markdown

from .conftest import make_item


def _sample_items():
    return [
        make_item("Commerce tightens semiconductor export control on TSMC",
                  "https://a.gov/1", tier="primary", topics=["us-tech-policy"],
                  summary="export control china decoupling $2 billion"),
        make_item("EU Chips Act 2.0 pledges new fab subsidies",
                  "https://ec.europa.eu/2", tier="primary", topics=["eu-chips-act"],
                  summary="chips act subsidy funding fab"),
        make_item("ASML reports record lithography orders",
                  "https://b.com/3", tier="secondary", topics=["semiconductor-industry"],
                  summary="asml semiconductor"),
        make_item("Local council debates parking rules",
                  "https://c.com/4", tier="secondary", topics=[]),
    ]


def test_build_edition_respects_top_n(settings, theses):
    edition = build_edition(_sample_items(), settings, theses)
    assert len(edition.items) == settings["top_n"]
    assert [i.rank for i in edition.items] == [1, 2, 3]
    assert edition.editor == "heuristic"


def test_edition_prefers_on_topic(settings, theses):
    edition = build_edition(_sample_items(), settings, theses)
    titles = [i.item.title for i in edition.items]
    assert "Local council debates parking rules" not in titles


def test_render_smoke(settings, theses):
    edition = build_edition(_sample_items(), settings, theses)
    html = render_dashboard(edition)
    md = render_markdown(edition)
    email = render_email(edition)
    assert "Daily Top 5" in html
    assert edition.items[0].item.title in md
    assert "Daily Top 5" in email
    # editorial fields are populated
    assert edition.items[0].why_it_matters
    assert edition.items[0].carousel_hook


def test_thesis_match_surfaces(settings, theses):
    edition = build_edition(_sample_items(), settings, theses)
    matched = [i for i in edition.items if i.candidate.thesis]
    assert matched, "expected at least one item matched to a thesis"
