from __future__ import annotations

import technews.fetch as fetch


class FakeResponse:
    def __init__(self, content=b"", data=None):
        self.content = content
        self._data = data

    def json(self):
        return self._data


_RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Test Feed</title>
  <item>
    <title>Chips Act 2.0 proposed by Commission</title>
    <link>https://example.eu/chips</link>
    <description>&lt;p&gt;New EU measures for semiconductors.&lt;/p&gt;</description>
    <pubDate>Mon, 13 Jul 2026 09:00:00 GMT</pubDate>
  </item>
</channel></rss>"""


def test_fetch_rss_parses(monkeypatch):
    monkeypatch.setattr(fetch, "_get", lambda *a, **k: FakeResponse(content=_RSS))
    items = fetch.fetch_rss(
        {"name": "Test", "url": "http://x", "tier": "primary",
         "topics": ["eu-chips-act"]}
    )
    assert len(items) == 1
    it = items[0]
    assert it.title == "Chips Act 2.0 proposed by Commission"
    assert it.url == "https://example.eu/chips"
    assert "semiconductors" in it.summary
    assert it.source_tier == "primary"
    assert it.published is not None


def test_fetch_federal_register_parses(monkeypatch):
    payload = {"results": [{
        "title": "Additions to the Entity List",
        "html_url": "https://federalregister.gov/d/2026-1",
        "publication_date": "2026-07-15",
        "abstract": "Export control update.",
    }]}
    monkeypatch.setattr(fetch, "_get", lambda *a, **k: FakeResponse(data=payload))
    items = fetch.fetch_federal_register(
        {"name": "FR", "query": "export control", "tier": "primary", "topics": []}
    )
    assert len(items) == 1
    assert items[0].url == "https://federalregister.gov/d/2026-1"
    assert items[0].source_tier == "primary"


def test_fetch_skips_unreachable(monkeypatch):
    monkeypatch.setattr(fetch, "_get", lambda *a, **k: None)
    assert fetch.fetch_rss({"name": "x", "url": "http://x"}) == []
