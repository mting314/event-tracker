"""Scrape a URL into a draft event YAML.

    python -m scrape.cli <url>                 # auto-detect source by domain
    python -m scrape.cli <url> --id my-slug    # force the output filename
    python -m scrape.cli --text "<pasted>"     # parse pasted text (X posts)

Writes ``events/<slug>.yaml`` (never overwrites without --force). The draft is a
starting point: fill in the lottery rounds and series tags, then commit.
"""

from __future__ import annotations

import argparse
import sys

from . import x_post
from .ingest import ingest_url
from .util import EVENTS_DIR, slugify, to_event_yaml


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scrape.cli")
    ap.add_argument("url", nargs="?", help="event URL to scrape")
    ap.add_argument("--text", help="parse pasted text instead of fetching (X posts)")
    ap.add_argument("--id", help="output slug / filename stem")
    ap.add_argument("--force", action="store_true", help="overwrite an existing file")
    ap.add_argument("--llm", action="store_true", help="force LLM (Vertex) extraction")
    ap.add_argument("--no-llm", action="store_true", help="disable the LLM fallback")
    args = ap.parse_args(argv)

    if not args.url and not args.text:
        ap.error("provide a URL or --text")

    if args.text:
        data = x_post.parse_text(args.text, args.url)
    else:
        try:
            res = ingest_url(args.url, allow_llm=not args.no_llm, force_llm=args.llm)
            data = res.data
            if res.used_llm:
                print("(used LLM extraction via Vertex)")
        except Exception as exc:  # noqa: BLE001 - report cleanly, still write a stub
            print(f"⚠️  ingest failed: {exc}", file=sys.stderr)
            data = {"source_url": args.url, "rounds": []}

    dates = data.get("event_dates") or [
        p["date"] for p in data.get("performances", []) if p.get("date")
    ]
    slug = args.id or slugify(data.get("name") or "", dates)
    out = EVENTS_DIR / f"{slug}.yaml"
    if out.exists() and not args.force:
        print(f"✗ {out} exists; pass --force or --id <other>", file=sys.stderr)
        return 1

    out.write_text(to_event_yaml(data), encoding="utf-8")
    print(f"✓ wrote draft {out}")
    print("  → fill in the lottery rounds + series tags, then `python -m build.build_site`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
