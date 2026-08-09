#!/usr/bin/env python3
"""Validate and publish an immutable signed AnyGo metadata release."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "source" / "clip-db.json"
MAX_PACK_BYTES = 24 * 1024 * 1024
FORBIDDEN = re.compile(rb"(?i)(https?://[^\s\"]+\.(?:m3u8|mp4)|cookie\s*:|authorization\s*:|private[ _-]?key)")


def run(*args: str) -> str:
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def validate(data: bytes) -> dict:
    if not 0 < len(data) <= MAX_PACK_BYTES:
        raise SystemExit("clip database exceeds bounded size")
    if FORBIDDEN.search(data):
        raise SystemExit("forbidden media or secret material found")
    payload = json.loads(data)
    if payload.get("schemaVersion") != 2 or not payload.get("shows") or not payload.get("clips"):
        raise SystemExit("invalid clip database")
    identities: set[tuple[int, int, int]] = set()
    for clip in payload["clips"]:
        required = {"malId", "episode", "part", "startSec", "endSec", "label"}
        if not required.issubset(clip):
            raise SystemExit("clip is missing schema-v1 identity fields")
        identity = (int(clip["malId"]), int(clip["episode"]), int(clip["part"]))
        if identity in identities or float(clip["endSec"]) <= float(clip["startSec"]):
            raise SystemExit("duplicate or invalid clip window")
        identities.add(identity)
    return payload


def main() -> None:
    data = SOURCE.read_bytes()
    payload = validate(data)
    release_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    release_dir = ROOT / "releases" / release_id
    release_dir.mkdir(parents=True, exist_ok=False)
    artifact = release_dir / "clip-db.json"
    shutil.copyfile(SOURCE, artifact)
    (release_dir / "validation-summary.json").write_text(json.dumps({
        "schemaVersion": 1,
        "releaseID": release_id,
        "showCount": len(payload["shows"]),
        "clipCount": len(payload["clips"]),
        "valid": True
    }, indent=2) + "\n")

    subprocess.run(["git", "add", str(release_dir.relative_to(ROOT))], cwd=ROOT, check=True)
    subprocess.run(["git", "commit", "-m", f"Publish immutable pack {release_id}"], cwd=ROOT, check=True)
    subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=ROOT, check=True)
    commit = run("git", "rev-parse", "HEAD")

    digest = hashlib.sha256(data).hexdigest()
    generated_at = datetime.now(timezone.utc)
    manifest = {
        "artifact": {
            "byteSize": len(data),
            "kind": "clipDatabase",
            "sha256": digest,
            "url": f"https://raw.githubusercontent.com/kas021/AnyGo-Packs/{commit}/releases/{release_id}/clip-db.json"
        },
        "generatedAt": generated_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "expiresAt": (generated_at + timedelta(days=7)).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "minimumAppVersion": "1.0.0",
        "releaseID": release_id,
        "schemaVersion": 1
    }
    canonical = json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode()
    private_raw = base64.b64decode(os.environ["ANYGO_ED25519_PRIVATE_KEY_B64"], validate=True)
    signature = Ed25519PrivateKey.from_private_bytes(private_raw).sign(canonical)
    envelope = {
        "payload": base64.b64encode(canonical).decode(),
        "signature": base64.b64encode(signature).decode()
    }
    (ROOT / "latest.json").write_text(json.dumps(envelope, separators=(",", ":"), sort_keys=True) + "\n")
    subprocess.run(["git", "add", "latest.json"], cwd=ROOT, check=True)
    subprocess.run(["git", "commit", "-m", f"Activate signed pack {release_id}"], cwd=ROOT, check=True)
    subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
