"""Collector agent: pull items from the source registry.

Two source types are supported:
  * ``rss``                  — any RSS/Atom feed (incl. newsletters that expose one)
  * ``federal_register_api`` — the keyless US Federal Register JSON API

Fetching is deliberately fault-tolerant: a source that is unreachable or
malformed is logged and skipped, never fatal — one dead feed must not sink a run.
"""

from __future__ import annotations

import calendar
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import httpx
import yaml

from .models import Item

log = logging.getLogger("technews.fetch")

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_USER_AGENT = "technews/0.1 (+https://github.com/lina767/technews)"
FEDERAL_REGISTER_API = "https://www.federalregister.gov/api/v1/documents.json"


def load_sources(path: str | Path) -> list[dict]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return data.get("sources", [])


def _clean(text: str | None, limit: int = 500) -> str:
    if not text:
        return ""
    text = _WS_RE.sub(" ", _TAG_RE.sub(" ", text)).strip()
    return text[:limit].rstrip()


def _struct_to_dt(struct: object) -> datetime | None:
    if not struct:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(struct), tz=timezone.utc)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None


def _get(url: str, timeout: float = 20.0, **params: str) -> httpx.Response | None:
    try:
        resp = httpx.get(
            url, params=params or None, timeout=timeout,
            follow_redirects=True, headers={"User-Agent": _USER_AGENT},
        )
        resp.raise_for_status()
        return resp
    except Exception as exc:  # noqa: BLE001 — one bad source must not break the run
        log.warning("fetch failed for %s: %s", url, exc)
        return None


def fetch_rss(source: dict) -> list[Item]:
    resp = _get(source["url"])
    if resp is None:
        return []
    parsed = feedparser.parse(resp.content)
    items: list[Item] = []
    for entry in parsed.entries:
        link = entry.get("link")
        title = entry.get("title")
        if not link or not title:
            continue
        published = _struct_to_dt(
            entry.get("published_parsed") or entry.get("updated_parsed")
        )
        summary = _clean(entry.get("summary") or entry.get("description"))
        items.append(
            Item(
                source_name=source["name"],
                source_tier=source.get("tier", "secondary"),
                title=_clean(title, limit=300),
                url=link,
                summary=summary,
                published=published,
                topics=list(source.get("topics", [])),
            )
        )
    return items


def fetch_federal_register(source: dict) -> list[Item]:
    """Query the Federal Register for one search term, most-recent first."""
    resp = _get(
        FEDERAL_REGISTER_API,
        **{
            "conditions[term]": source["query"],
            "order": "newest",
            "per_page": str(source.get("per_page", 20)),
        },
    )
    if resp is None:
        return []
    payload = resp.json()
    items: list[Item] = []
    for doc in payload.get("results", []):
        url = doc.get("html_url")
        title = doc.get("title")
        if not url or not title:
            continue
        published = None
        if doc.get("publication_date"):
            try:
                published = datetime.fromisoformat(doc["publication_date"]).replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                published = None
        summary = _clean(doc.get("abstract"))
        items.append(
            Item(
                source_name=source["name"],
                source_tier=source.get("tier", "primary"),
                title=_clean(title, limit=300),
                url=url,
                summary=summary,
                published=published,
                topics=list(source.get("topics", [])),
            )
        )
    return items


_FETCHERS = {
    "rss": fetch_rss,
    "federal_register_api": fetch_federal_register,
}


def fetch_all(sources: list[dict]) -> list[Item]:
    items: list[Item] = []
    for source in sources:
        fetcher = _FETCHERS.get(source.get("type", "rss"))
        if fetcher is None:
            log.warning("unknown source type for %s", source.get("name"))
            continue
        got = fetcher(source)
        log.info("%-28s %3d items", source.get("name", "?"), len(got))
        items.extend(got)
    return items
