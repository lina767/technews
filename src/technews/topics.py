"""Topic Discovery agent: what's being talked about, and what's just starting to be.

Unlike the curated ``settings.topics`` keyword tags (used for relevance scoring),
labels here are open-vocabulary — discovered fresh from the full cluster pool each
run, not matched against a fixed list. That is what lets a real but not-yet-big
theme (a datacenter-energy debate, say) surface before it would ever clear the
bar for the Top 5.

Two agents share one pass:
  * region assignment — Europe vs. worldwide, read from article content (not just
    which feed it came from), since a US outlet covering an EU story should count
    as Europe.
  * topic grouping — cluster headlines into a handful of named, described themes.

``HeuristicTopicExtractor`` runs offline (curated topics + frequent capitalized
n-grams as an open-vocabulary approximation); ``ClaudeTopicExtractor`` does the
grouping and per-cluster region call in a single batched request — one call per
run, not per cluster, since neither task needs the fine-grained composite score.

Trend classification (``classify_trends``) is a separate, extractor-agnostic step:
it compares today's mention counts against each topic's trailing history in the
store via fuzzy label matching (Claude does not name the same theme identically
day to day).
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from rapidfuzz import fuzz

from . import cluster as cluster_mod
from .models import Cluster, Item, Topic, TopicMention, TopicReport
from .store import Store

log = logging.getLogger("technews.topics")

_HISTORY_RETENTION_DAYS = 60  # bound the JSON sidecar file's growth

_LABEL_MATCH_THRESHOLD = 78  # rapidfuzz token_set_ratio to treat two days' labels as the same topic
_EMERGING_RATIO = 2.0        # today's count must be >= this multiple of its baseline
_EMERGING_MIN_DELTA = 3      # ...or at least this many more mentions than baseline

_EUROPE_KEYWORDS = {
    "eu", "european union", "european commission", "brussels", "europe", "european",
    "germany", "german", "france", "french", "berlin", "paris", "eprs", "european parliament",
    "bruegel", "ceps", "digital sovereignty", "gaia-x", "ipcei", "chips act",
    "uk", "united kingdom", "dsit", "london",
}
_WORLDWIDE_HINT_KEYWORDS = {
    "united states", "u.s.", "us ", "white house", "washington", "congress",
    "commerce department", "china", "chinese", "beijing", "taiwan", "tsmc",
    "japan", "korea", "india", "bis", "ustr",
}
# Case-insensitive: title case capitalizes these too ("Weigh In On"), which would
# otherwise glue unrelated capital runs together.
_STOP_CI = {
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "with", "as",
    "is", "are", "new", "says", "amid", "over", "its", "how", "why", "what", "up",
    "at", "by", "from", "into", "amid", "after", "before", "than", "vs",
}
_WORD_CHARS_RE = re.compile(r"^[A-Za-z0-9&.\-]+$")


def _classify_region(text: str) -> str:
    low = text.lower()
    eu_hits = sum(1 for kw in _EUROPE_KEYWORDS if kw in low)
    world_hits = sum(1 for kw in _WORLDWIDE_HINT_KEYWORDS if kw in low)
    if eu_hits and eu_hits >= world_hits:
        return "europe"
    return "worldwide"


class Extractor(Protocol):
    def discover(self, clusters: list[Cluster]) -> list[Topic]: ...


def _strip_source_suffix(title: str, source_name: str) -> str:
    """Drop a trailing " - <Publisher Name>" that Google News RSS appends.

    Left unstripped, the publisher name itself gets picked up as a recurring
    "topic" (it appears in every headline from that feed).
    """
    suffix = re.compile(r"\s*[-|]\s*" + re.escape(source_name) + r"\s*$", re.IGNORECASE)
    return suffix.sub("", title).strip()


def _capital_runs(title: str) -> list[list[str]]:
    """Consecutive non-stopword capitalized words, e.g. for sliding n-grams."""
    runs: list[list[str]] = []
    current: list[str] = []
    for raw in title.split():
        word = raw.strip(".,;:!?\"'()")
        is_word = bool(_WORD_CHARS_RE.match(word))
        if is_word and word[:1].isupper() and word.lower() not in _STOP_CI:
            current.append(word)
        else:
            if len(current) >= 2:
                runs.append(current)
            current = []
    if len(current) >= 2:
        runs.append(current)
    return runs


def _ngrams(run: list[str], n: int) -> list[str]:
    return [" ".join(run[i:i + n]) for i in range(len(run) - n + 1)]


class HeuristicTopicExtractor:
    """Frequent capitalized phrases across headlines, region-tagged by keyword."""

    def __init__(self, settings: dict) -> None:
        self.limit = int(settings.get("topic_scan_limit", 200))

    def discover(self, clusters: list[Cluster]) -> list[Topic]:
        pool = clusters[: self.limit]
        phrase_hits: dict[str, list[Cluster]] = {}
        for cl in pool:
            rep = cl.representative
            clean_title = _strip_source_suffix(rep.title, rep.source_name)
            seen_in_cluster: set[str] = set()
            for run in _capital_runs(clean_title):
                for n in (3, 2):
                    for phrase in _ngrams(run, n):
                        if phrase in seen_in_cluster:
                            continue
                        seen_in_cluster.add(phrase)
                        phrase_hits.setdefault(phrase, []).append(cl)

        # Keep phrases that recur across >= 2 independent clusters, longest first
        # so a 3-gram like "Datacenter Energy Debate" is kept and its 2-gram
        # sub-phrases ("Datacenter Energy") are dropped as redundant.
        topics: list[Topic] = []
        kept_labels: list[str] = []
        ranked = sorted(phrase_hits.items(), key=lambda kv: (-len(kv[0].split()), -len(kv[1])))
        for phrase, hit_clusters in ranked:
            if len(hit_clusters) < 2:
                continue
            if any(phrase.lower() in kept.lower() for kept in kept_labels):
                continue
            kept_labels.append(phrase)
            topic = Topic(label=phrase, description="Frequently mentioned across headlines.")
            for cl in hit_clusters:
                rep = cl.representative
                region = _classify_region(f"{rep.title} {rep.summary}")
                if region == "europe":
                    topic.europe_count += 1
                else:
                    topic.worldwide_count += 1
                if len(topic.examples) < 3:
                    topic.examples.append(TopicMention(rep.title, rep.url, rep.source_name))
            topics.append(topic)
            if len(topics) >= 25:
                break
        return topics


_SYSTEM_PROMPT = """You analyze a day's worth of tech-politics headlines for a creator \
covering EU/US tech policy, semiconductors, AI regulation, and digital sovereignty.

