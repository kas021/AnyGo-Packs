#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

if __package__:
    from .models import balanced_chunks, content_bounds, load_json, write_json
else:
    from models import balanced_chunks, content_bounds, load_json, write_json


def marker_index(manifest: dict) -> dict[tuple[int, int], dict]:
    return {(int(item["malId"]), int(item["episode"])): item for item in manifest.get("records", []) if item.get("status") in {"matched", "review"}}


def generate(catalogue: dict, manifest: dict, existing: dict | None = None) -> tuple[dict, dict]:
    markers = marker_index(manifest)
    previous: dict[tuple[int, int], list[dict]] = {}
    for clip in (existing or {}).get("clips", []):
        previous.setdefault((int(clip["malId"]), int(clip["episode"])), []).append(clip)
    clips: list[dict] = []
    reused = 0
    generated = 0
    marker_fallbacks: list[dict] = []
    for show in sorted(catalogue.get("shows", []), key=lambda item: int(item["malId"])):
        for episode in sorted(show.get("episodes", []), key=lambda item: int(item["episode"])):
            key = (int(show["malId"]), int(episode["episode"]))
            duration = float(episode["durationSec"])
            raw_marker = markers.get(key)
            marker = None
            if raw_marker:
                marker = type("Marker", (), {
                    "intro_start": raw_marker.get("introStartSec"),
                    "intro_end": raw_marker.get("introEndSec"),
                    "outro_end": raw_marker.get("outroEndSec"),
                    "outro_start": raw_marker.get("outroStartSec"),
                })()
            try:
                start, end = content_bounds(duration, marker)
                windows = balanced_chunks(start, end)
            except ValueError as error:
                # Invalid marker combinations are quarantined in the report,
                # while the episode remains available using honest full bounds.
                start, end = content_bounds(duration, None)
                windows = balanced_chunks(start, end)
                marker_fallbacks.append({
                    "malId": key[0],
                    "episode": key[1],
                    "reason": str(error),
                })
            expected = []
            for part, (window_start, window_end) in enumerate(windows, 1):
                expected.append({
                    "malId": key[0],
                    "episode": key[1],
                    "part": part,
                    "startSec": round(window_start, 3),
                    "endSec": round(window_end, 3),
                    "label": f"E{key[1]} P{part}",
                })
            prior = sorted(previous.get(key, []), key=lambda item: int(item["part"]))
            if prior == expected:
                clips.extend(prior)
                reused += 1
            else:
                clips.extend(expected)
                generated += 1
    output = dict(catalogue)
    output.update({"clips": sorted(clips, key=lambda item: (item["malId"], item["episode"], item["part"])), "clipSchemaVersion": 1})
    episode_count = sum(len(show.get("episodes", [])) for show in catalogue.get("shows", []))
    return output, {
        "schemaVersion": 1,
        "showCount": len(catalogue.get("shows", [])),
        "episodeCount": episode_count,
        "episodesGenerated": generated,
        "episodesReused": reused,
        "clipCount": len(clips),
        "markerFallbackCount": len(marker_fallbacks),
        "markerFallbacks": marker_fallbacks,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate deterministic intro/outro-free clip windows.")
    parser.add_argument("--catalogue", type=Path, default=Path("packs/supported-catalogue.v1.json"))
    parser.add_argument("--manifest", type=Path, default=Path("packs/marker-generation-manifest.v1.json"))
    parser.add_argument("--output", type=Path, default=Path("packs/supported-catalogue.v1.json"))
    parser.add_argument("--reports", type=Path, default=Path("reports"))
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    catalogue = load_json(args.catalogue)
    existing = load_json(args.output) if args.resume and args.output.exists() else None
    output, summary = generate(catalogue, load_json(args.manifest), existing)
    write_json(args.output, output)
    write_json(args.reports / "generation-summary.json", summary)
    print(f"generated: {summary['clipCount']} clips; {summary['episodesReused']} episodes reused")


if __name__ == "__main__":
    main()
