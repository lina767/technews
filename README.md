# Tech Politics Brief

A proactive platform that surfaces worldwide **tech-politics** news from primary
and secondary sources, pre-sorts it, scores it with a Claude layer, matches every
item against **your own theses**, and publishes a daily **Top 5** dashboard.

Topics it tracks: EU Chips Act & Chips Act 2.0 · EU AI regulation · digital
sovereignty · AI industrial policy · US technology policy · semiconductor
industry news.

## How it works — a pipeline of named agents

```
fetch → cluster (novelty) → score → thesis-match → edit → render/notify
```

1. **Collector** (`fetch.py`) pulls ~20 feeds from `config/sources.yaml`
   (RSS + the keyless US Federal Register JSON API).
2. **Novelty agent** (`cluster.py`) groups the same story across sources and
   answers *"is this already everywhere?"* — more independent domains ⇒ less
   novel, but a primary-source break stays novel even when the trade press piles on.
3. **Scoring agent** (`scoring.py`) rates each story 1–5 on **relevance**,
   **novelty**, and **carousel-worthiness**.
4. **Thesis agent** (`thesis.py`) matches each item to your standing arguments
   in `config/theses.yaml` (supports / contradicts / extends / neutral).
5. **Editor agent** (`editor.py`) picks the daily **Top 5** with topic diversity
   and writes a *why-it-matters* line and a *carousel hook* per pick.
6. **Render / Notify** produce a self-contained HTML dashboard, a dated markdown
   archive, and an email digest.

A parallel, independent pass — **Topic Discovery** (`topics.py`) — runs over the
*full* cluster pool (not just the Top-5 candidates) to build `output/topics.html`:
open-vocabulary topics (not the fixed keyword list above), split **Europe** vs.
**worldwide** from article content, plus a **Hidden & emerging** section for themes
trending above their own trailing average but not yet mainstream (needs ~1–2 weeks
of daily runs to build a baseline — `output/topic_history.json` is the durable
record, since it's what the daily workflow actually commits back to the repo).

**No API key? It still runs.** Every Claude-backed agent has a deterministic
heuristic fallback, so the whole pipeline works offline. Set `ANTHROPIC_API_KEY`
to switch scoring/editing to Claude (Haiku for per-item scoring, Sonnet for the
editor) — the code path is identical.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

technews run --once          # fetch, score, render → output/dashboard.html + output/topics.html
open output/dashboard.html   # (or xdg-open) view the Top 5
open output/topics.html      # what's being discussed, Europe vs. worldwide + emerging themes

technews show                # print the latest edition as markdown
technews fetch               # just fetch and report per-source counts
technews notify --dry-run    # render the email digest without sending
```

With Claude scoring:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
technews run --once
```

## Configuration (`config/`)

- **`sources.yaml`** — the source registry, 4 tiers (`primary` · `institute` ·
  `secondary` · `newsletter`). Each source carries `topics` tags; `tier` feeds
  the scoring so primary sources anchor relevance and novelty. Add/replace feeds
  freely — unreachable ones are skipped, never fatal.
- **`theses.yaml`** — *your* arguments. Every item is matched against these; edit
  the claims and keywords to make the brief personal.
- **`settings.yaml`** — score weights, `top_n`, `recency_days`, cost cap
  (`max_items_per_day`), Claude model ids, and the Resend email from/to. Topic
  Discovery has its own cost guard (`topic_scan_limit`) and trend window
  (`topic_history_days`).

## Email digest (Resend)

Email uses [Resend](https://resend.com): a single `RESEND_API_KEY`, good
deliverability for automated mail, and self-delivery works without a custom
domain. Set the key and recipient, then:

```bash
export RESEND_API_KEY=re_...
technews run --email
```

Without the key, `--email` / `notify` render the HTML but don't send.

## Deploy (Vercel) + daily automation

The dashboard is a single self-contained HTML file — host it anywhere static.
This repo ships a Vercel config (`vercel.json`, output dir `output/`).

`.github/workflows/daily.yml` runs the pipeline every morning (06:00 UTC),
commits the dated markdown archive, emails the digest, and deploys the dashboard
to Vercel. Add these repo secrets to enable the optional pieces:

| Secret | Enables |
|---|---|
| `ANTHROPIC_API_KEY` | Claude scoring/editing (else heuristic) |
| `RESEND_API_KEY` | Email digest |
| `VERCEL_TOKEN` | Auto-deploy the dashboard |

## Cost

Hosting (Vercel), CI (GitHub Actions), and email (Resend free tier) are €0. The
only variable cost is the Claude scoring layer — roughly **€6–18/month** at
~50–100 stories/day (Haiku for scoring, Sonnet for the editor, prompt-cached).
The `max_items_per_day` cap and the Batch API keep it well under €20; the
heuristic fallback is €0.

## Tests

```bash
pytest -q          # offline: fixtures for fetch, clustering, scoring, thesis, render
```
