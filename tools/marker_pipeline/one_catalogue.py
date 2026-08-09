"""Bounded, read-only ONE catalogue enumeration.

This module deliberately exposes no stream endpoint. Network access only occurs
when ``generate_catalogue.py --network`` is explicitly selected.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

if __package__:
    from .models import load_json, write_json
else:
    from models import load_json, write_json


class CatalogueNetworkError(RuntimeError):
    """Raised when a bounded catalogue refresh cannot complete safely."""


@dataclass(frozen=True)
class NetworkOptions:
    base_url: str
    checkpoint: Path
    max_pages: int = 100
    workers: int = 4
    timeout: float = 12.0
    retries: int = 2
    response_cap: int = 2 * 1024 * 1024

    def validated(self) -> "NetworkOptions":
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("ONE base URL must be credential-free HTTPS")
        if not 1 <= self.max_pages <= 500:
            raise ValueError("max-pages must be between 1 and 500")
        if not 1 <= self.workers <= 8:
            raise ValueError("workers must be between 1 and 8")
        if not 1 <= self.timeout <= 60:
            raise ValueError("timeout must be between 1 and 60 seconds")
        if not 0 <= self.retries <= 5:
            raise ValueError("retries must be between 0 and 5")
        if not 1024 <= self.response_cap <= 8 * 1024 * 1024:
            raise ValueError("response-cap must be between 1 KiB and 8 MiB")
        return self


Fetch = Callable[[str, str | None, NetworkOptions], tuple[str | None, dict[str, Any] | None]]


def fetch_json(url: str, etag: str | None, options: NetworkOptions) -> tuple[str | None, dict[str, Any] | None]:
    """Fetch one bounded JSON document, returning ``None`` for HTTP 304."""
    headers = {"Accept": "application/json", "User-Agent": "AnyGoMarkerPipeline/1.0"}
    if etag:
        headers["If-None-Match"] = etag
    request = urllib.request.Request(url, headers=headers, method="GET")
    last_error: Exception | None = None
    for attempt in range(options.retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=options.timeout) as response:
                content_type = response.headers.get_content_type()
                if content_type not in {"application/json", "text/json"}:
                    raise CatalogueNetworkError(f"unexpected content type from {url}: {content_type}")
                body = response.read(options.response_cap + 1)
                if len(body) > options.response_cap:
                    raise CatalogueNetworkError(f"response exceeded byte cap: {url}")
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise CatalogueNetworkError(f"response root must be an object: {url}")
                return response.headers.get("ETag"), payload
        except urllib.error.HTTPError as error:
            if error.code == 304:
                return etag, None
            last_error = error
            if error.code < 500 and error.code != 429:
                break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, CatalogueNetworkError) as error:
            last_error = error
            if isinstance(error, CatalogueNetworkError):
                break
        if attempt < options.retries:
            time.sleep(0.25 * (2**attempt))
    raise CatalogueNetworkError(f"request failed after bounded retries: {url}: {last_error}")


def _items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidate: Any = payload.get("items")
    if candidate is None and isinstance(payload.get("data"), dict):
        candidate = payload["data"].get("items")
    if not isinstance(candidate, list) or not all(isinstance(item, dict) for item in candidate):
        raise CatalogueNetworkError("ONE response is missing an object items array")
    return candidate


def _has_more(payload: dict[str, Any]) -> bool:
    value: Any = payload.get("hasMore")
    if value is None and isinstance(payload.get("data"), dict):
        value = payload["data"].get("hasMore")
    if not isinstance(value, bool):
        raise CatalogueNetworkError("ONE feed response is missing boolean hasMore")
    return value


def _mal_id(item: dict[str, Any]) -> int:
    raw = item.get("malId", item.get("mal_id"))
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        raise CatalogueNetworkError("ONE feed item has no valid MAL ID")
    value = int(raw)
    if value <= 0:
        raise CatalogueNetworkError("ONE feed item MAL ID must be positive")
    return value


def _checkpoint(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schemaVersion": 1, "feedPages": {}, "episodes": {}}
    value = load_json(path)
    if value.get("schemaVersion") != 1:
        raise CatalogueNetworkError("unsupported checkpoint schema")
    value.setdefault("feedPages", {})
    value.setdefault("episodes", {})
    return value


def _cached_fetch(
    url: str,
    cache_record: dict[str, Any] | None,
    options: NetworkOptions,
    fetch: Fetch,
) -> dict[str, Any]:
    etag, payload = fetch(url, (cache_record or {}).get("etag"), options)
    if payload is None:
        cached = (cache_record or {}).get("payload")
        if not isinstance(cached, dict):
            raise CatalogueNetworkError(f"received 304 without cached payload: {url}")
        return {"etag": etag, "payload": cached}
    return {"etag": etag, "payload": payload}


def _sanitized_feed(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist only catalogue identity fields, never arbitrary service data."""
    sanitized = []
    for item in _items(payload):
        sanitized.append({
            "malId": _mal_id(item),
            "title": str(item.get("title") or f"Anime #{_mal_id(item)}"),
        })
    return {"items": sanitized, "hasMore": _has_more(payload)}


