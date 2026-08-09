"""Models and deterministic functions for the offline marker pipeline."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

TARGET_SECONDS = 210.0
MIN_SECONDS = 120.0
MAX_SECONDS = 300.0


@dataclass(frozen=True)
class Episode:
    mal_id: int
    episode: int
    duration_sec: float


@dataclass(frozen=True)
class Show:
    mal_id: int
    title: str
    episodes: tuple[Episode, ...]


@dataclass(frozen=True)
class Marker:
    series_key: str
    mal_id: int | None
    canonical_title: str | None
    episode: int
    intro_start: int | None
    intro_end: int | None
    outro_start: int | None
    outro_end: int | None
    source_family: str
    updated_at: str | None = None


@dataclass(frozen=True)
class JoinedMarker:
    mal_id: int
    episode: int
    series_key: str
    canonical_title: str | None
    intro_start: int | None
    intro_end: int | None
    outro_start: int | None
    outro_end: int | None
    source_family: str
    status: str
    review_reason: str | None = None


@dataclass(frozen=True)
class ClipWindow:
    mal_id: int
    episode: int
    part: int
    start_sec: float
    end_sec: float
    label: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "malId": self.mal_id,
            "episode": self.episode,
            "part": self.part,
            "startSec": round(self.start_sec, 3),
            "endSec": round(self.end_sec, 3),
            "label": self.label,
        }


def normalize_title(value: str) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").replace("-", " ").split())


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=False)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def catalogue_from_clip_db(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert the existing exported clip DB into a deterministic catalogue."""
    shows_by_id: dict[int, dict[str, Any]] = {}
    episodes_by_key: dict[tuple[int, int], float] = {}
    show_durations: dict[int, float] = {}
    for show in payload.get("shows", []):
        mal_id = int(show["malId"])
        shows_by_id[mal_id] = {"malId": mal_id, "title": show["title"]}
        if show.get("durationSec") is not None:
            show_durations[mal_id] = float(show["durationSec"])
    for clip in payload.get("clips", []):
        key = (int(clip["malId"]), int(clip["episode"]))
        episodes_by_key[key] = max(episodes_by_key.get(key, 0.0), float(clip["endSec"]))
        shows_by_id.setdefault(key[0], {"malId": key[0], "title": f"Anime #{key[0]}"})
    shows: list[dict[str, Any]] = []
    for mal_id in sorted(shows_by_id):
        episodes = [
            {
                "episode": episode,
                "durationSec": round(max(duration, show_durations.get(mal_id, 0.0)), 3),
                "durationSource": "clip-db-show" if mal_id in show_durations else "clip-db-window",
            }
            for (show_id, episode), duration in sorted(episodes_by_key.items())
            if show_id == mal_id
        ]
        shows.append({
            "malId": mal_id,
            "title": shows_by_id[mal_id]["title"],
            "episodes": episodes,
        })
    return {
        "schemaVersion": 1,
        "source": "existing exported clip-db.json",
        "sourceSchemaVersion": payload.get("schemaVersion"),
        "shows": shows,
    }


def parse_marker(raw: dict[str, Any]) -> Marker:
    return Marker(
        series_key=str(raw.get("seriesKey") or raw.get("series_key") or ""),
        mal_id=int(raw["malId"]) if raw.get("malId") is not None else None,
        canonical_title=raw.get("canonicalTitle"),
        episode=int(raw["episodeNumber"]),
        intro_start=raw.get("introStartSeconds"),
        intro_end=raw.get("introEndSeconds"),
        outro_start=raw.get("outroStartSeconds"),
        outro_end=raw.get("outroEndSeconds"),
        source_family=str(raw.get("sourceFamily") or "episode"),
        updated_at=raw.get("updatedAt"),
    )


def parse_catalogue(payload: dict[str, Any]) -> tuple[Show, ...]:
    shows: list[Show] = []
    for raw_show in payload.get("shows", []):
        episodes = tuple(
            Episode(int(raw_episode["malId"]) if "malId" in raw_episode else int(raw_show["malId"]), int(raw_episode["episode"]), float(raw_episode["durationSec"]))
            for raw_episode in raw_show.get("episodes", [])
        )
        shows.append(Show(int(raw_show["malId"]), str(raw_show["title"]), episodes))
    return tuple(shows)


def join_markers(shows: Iterable[Show], markers: Iterable[Marker]) -> tuple[list[JoinedMarker], list[dict[str, Any]], list[dict[str, Any]]]:
    shows_by_id = {show.mal_id: show for show in shows}
    title_matches: dict[str, list[Show]] = {}
    for show in shows:
        title_matches.setdefault(normalize_title(show.title), []).append(show)

    joined: list[JoinedMarker] = []
    review: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for marker in sorted(markers, key=lambda item: (item.series_key, item.episode, item.source_family)):
        candidates: list[Show] = []
        reason: str | None = None
        if marker.mal_id is not None:
            show = shows_by_id.get(marker.mal_id)
            if show is not None:
                candidates = [show]
            else:
                failed.append({"reason": "mal_id_not_in_catalogue", "marker": marker.__dict__})
                continue
        else:
            candidates = title_matches.get(normalize_title(marker.canonical_title or marker.series_key), [])
            reason = "null_mal_id"
        if len(candidates) > 1:
            item = {"reason": "ambiguous_title_match", "marker": marker.__dict__, "candidateMalIds": [s.mal_id for s in candidates]}
            review.append(item)
            continue
        if not candidates:
            failed.append({"reason": "title_not_in_catalogue", "marker": marker.__dict__})
            continue
        if marker.episode not in {episode.episode for episode in candidates[0].episodes}:
            failed.append({
                "reason": "episode_not_in_catalogue",
                "marker": marker.__dict__,
                "malId": candidates[0].mal_id,
            })
            continue
        key = (candidates[0].mal_id, marker.episode)
        if key in seen:
            failed.append({"reason": "duplicate_join", "marker": marker.__dict__, "malId": key[0]})
            continue
        seen.add(key)
        status = "review" if reason else "matched"
        record = JoinedMarker(candidates[0].mal_id, marker.episode, marker.series_key, marker.canonical_title, marker.intro_start, marker.intro_end, marker.outro_start, marker.outro_end, marker.source_family, status, reason)
        joined.append(record)
        if reason:
            review.append({"reason": reason, "marker": marker.__dict__, "matchedMalId": candidates[0].mal_id})
    return joined, review, failed


def content_bounds(duration: float, marker: JoinedMarker | None) -> tuple[float, float]:
    start = 0.0
    end = duration
    valid_intro = (
        marker is not None
        and marker.intro_start is not None
        and marker.intro_end is not None
        and 0 <= marker.intro_start < marker.intro_end <= duration
    )
    valid_outro = (
        marker is not None
        and marker.outro_start is not None
        and marker.outro_end is not None
        and 0 < marker.outro_start < marker.outro_end <= duration
    )
    if valid_intro:
        start = float(marker.intro_end)
    if valid_outro:
        end = float(marker.outro_start)
    if end <= start:
        raise ValueError("marker ranges leave no content window")
    return start, end


def balanced_chunks(start: float, end: float) -> list[tuple[float, float]]:
    total = end - start
    count = max(1, round(total / TARGET_SECONDS))
    while count > 1 and total / count < MIN_SECONDS:
        count -= 1
    while total / count > MAX_SECONDS:
        count += 1
    size = total / count
    return [(start + i * size, start + (i + 1) * size) for i in range(count)]
