# Progress

## Updater adoption — 2026-10-08

Entry release target: 1.0.0. Source adds durable admission and an independent
mTLS Owner with application-owned schema/resource inspection. Review/testing
and matched dependency wiring are in progress. No release or live update is
claimed. See [Updater contract](docs/UPDATER.md).


## Updater entry review checkpoint

Version 1.0.0 adds the independent application Owner and durable admission
through the MCP-independent SDK. Local complete suite: **295 passed**. Ruff
check/format passed. Owner tests prove retained reservations are read without
constructing a second Controller, and unknown work remains unchanged/blocked.
The SDK is pinned to d9f010a92ff6e8a1e7a3b7fad8817850bdfb72cd. Updater PR #6
records the real isolated PostgreSQL restore and native bundle validation.
Matched facade/Studio pins and final application CI remain under review.
No release, live change, private profile or enrollment is performed.
