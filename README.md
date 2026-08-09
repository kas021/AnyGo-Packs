# AnyGo Packs

Signed, metadata-only catalogue releases consumed by the AnyGo iOS app.

This repository contains timestamps, catalogue availability, AniKoto mapping identifiers, schemas, and validation summaries. It must never contain video files, episode URLs, HLS/MP4 addresses, request headers, cookies, credentials, private signing keys, or captured frames.

`latest.json` is a signed envelope. Its `payload` is the base64-encoded canonical manifest bytes and `signature` is an Ed25519 signature over those exact bytes. The manifest points to an immutable commit-pinned artifact and includes its declared size and SHA-256 digest.

Publication is atomic: validation and the immutable artifact commit complete before `latest.json` changes. A failed workflow leaves the previous signed release active.