Two tasks over the numbered headline list:
1. Assign EVERY headline index to a region based on its actual content (not just the \
outlet) — "europe" if it is primarily about the EU/UK or European actors/policy, else \
"worldwide".
2. Group the headlines into 10-20 open-vocabulary topics — real themes, not the fixed \
categories you might expect. Actively surface topics that are NOT yet mainstream: a \
handful of related headlines on a nascent theme (e.g. a datacenter-energy debate) matter \
more here than repeating an already-obvious mega-story. Each topic needs a short label, \
a one-sentence description, and the indices of the headlines that belong to it (an index \
may belong to zero or one topic; skip headlines that do not fit any coherent group).

Reply with ONLY this JSON shape:
{"regions": {"europe": [i, ...], "worldwide": [i, ...]},
 "topics": [{"label": "...", "description": "...", "indices": [i, ...]}, ...]}"""


class ClaudeTopicExtractor:
    def __init__(self, settings: dict) -> None:
        import anthropic  # local import: only needed on this path

        self.limit = int(settings.get("topic_scan_limit", 200))
        self.model = settings["models"].get("topics", settings["models"]["editor"])
        self.client = anthropic.Anthropic()
        self.fallback = HeuristicTopicExtractor(settings)

    def discover(self, clusters: list[Cluster]) -> list[Topic]:
        pool = clusters[: self.limit]
        if not pool:
            return []
        listing = "\n".join(self._describe(i, cl) for i, cl in enumerate(pool, 1))
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=4000,
                system=[{"type": "text", "text": _SYSTEM_PROMPT,
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": f"Headlines:\n{listing}"}],
            )
            data = _extract_json(resp.content)
            region_by_idx: dict[int, str] = {}
            for idx in data.get("regions", {}).get("europe", []):
                region_by_idx[int(idx)] = "europe"
            for idx in data.get("regions", {}).get("worldwide", []):
                region_by_idx.setdefault(int(idx), "worldwide")

            topics: list[Topic] = []
            for entry in data.get("topics", []):
                label = str(entry.get("label", "")).strip()
                if not label:
                    continue
                topic = Topic(label=label, description=str(entry.get("description", "")).strip())
                for raw_idx in entry.get("indices", []):
                    idx = int(raw_idx)
                    if not 1 <= idx <= len(pool):
                        continue
                    cl = pool[idx - 1]
                    region = region_by_idx.get(idx) or _classify_region(
                        f"{cl.representative.title} {cl.representative.summary}"
                    )
                    if region == "europe":
                        topic.europe_count += 1
                    else:
                        topic.worldwide_count += 1
                    if len(topic.examples) < 3:
                        rep = cl.representative
                        topic.examples.append(TopicMention(rep.title, rep.url, rep.source_name))
                if topic.mentions:
                    topics.append(topic)
            if topics:
                return topics
            raise ValueError("Claude returned no usable topics")
        except Exception as exc:  # noqa: BLE001 — degrade to heuristic, never crash
            log.warning("Claude topic discovery failed (%s); using heuristic", exc)
            return self.fallback.discover(clusters)

    def _describe(self, idx: int, cl: Cluster) -> str:
        rep = cl.representative
        title = _strip_source_suffix(rep.title, rep.source_name)
        return f"{idx}. {title} — {rep.source_name} ({len(cl.source_domains)} src)"


def _extract_json(content: list) -> dict:
    text = "".join(getattr(b, "text", "") for b in content)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return json.loads(match.group(0) if match else text)


def get_extractor(settings: dict) -> Extractor:
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return ClaudeTopicExtractor(settings)
        except Exception as exc:  # noqa: BLE001
            log.warning("Claude unavailable (%s); using heuristic topic extractor", exc)
    return HeuristicTopicExtractor(settings)


def _extractor_label(extractor: Extractor) -> str:
    return "claude" if type(extractor).__name__ == "ClaudeTopicExtractor" else "heuristic"


def _best_history_match(label: str, history_labels: list[str]) -> str | None:
    best, best_score = None, 0.0
    for hist_label in history_labels:
        score = fuzz.token_set_ratio(label, hist_label)
        if score > best_score:
            best_score, best = score, hist_label
    return best if best_score >= _LABEL_MATCH_THRESHOLD else None


def classify_trends(topics: list[Topic], history: list[dict]) -> None:
    """Set ``trend`` / ``baseline_avg`` on each topic in place.

    ``history`` is prior days' snapshots: ``[{"label": str, "mentions": int}, ...]``,
    already excluding today. A topic with no matching history is "new"; one whose
    mentions are well above its own trailing average — but still below today's
    established topics — is "emerging"; everything else is "established".
    """
    if not topics:
        return
    by_label: dict[str, list[int]] = {}
    for row in history:
        by_label.setdefault(row["label"], []).append(int(row["mentions"]))
    history_labels = list(by_label)

    mainstream_cutoff = sorted((t.mentions for t in topics), reverse=True)[2] if len(topics) >= 3 else None

    for topic in topics:
        not_yet_mainstream = mainstream_cutoff is None or topic.mentions < mainstream_cutoff
        match = _best_history_match(topic.label, history_labels)
        if match is None:
            topic.baseline_avg = None
            # No tracked history for this label: only call it "new" if it isn't
            # already one of today's biggest stories — otherwise it's more likely
            # a big, obvious topic we just don't have continuity data for yet.
            topic.trend = "new" if not_yet_mainstream else "established"
            continue
        counts = by_label[match]
        baseline = sum(counts) / len(counts)
        topic.baseline_avg = round(baseline, 2)
        above_baseline = topic.mentions >= baseline * _EMERGING_RATIO or \
            topic.mentions - baseline >= _EMERGING_MIN_DELTA
        topic.trend = "emerging" if (above_baseline and not_yet_mainstream) else "established"


def _within_recency(item: Item, cutoff: datetime) -> bool:
    return item.published is None or item.published >= cutoff


def _load_history_json(path: Path, before_date: str, days: int) -> list[dict]:
    if not path.exists():
        return []
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    distinct_dates = sorted({r["date"] for r in rows if r["date"] < before_date}, reverse=True)[:days]
    keep = set(distinct_dates)
    return [r for r in rows if r["date"] in keep]


def _append_history_json(path: Path, date: str, topics: list[Topic]) -> None:
    rows: list[dict] = []
    if path.exists():
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            rows = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_HISTORY_RETENTION_DAYS)).strftime("%Y-%m-%d")
    rows = [r for r in rows if r["date"] >= cutoff and r["date"] != date]
    rows.extend({"date": date, "label": t.label, "mentions": t.mentions} for t in topics)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=0), encoding="utf-8")


def build_topic_report(
    items: list[Item],
    settings: dict,
    *,
    store: Store | None = None,
    extractor: Extractor | None = None,
    date: str | None = None,
    history_file: str | Path | None = None,
) -> TopicReport:
    """Runs independently of ``build_edition`` over the *full* cluster pool.

    Deliberately does not reuse the Top-5 candidate list: region + topic
    grouping need no relevance/carousel score, so this can see every cluster
    (not just the ``max_items_per_day`` slice that reaches the paid scorer) —
    which is what lets a not-yet-mainstream theme show up here at all.

    Trend history: pass ``history_file`` (a small JSON sidecar under ``output/``)
    when running in CI, where the SQLite store is never durable across runs —
    it's the only thing the daily workflow actually commits back to the repo.
    ``store`` alone is fine for local/persistent-DB use (e.g. the test suite).
    """
    recency_days = int(settings.get("recency_days", 7))
    cutoff = datetime.now(timezone.utc) - timedelta(days=recency_days)
    fresh = [i for i in items if _within_recency(i, cutoff)]

    clusters = cluster_mod.cluster_items(fresh)
    first_seen = store.record_items(fresh) if store else {i.id: i.fetched_at for i in fresh}
    cluster_mod.score_novelty(clusters, first_seen, recency_days)
    clusters = sorted(clusters, key=lambda c: c.novelty, reverse=True)

    extractor = extractor or get_extractor(settings)
    topics = sorted(extractor.discover(clusters), key=lambda t: t.mentions, reverse=True)

    report_date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    history_days = int(settings.get("topic_history_days", 14))
    history_path = Path(history_file) if history_file else None
    if history_path:
        history = _load_history_json(history_path, report_date, history_days)
    elif store:
        history = store.get_topic_history(report_date, days=history_days)
    else:
        history = []

    classify_trends(topics, history)

    if history_path:
        _append_history_json(history_path, report_date, topics)
    elif store:
        store.save_topic_snapshot(report_date, topics)

    has_baseline = bool(history)
    emerging = [t for t in topics if t.trend in ("emerging", "new")] if has_baseline else []
    return TopicReport(
        date=report_date,
        current=topics,
        emerging=emerging,
        has_baseline=has_baseline,
        extractor=_extractor_label(extractor),
    )
