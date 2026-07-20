from __future__ import annotations

import json

from technews.models import Cluster, Topic
from technews.topics import (
    HeuristicTopicExtractor,
    _classify_region,
    build_topic_report,
    classify_trends,
)

from .conftest import make_item


def _cluster(*items):
    return Cluster(items=list(items))


def test_classify_region_prefers_europe_signal():
    assert _classify_region("EU Commission unveils Brussels sovereignty plan") == "europe"
    assert _classify_region("White House commerce department expands export controls") == "worldwide"
    assert _classify_region("Local bakery wins award") == "worldwide"


def test_heuristic_extractor_finds_recurring_phrases(settings):
    clusters = [
        _cluster(make_item("Datacenter Energy Debate Heats Up in Virginia", "https://a.com/1")),
        _cluster(make_item("Senators Weigh In On Datacenter Energy Debate", "https://b.com/2")),
        _cluster(make_item("Local council debates parking rules", "https://c.com/3")),
    ]
    extractor = HeuristicTopicExtractor(settings)
    topics = extractor.discover(clusters)
    labels = [t.label for t in topics]
    assert any("Datacenter Energy Debate" in label for label in labels), labels
    # A phrase mentioned in only one cluster must not surface as a topic.
    assert not any("parking" in label.lower() for label in labels)


def test_classify_trends_new_topic_has_no_baseline():
    topics = [Topic(label="Novel Theme X", europe_count=0, worldwide_count=4)]
    classify_trends(topics, history=[])
    assert topics[0].trend == "new"
    assert topics[0].baseline_avg is None


def test_classify_trends_flags_emerging_above_baseline():
    topics = [
        Topic(label="Mainstream Story", worldwide_count=40),
        Topic(label="Second Story", worldwide_count=20),
        Topic(label="Third Story", worldwide_count=15),
        Topic(label="Datacenter Debate", worldwide_count=8),
    ]
    history = [
        {"date": "2026-07-10", "label": "Datacenter Debate", "mentions": 1},
        {"date": "2026-07-11", "label": "Datacenter Debate", "mentions": 2},
    ]
    classify_trends(topics, history)
    emerging = [t.label for t in topics if t.trend == "emerging"]
    assert "Datacenter Debate" in emerging
    assert topics[0].trend == "established"  # the mainstream cutoff itself never counts as emerging


def test_classify_trends_matches_fuzzy_labels_across_days():
    topics = [Topic(label="EU AI Act enforcement pushback", worldwide_count=6)]
    history = [{"date": "2026-07-10", "label": "EU AI Act Enforcement Pushback", "mentions": 2}]
    classify_trends(topics, history)
    assert topics[0].baseline_avg == 2.0


def test_build_topic_report_persists_history_across_runs(settings, tmp_path):
    history_file = tmp_path / "topic_history.json"
    items_day1 = [
        make_item("Datacenter Energy Debate Splits Virginia Lawmakers", "https://a.com/1"),
        make_item("Utility Regulators Weigh In On Datacenter Energy Debate", "https://b.com/2"),
    ]
    report1 = build_topic_report(items_day1, settings, date="2026-07-10", history_file=history_file)
    assert report1.has_baseline is False  # nothing before day 1
    assert history_file.exists()
    saved = json.loads(history_file.read_text())
    assert any(row["date"] == "2026-07-10" for row in saved)

    items_day2 = [
        make_item("Datacenter Energy Debate Splits Virginia Lawmakers", "https://a.com/1", age_hours=1),
        make_item("Utility Regulators Weigh In On Datacenter Energy Debate", "https://b.com/2", age_hours=1),
        make_item("Senate Adds To Datacenter Energy Debate", "https://c.com/3", age_hours=1),
        make_item("FERC Escalates Datacenter Energy Debate Further", "https://d.com/4", age_hours=1),
    ]
    report2 = build_topic_report(items_day2, settings, date="2026-07-11", history_file=history_file)
    assert report2.has_baseline is True
