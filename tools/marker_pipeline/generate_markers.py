#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

if __package__:
    from .models import join_markers, load_json, parse_catalogue, parse_marker, write_json
else:
    from models import join_markers, load_json, parse_catalogue, parse_marker, write_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Join exported intro/outro markers to the offline catalogue.")
    parser.add_argument("--catalogue", type=Path, default=Path("packs/supported-catalogue.v1.json"))
    parser.add_argument("--markers", type=Path, default=Path("packs/markers.v1.json"))
    parser.add_argument("--output", type=Path, default=Path("packs/marker-generation-manifest.v1.json"))
    parser.add_argument("--reports", type=Path, default=Path("reports"))
    args = parser.parse_args()
    shows = parse_catalogue(load_json(args.catalogue))
    raw_markers = load_json(args.markers).get("markers", [])
    joined, review, failed = join_markers(shows, (parse_marker(raw) for raw in raw_markers))
    manifest = {
        "schemaVersion": 1,
        "source": "packs/markers.v1.json joined to packs/supported-catalogue.v1.json",
        "records": [
            {
                "malId": item.mal_id,
                "episode": item.episode,
                "seriesKey": item.series_key,
                "canonicalTitle": item.canonical_title,
                "introStartSec": item.intro_start,
                "introEndSec": item.intro_end,
                "outroStartSec": item.outro_start,
                "outroEndSec": item.outro_end,
                "sourceFamily": item.source_family,
                "status": item.status,
                "reviewReason": item.review_reason,
            }
            for item in joined
        ],
    }
    write_json(args.output, manifest)
    ambiguous = [item for item in review if item["reason"] == "ambiguous_title_match"]
    review_records = [item for item in review if item["reason"] != "ambiguous_title_match"]
    write_json(args.reports / "ambiguous-markers.json", {
        "schemaVersion": 1,
        "recordCount": len(ambiguous),
        "reviewRecordCount": len(review_records),
        "records": ambiguous,
        "reviewRecords": review_records,
    })
    write_json(args.reports / "failed-records.json", {
        "schemaVersion": 1,
        "recordCount": len(failed),
        "records": failed,
    })
    print(f"joined: {len(joined)}, review: {len(review)}, failed: {len(failed)}")


if __name__ == "__main__":
    main()
