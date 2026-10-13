# Production reference images — 2026-10-12

Controller #13 implements IPAdapter appearance/style and prepared-map ControlNet profiles, multi-image staging and schema 8; retains schema-7 img2img. Includes concrete ComfyWorkFlow examples and a local graph-writing helper. Source target 1.0.1, not a published release. Current local checks: 319 domain/API/CLI tests, Ruff and wheel/sdist build pass; CI receipts live with the PR. Studio #79 is the matching UI/owner integration. No deployment, model download or live inference occurred.

---

# Repository-owned library release preparation — 2026-10-11

The operator requested preparing each repository for Updater installation. Controller publishes its own library wheel/source; Generation MCP embeds its fixed dependency and publishes the runnable image/catalog. No second daemon or authority is introduced. Library publication/CI and real-host acceptance are separate; no host/data/provider actions are performed.

---

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

## Pre-deployment retained-resource correction

The private inventory review found separate retained input, definition and
recipe trees in addition to the Controller output root. The Owner contract now
requires `configuration`, `definitions`, `inputs`, `recipes`, and `outputs`;
the output resource continues to contain managed inputs/uploads, generated
assets, reservations, journals and the authority lock. Shared models remain an
external read-only library. Linux verification: **296 passed** plus Ruff
check/format for the full source and test trees. The SDK is fixed to merged
Updater revision `797d6f4e7bd4089e7c162fa50c10a0afae68370a`; PR review and CI at the
new revision remain pending. No live service or data was changed.


## 2026-10-13 — linked external gallery (source preparation)

Controller #16 adds internal-only `jobs.external.list` and a bounded durable recent index. Exact actor isolation, strict permissions/arguments, metadata-only completion observation, restart, pagination and corrupt-index rejection are covered by deterministic tests. Source preparation is not yet merged or deployed; the matched Generation pin and Studio #86 integration are required.

## 2026-10-13 — linked discovery accepted

Controller #17 is merged and v1.0.2 published. Final validation passed 313 tests, Python 3.11/3.12 CI, lint/format and installed core without MCP/HTTP dependencies. Matched Generation #84 and Studio #87 are merged; Generation 1.0.5 embeds this library and Studio 1.0.6 implements the bounded linked gallery and Music grouping. Operator app updates succeeded. Individual Hub credentials, matching signed provenance, exact Studio bindings and real browser/two-client publication still require operational acceptance; no anonymous history is assigned automatically.
