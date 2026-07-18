"""Pipeline orchestration: fetch → cluster → score → thesis → edit → render.

``build_edition`` is pure over an item list + collaborators, so tests can drive
the whole scoring/selection path offline with fixtures. ``run`` wires in the
real collectors, store, and outputs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from . import cluster as cluster_mod
from . import editor as editor_mod
from . import scoring as scoring_mod
from . import thesis as thesis_mod
from .fetch import fetch_all, load_sources
from .models import Candidate, Cluster, Edition, Item
from .render import to_payload, write_outputs
from .store import Store
from .thesis import load_theses

log = logging.getLogger("technews.pipeline")


def load_settings(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def _within_recency(item: Item, cutoff: datetime) -> bool:
    return item.published is None or item.published >= cutoff


def _editor_label(editor: editor_mod.Editor) -> str:
    return "claude" if type(editor).__name__ == "ClaudeEditor" else "heuristic"


def build_edition(
    items: list[Item],
    settings: dict,
    theses: list[dict],
    *,
    store: Store | None = None,
    scorer: scoring_mod.Scorer | None = None,
    matcher: thesis_mod.Matcher | None = None,
    editor: editor_mod.Editor | None = None,
    date: str | None = None,
) -> Edition:
    recency_days = int(settings.get("recency_days", 7))
    top_n = int(settings.get("top_n", 5))
    more_items = int(settings.get("more_items", 20))
    max_items = int(settings.get("max_items_per_day", 120))
    cutoff = datetime.now(timezone.utc) - timedelta(days=recency_days)

    fresh = [i for i in items if _within_recency(i, cutoff)]
    log.info("items: %d fetched, %d within %dd", len(items), len(fresh), recency_days)

    first_seen = store.record_items(fresh) if store else {i.id: i.fetched_at for i in fresh}

    clusters = cluster_mod.cluster_items(fresh)
    cluster_mod.score_novelty(clusters, first_seen, recency_days)
    log.info("clustered into %d stories", len(clusters))

    # Cost guard: cap the number of clusters that reach the (paid) scorer.
    clusters = sorted(clusters, key=_cluster_priority, reverse=True)[:max_items]

    scorer = scorer or scoring_mod.get_scorer(settings)
    matcher = matcher or thesis_mod.get_matcher(settings, theses)
    editor = editor or editor_mod.get_editor(settings)

    candidates: list[Candidate] = []
    for cl in clusters:
        candidates.append(
            Candidate(cluster=cl, score=scorer.score(cl), thesis=matcher.match(cl))
        )

    edition_items = editor.select(candidates, top_n)
    chosen_ids = {item.candidate.cluster.id for item in edition_items}
    more = sorted(
        (c for c in candidates if c.cluster.id not in chosen_ids),
        key=lambda c: c.score.composite,
        reverse=True,
    )[:more_items]
    return Edition(
        date=date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        items=edition_items,
        more=more,
        editor=_editor_label(editor),
    )


def _cluster_priority(cl: Cluster) -> tuple[float, int]:
    return (cl.novelty, len(cl.items))


def run(
    config_dir: str | Path = "config",
    output_dir: str | Path = "output",
    db_path: str | Path = "technews.db",
    *,
    send_email: bool = False,
    email_dry_run: bool = False,
) -> Edition:
    config_dir = Path(config_dir)
    settings = load_settings(config_dir / "settings.yaml")
    sources = load_sources(config_dir / "sources.yaml")
    theses = load_theses(config_dir / "theses.yaml")

    items = fetch_all(sources)
    with Store(db_path) as store:
        edition = build_edition(items, settings, theses, store=store)
        paths = write_outputs(edition, output_dir)
        store.save_edition(edition, to_payload(edition))

    log.info("wrote %s and %s", paths["dashboard"], paths["archive"])

    if send_email or email_dry_run:
        from .notify import send_digest

        status = send_digest(edition, settings, dry_run=email_dry_run)
        log.info("email: %s", status.get("reason") or f"sent id={status.get('id')}")

    return edition