def _sanitized_episodes(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = []
    for item in _items(payload):
        number = item.get("number", item.get("episode"))
        sanitized.append({
            "number": number,
            "dubAvailable": item.get("dubAvailable") is True,
            "streamCachedDub": item.get("streamCachedDub") is True,
        })
    return {"items": sanitized}


def enumerate_one_catalogue(
    options: NetworkOptions,
    offline_catalogue: dict[str, Any],
    fetch: Fetch = fetch_json,
) -> dict[str, Any]:
    """Enumerate dubbed availability without resolving or persisting media URLs."""
    options = options.validated()
    base = options.base_url.rstrip("/")
    state = _checkpoint(options.checkpoint)
    feed_items: dict[int, dict[str, Any]] = {}
    completed_feed = False

    for page in range(1, options.max_pages + 1):
        key = str(page)
        url = f"{base}/v1/feed/more-fast?page={page}"
        record = _cached_fetch(url, state["feedPages"].get(key), options, fetch)
        record["payload"] = _sanitized_feed(record["payload"])
        state["feedPages"][key] = record
        write_json(options.checkpoint, state)
        payload = record["payload"]
        for item in _items(payload):
            mal_id = _mal_id(item)
            current = feed_items.get(mal_id)
            if current is not None and current != item:
                raise CatalogueNetworkError(f"conflicting duplicate MAL ID in feed: {mal_id}")
            feed_items[mal_id] = item
        if not _has_more(payload):
            completed_feed = True
            break
    if not completed_feed:
        raise CatalogueNetworkError("feed pagination reached max-pages before hasMore=false")

    def load_episodes(mal_id: int) -> tuple[int, dict[str, Any]]:
        key = str(mal_id)
        url = f"{base}/v1/anime/{mal_id}/episodes"
        record = _cached_fetch(url, state["episodes"].get(key), options, fetch)
        record["payload"] = _sanitized_episodes(record["payload"])
        return mal_id, record

    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=options.workers) as executor:
        futures = {executor.submit(load_episodes, mal_id): mal_id for mal_id in sorted(feed_items)}
        for future in as_completed(futures):
            mal_id = futures[future]
            try:
                _, record = future.result()
                state["episodes"][str(mal_id)] = record
                write_json(options.checkpoint, state)
            except Exception as error:  # consolidate failures after bounded workers finish
                failures.append(f"{mal_id}: {error}")
    if failures:
        raise CatalogueNetworkError("episode enumeration incomplete: " + "; ".join(sorted(failures)))

    offline: dict[tuple[int, int], dict[str, Any]] = {
        (int(show["malId"]), int(episode["episode"])): episode
        for show in offline_catalogue.get("shows", [])
        for episode in show.get("episodes", [])
    }
    shows: list[dict[str, Any]] = []
    for mal_id, feed_item in sorted(feed_items.items()):
        response = state["episodes"][str(mal_id)]["payload"]
        episode_rows: list[dict[str, Any]] = []
        seen_episodes: set[int] = set()
        for item in _items(response):
            if item.get("dubAvailable") is not True:
                continue
            number_raw = item.get("number", item.get("episode"))
            if isinstance(number_raw, bool) or not isinstance(number_raw, (int, str)):
                raise CatalogueNetworkError(f"invalid episode number for MAL {mal_id}")
            number = int(number_raw)
            if number <= 0 or number in seen_episodes:
                raise CatalogueNetworkError(f"invalid or duplicate episode {number} for MAL {mal_id}")
            seen_episodes.add(number)
            prior = offline.get((mal_id, number), {})
            duration = float(prior.get("durationSec", 1440))
            cached = item.get("streamCachedDub") is True
            episode_rows.append({
                "episode": number,
                "durationSec": round(duration, 3),
                "durationSource": prior.get("durationSource", "fallback-1440"),
                "dubAvailable": True,
                "streamCachedDub": cached,
                "availability": "oneCached" if cached else "onDemand",
            })
        if episode_rows:
            shows.append({
                "malId": mal_id,
                "title": str(feed_item.get("title") or f"Anime #{mal_id}"),
                "episodes": sorted(episode_rows, key=lambda item: item["episode"]),
            })
    return {
        "schemaVersion": 1,
        "source": "Synthetiq ONE read-only catalogue enumeration",
        "refreshStatus": "complete",
        "shows": shows,
    }
