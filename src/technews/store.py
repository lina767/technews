"""SQLite persistence: item dedup/first-seen tracking and edition history.

The store gives the pipeline two things it can't derive from a single run:
1. *first_seen* per URL — so an item that has been circulating for days is not
   treated as brand new, and so re-runs don't re-surface the same story.
2. an archive of past editions.

Stdlib only (sqlite3 + json) — keeps the footprint tiny.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Edition, Item, Topic

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id          TEXT PRIMARY KEY,
    url         TEXT NOT NULL,
    source_name TEXT,
    source_tier TEXT,
    title       TEXT,
    summary     TEXT,
    published   TEXT,
    topics      TEXT,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS editions (
    date         TEXT PRIMARY KEY,
    generated_at TEXT NOT NULL,
    editor       TEXT,
    payload      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS topic_history (
    date            TEXT NOT NULL,
    label           TEXT NOT NULL,
    mentions        INTEGER NOT NULL,
    europe_count    INTEGER NOT NULL,
    worldwide_count INTEGER NOT NULL,
    PRIMARY KEY (date, label)
);
"""


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


class Store:
    def __init__(self, path: str | Path = "technews.db") -> None:
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def record_items(self, items: list[Item]) -> dict[str, datetime]:
        """Upsert items; return {item.id: first_seen} for every recorded item.

        first_seen is preserved across runs; last_seen is bumped every time.
        """
        now = datetime.now(timezone.utc).isoformat()
        first_seen: dict[str, datetime] = {}
        for item in items:
            row = self.conn.execute(
                "SELECT first_seen FROM items WHERE id = ?", (item.id,)
            ).fetchone()
            seen = row["first_seen"] if row else now
            self.conn.execute(
                """
                INSERT INTO items (id, url, source_name, source_tier, title,
                                   summary, published, topics, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET last_seen = excluded.last_seen
                """,
                (
                    item.id, item.url, item.source_name, item.source_tier,
                    item.title, item.summary, _iso(item.published),
                    json.dumps(item.topics), seen, now,
                ),
            )
            first_seen[item.id] = datetime.fromisoformat(seen)
        self.conn.commit()
        return first_seen

    def save_edition(self, edition: Edition, payload: dict) -> None:
        self.conn.execute(
            """
            INSERT INTO editions (date, generated_at, editor, payload)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                generated_at = excluded.generated_at,
                editor = excluded.editor,
                payload = excluded.payload
            """,
            (edition.date, edition.generated_at.isoformat(), edition.editor,
             json.dumps(payload, default=str)),
        )
        self.conn.commit()

    def get_edition_payload(self, date: str) -> dict | None:
        row = self.conn.execute(
            "SELECT payload FROM editions WHERE date = ?", (date,)
        ).fetchone()
        return json.loads(row["payload"]) if row else None

    def get_topic_history(self, before_date: str, days: int = 14) -> list[dict]:
        """Prior days' topic snapshots, strictly before ``before_date``."""
        rows = self.conn.execute(
            """
            SELECT date, label, mentions FROM topic_history
            WHERE date < ?
            ORDER BY date DESC
            """,
            (before_date,),
        ).fetchall()
        # SQLite has no easy "last N distinct dates" filter without a window
        # function version check, so trim in Python — history is tiny.
        distinct_dates = sorted({r["date"] for r in rows}, reverse=True)[:days]
        keep = set(distinct_dates)
        return [dict(r) for r in rows if r["date"] in keep]

    def save_topic_snapshot(self, date: str, topics: list[Topic]) -> None:
        self.conn.execute("DELETE FROM topic_history WHERE date = ?", (date,))
        self.conn.executemany(
            """
            INSERT INTO topic_history (date, label, mentions, europe_count, worldwide_count)
            VALUES (?, ?, ?, ?, ?)
            """,
            [(date, t.label, t.mentions, t.europe_count, t.worldwide_count) for t in topics],
        )
        self.conn.commit()
