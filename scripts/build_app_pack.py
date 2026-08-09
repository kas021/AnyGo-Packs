#!/usr/bin/env python3
"""Build the production app pack from route-certified catalogue metadata."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = ROOT / "packs" / "supported-catalogue.v1.json"
MAPPINGS = ROOT / "packs" / "anikoto-mappings.v1.json"
OUTPUT = ROOT / "source" / "clip-db.json"
QUARANTINE = ROOT / "reports" / "route-quarantine.json"


def main() -> None:
    catalogue = json.loads(CATALOGUE.read_text())
    mapping_payload = json.loads(MAPPINGS.read_text())
    mappings = {
        (int(item["malId"]), int(item["episode"]))
        for item in mapping_payload.get("records", [])
        if item.get("certified") is True and item.get("titleID") and item.get("episodeID")
    }
    eligible: set[tuple[int, int]] = set()
    quarantine: list[dict] = []
    show_rows: list[dict] = []
    clips = catalogue.get("clips", [])
    for show in catalogue.get("shows", []):
        episode_rows = []
        cached_count = 0
        for episode in show.get("episodes", []):
            key = (int(show["malId"]), int(episode["episode"]))
            cached = episode.get("availability") == "oneCached" or episode.get("streamCachedDub") is True
            mapped = key in mappings
            if cached or mapped:
                eligible.add(key)
                episode_rows.append(episode)
                cached_count += int(cached)
            else:
                quarantine.append({
                    "malId": key[0],
                    "episode": key[1],
                    "reason": "on_demand_without_certified_anikoto_mapping"
                })
        if episode_rows:
            show_clips = [item for item in clips if (int(item["malId"]), int(item["episode"])) in eligible and int(item["malId"]) == int(show["malId"])]
            show_rows.append({
                "malId": int(show["malId"]),
                "title": str(show["title"]),
                "episodeCount": len(episode_rows),
                "clipCount": len(show_clips),
                "oneCachedEpisodes": cached_count
            })
    app_clips = []
    for clip in clips:
        key = (int(clip["malId"]), int(clip["episode"]))
        if key not in eligible:
            continue
        app_clips.append({
            "malId": key[0],
            "episode": key[1],
            "season": 1,
            "part": int(clip["part"]),
            "startSec": float(clip["startSec"]),
            "endSec": float(clip["endSec"]),
            "label": str(clip["label"]),
            "hasMarkers": False
        })
    if not app_clips:
        raise SystemExit("no route-certified clips")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({
        "schemaVersion": 2,
        "shows": sorted(show_rows, key=lambda item: item["malId"]),
        "clips": sorted(app_clips, key=lambda item: (item["malId"], item["episode"], item["part"]))
    }, separators=(",", ":")) + "\n")
    QUARANTINE.parent.mkdir(parents=True, exist_ok=True)
    QUARANTINE.write_text(json.dumps({
        "schemaVersion": 1,
        "recordCount": len(quarantine),
        "records": quarantine
    }, indent=2) + "\n")
    print(f"eligible clips={len(app_clips)} quarantined episodes={len(quarantine)}")


if __name__ == "__main__":
    main()

