#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

if __package__:
    from .models import MAX_SECONDS, MIN_SECONDS, load_json, write_json
else:
    from models import MAX_SECONDS, MIN_SECONDS, load_json, write_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate offline catalogue, manifest, and schema-v1 clips.")
    parser.add_argument("--catalogue", type=Path, default=Path("packs/supported-catalogue.v1.json"))
    parser.add_argument("--manifest", type=Path, default=Path("packs/marker-generation-manifest.v1.json"))
    parser.add_argument("--report", type=Path, default=Path("reports/validation-report.json"))
    args = parser.parse_args()
    catalogue = load_json(args.catalogue)
    manifest = load_json(args.manifest)
    errors: list[str] = []
    clips = catalogue.get("clips", [])
    seen: set[tuple[int, int, int]] = set()
    by_episode: dict[tuple[int, int], list[dict]] = {}
    durations = {(int(s["malId"]), int(e["episode"])): float(e["durationSec"]) for s in catalogue.get("shows", []) for e in s.get("episodes", [])}
    markers = {(int(m["malId"]), int(m["episode"])): m for m in manifest.get("records", []) if m.get("status") in {"matched", "review"}}
    for clip in clips:
        key = (int(clip["malId"]), int(clip["episode"]), int(clip["part"]))
        if key in seen:
            errors.append(f"duplicate clip {key}")
        seen.add(key)
        if set(clip) != {"malId", "episode", "part", "startSec", "endSec", "label"}:
            errors.append(f"schema-v1 keys invalid for {key}")
        start, end = float(clip["startSec"]), float(clip["endSec"])
        duration = durations.get((key[0], key[1]))
        if duration is None or not (0 <= start < end <= duration + 0.001):
            errors.append(f"bounds invalid for {key}")
        by_episode.setdefault((key[0], key[1]), []).append(clip)
    for key, episode_clips in by_episode.items():
        ordered = sorted(episode_clips, key=lambda c: c["part"])
        if [int(item["part"]) for item in ordered] != list(range(1, len(ordered) + 1)):
            errors.append(f"part sequence invalid in {key}")
        duration = durations.get(key)
        for left, right in zip(ordered, ordered[1:]):
            if abs(float(left["endSec"]) - float(right["startSec"])) > 0.01:
                errors.append(f"gap or overlap in {key}")
        marker = markers.get(key)
        if marker:
            intro_start = marker.get("introStartSec")
            intro_end = marker.get("introEndSec")
            outro_start = marker.get("outroStartSec")
            outro_end = marker.get("outroEndSec")
            valid_intro = duration is not None and intro_start is not None and intro_end is not None and 0 <= intro_start < intro_end <= duration
            valid_outro = duration is not None and outro_start is not None and outro_end is not None and 0 < outro_start < outro_end <= duration
            if valid_intro and valid_outro and float(intro_end) >= float(outro_start):
                valid_intro = False
                valid_outro = False
            if valid_intro and float(ordered[0]["startSec"]) < float(intro_end):
                errors.append(f"intro overlap in {key}")
            if valid_outro and float(ordered[-1]["endSec"]) > float(outro_start):
                errors.append(f"outro overlap in {key}")
        for clip in ordered:
            length = float(clip["endSec"]) - float(clip["startSec"])
            if len(ordered) > 1 and not (MIN_SECONDS - 0.01 <= length <= MAX_SECONDS + 0.01):
                errors.append(f"chunk length out of bounds in {key}: {length}")
    missing_episodes = sorted(set(durations) - set(by_episode))
    for key in missing_episodes:
        errors.append(f"episode has no clips {key}")
    report = {
        "schemaVersion": 1,
        "valid": not errors,
        "errorCount": len(errors),
        "showCount": len(catalogue.get("shows", [])),
        "episodeCount": len(durations),
        "clipCount": len(clips),
        "markerRecordCount": len(manifest.get("records", [])),
        "errors": errors,
    }
    write_json(args.report, report)
    print(f"valid={report['valid']} errors={len(errors)} clips={len(clips)}")
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
