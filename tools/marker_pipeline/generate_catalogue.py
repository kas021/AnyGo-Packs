#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

if __package__:
    from .models import catalogue_from_clip_db, load_json, write_json
    from .one_catalogue import NetworkOptions, enumerate_one_catalogue
else:
    from models import catalogue_from_clip_db, load_json, write_json
    from one_catalogue import NetworkOptions, enumerate_one_catalogue


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an offline catalogue, or explicitly refresh ONE availability.")
    parser.add_argument("--source", type=Path, default=Path("packs/clip-db.json"))
    parser.add_argument("--output", type=Path, default=Path("packs/supported-catalogue.v1.json"))
    parser.add_argument("--network", action="store_true", help="Opt in to read-only ONE catalogue requests.")
    parser.add_argument("--base-url", default="https://one.synthetiq.uk")
    parser.add_argument("--checkpoint", type=Path, default=Path("reports/one-catalogue-checkpoint.json"))
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--response-cap", type=int, default=2 * 1024 * 1024)
    args = parser.parse_args()
    payload = load_json(args.source)
    offline = catalogue_from_clip_db(payload)
    if args.network:
        options = NetworkOptions(
            base_url=args.base_url,
            checkpoint=args.checkpoint,
            max_pages=args.max_pages,
            workers=args.workers,
            timeout=args.timeout,
            retries=args.retries,
            response_cap=args.response_cap,
        )
        catalogue = enumerate_one_catalogue(options, offline)
    else:
        catalogue = offline
    write_json(args.output, catalogue)
    episode_count = sum(len(show.get("episodes", [])) for show in catalogue["shows"])
    mode = "ONE network refresh" if args.network else "offline export"
    print(f"catalogue ({mode}): {len(catalogue['shows'])} shows, {episode_count} episodes -> {args.output}")


if __name__ == "__main__":
    main()
