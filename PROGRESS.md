# Progress

## Updater adoption — 2026-10-08

Entry release target: **1.0.0**. Independent mTLS Owner and durable admission
source are implemented; source review/fixes and CI integration are complete.
PR [#9](https://github.com/flamoris-jp/flamoris-generation-controller/pull/9) is prepared for human
review. Release publication and real-host adoption remain pending.

## Verification

Local full suite: **295 passed**. All seven application wheel-from-sdist builds and
declared Owner entrypoint/module checks passed. The operator made Updater public,
resolving the initial SDK download 404. SDK source remains pinned to
`d9f010a92ff6e8a1e7a3b7fad8817850bdfb72cd` (Updater PR #6).

[CI run 37765917211](https://github.com/flamoris-jp/flamoris-generation-controller/actions/runs/37765917211):
295 passed on Python 3.11/3.12; lint, package and MCP/HTTP-free installed-core import succeeded. Owner tests read retained reservations without constructing another runtime or clearing unknown work.
These results precede this progress-only commit; package/source dependency pins
are unchanged. Cross-repository findings and exact evidence are recorded in
Updater [ADOPTION_REVIEW.md](https://github.com/flamoris-jp/flamoris-updater/blob/feat/application-entry-v1/docs/ADOPTION_REVIEW.md).

## Operational boundary

The entry path preserves already current application schemas and retained data;
unsupported schemas/resources and unknown outcomes remain blocked. No data/schema
initialization, private profile/trust provisioning, release publication, live
provider call, real-host update, enrollment or automatic merge occurred. Native
deployment overlays and matched dependencies remain deployment-owned.
See [Updater contract](docs/UPDATER.md).
