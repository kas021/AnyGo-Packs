# Offline Marker Pipeline

This pipeline creates timestamp metadata only. It never downloads video and never stores HLS URLs, cookies, credentials, headers, or thumbnails.

## Catalogue authority

The default authoritative input is the existing exported `packs/clip-db.json`, which is the repository's last-known-good catalogue snapshot. The default commands are fully offline.

## Run locally

From the repository root:

```sh
python3 tools/marker_pipeline/generate_catalogue.py
python3 tools/marker_pipeline/generate_markers.py
python3 tools/marker_pipeline/generate_clips.py
python3 tools/marker_pipeline/validate_outputs.py
python3 -m unittest discover -s tools/marker_pipeline/tests
```

Use `generate_clips.py --resume` to reuse existing episode output deterministically. No network generation job is performed by these commands.

## Opt-in ONE availability refresh

The production refresh is deliberately opt-in and read-only:

```sh
python3 tools/marker_pipeline/generate_catalogue.py --network
```

It enumerates `/v1/feed/more-fast?page=N` until `hasMore=false`, then requests `/v1/anime/{malId}/episodes`. It includes dubbed episodes, labels cached versus on-demand availability, and never calls a stream endpoint. Requests use bounded workers, timeouts, response limits, retries, ETags, and an atomic resumable checkpoint. A partial refresh raises an error and does not replace the output catalogue.

Useful safety controls include `--max-pages`, `--workers`, `--timeout`, `--retries`, `--response-cap`, and `--checkpoint`. Network mode requires a credential-free HTTPS base URL.

## Window policy

Content is split toward 210 seconds per part. Multiple chunks are constrained to 120–300 seconds. Intro and outro marker ranges are excluded. A missing marker means the full episode is used. Null MAL IDs are retained as review records; ambiguous title matches are never silently joined.

## Output

- `packs/supported-catalogue.v1.json`: catalogue plus generated schema-v1 clips.
- `packs/marker-generation-manifest.v1.json`: deterministic marker joins and review status.
- `reports/`: validation, failed records, ambiguity, and resumable generation reports.
