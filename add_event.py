#!/usr/bin/env python3
"""Scaffold a new event draft.

    python add_event.py "My Event Name"        # blank template -> events/<slug>.yaml
    python add_event.py --url <eventernote_url> # delegate to the scrape helper
    python add_event.py --url <url> --id slug

Then fill in the lottery rounds and run `python -m build.build_site`.
"""

from __future__ import annotations

import argparse
import sys

from scrape.cli import main as scrape_main
from scrape.util import EVENTS_DIR, render_draft_yaml, slugify


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="add_event")
    ap.add_argument("name", nargs="?", help="event name for a blank draft")
    ap.add_argument("--url", help="scrape this URL instead of a blank draft")
    ap.add_argument("--id", help="output slug / filename stem")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    if args.url:
        passthru = [args.url]
        if args.id:
            passthru += ["--id", args.id]
        if args.force:
            passthru += ["--force"]
        return scrape_main(passthru)

    if not args.name:
        ap.error("provide an event name or --url")

    slug = args.id or slugify(args.name)
    out = EVENTS_DIR / f"{slug}.yaml"
    if out.exists() and not args.force:
        print(f"✗ {out} exists; pass --force or --id <other>", file=sys.stderr)
        return 1
    out.write_text(render_draft_yaml({"name": args.name}), encoding="utf-8")
    print(f"✓ wrote blank draft {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
