"""Command line entry point: ``technews run|fetch|notify|show``."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import pipeline
from .fetch import fetch_all, load_sources
from .notify import send_digest
from .thesis import load_theses


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )


def cmd_run(args: argparse.Namespace) -> int:
    edition = pipeline.run(
        config_dir=args.config,
        output_dir=args.output,
        db_path=args.db,
        send_email=args.email,
        email_dry_run=args.email_dry_run,
    )
    print(f"Edition {edition.date}: {len(edition.items)} items "
          f"(editor: {edition.editor}) → {Path(args.output) / 'dashboard.html'}")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    sources = load_sources(Path(args.config) / "sources.yaml")
    items = fetch_all(sources)
    print(f"Fetched {len(items)} items from {len(sources)} sources.")
    return 0


def cmd_notify(args: argparse.Namespace) -> int:
    config = Path(args.config)
    settings = pipeline.load_settings(config / "settings.yaml")
    sources = load_sources(config / "sources.yaml")
    theses = load_theses(config / "theses.yaml")
    items = fetch_all(sources)
    edition = pipeline.build_edition(items, settings, theses)
    status = send_digest(edition, settings, dry_run=args.dry_run)
    if status["sent"]:
        print(f"Sent digest (id={status.get('id')}).")
    else:
        print(f"Not sent ({status['reason']}). Rendered subject: {status['subject']}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    archive = Path(args.output) / "archive"
    if args.date:
        target = archive / f"{args.date}.md"
    else:
        files = sorted(archive.glob("*.md"))
        target = files[-1] if files else None
    if not target or not target.exists():
        print("No archived edition found.", file=sys.stderr)
        return 1
    print(target.read_text(encoding="utf-8"))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="technews", description="Tech Politics daily brief.")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--config", default="config", help="config directory")
    parser.add_argument("--output", default="output", help="output directory")
    parser.add_argument("--db", default="technews.db", help="SQLite path")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="fetch, score, and render the daily edition")
    p_run.add_argument("--once", action="store_true", help="single run (default)")
    p_run.add_argument("--email", action="store_true", help="also send the email digest")
    p_run.add_argument("--email-dry-run", action="store_true",
                       help="render the email but do not send")
    p_run.set_defaults(func=cmd_run)

    p_fetch = sub.add_parser("fetch", help="fetch sources and report counts")
    p_fetch.set_defaults(func=cmd_fetch)

    p_notify = sub.add_parser("notify", help="build an edition and email it")
    p_notify.add_argument("--dry-run", action="store_true", help="render without sending")
    p_notify.set_defaults(func=cmd_notify)

    p_show = sub.add_parser("show", help="print an archived edition (markdown)")
    p_show.add_argument("--date", help="YYYY-MM-DD (default: latest)")
    p_show.set_defaults(func=cmd_show)

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
