# Controller implementation plan

Updated: 2026-10-05 (JST). Parent decision: [AI #18](https://github.com/flamoris-jp/flamoris-ai/issues/18); local coordination: [Controller #1](https://github.com/flamoris-jp/flamoris-generation-controller/issues/1).

The user first requested repository/policy preparation and has now explicitly requested Controller implementation. Controller #4 and AI #24 are merged. This source implements the retained domain and matched adapters; source acceptance and live cutover are separate. No live deployment is performed.

## Goal and bounded scope

Give Studio and the external Generation MCP facade one non-MCP generation contract and one state owner. Reuse the retained generation domain rather than build a new orchestration platform.

Preserve the two builtin image templates, configured native Speech/Music/transcription recipes, model discovery, job/result/cancel behavior, managed inputs, generated assets and existing limits/failure protections. Feature support and real provider availability do not expand merely because ownership changes.

Studio calls Controller directly through the internal contract. Generation MCP translates external calls into that same contract. Controller does not route Studio through MCP, require an Agent or Runtime bridge, or manage host-wide GPU lifecycle.

## Reviewed source anchors

The inventory below was read against these current-main revisions. Re-check source and owner instructions before a code task; these are planning anchors, not deployment claims.

| Repository | Revision | Relevant source |
| --- | --- | --- |
| Generation MCP | `02ce5e23f2c6cd311181169e36e58a0eb6eb6ff4` | [Domain and facade](https://github.com/flamoris-jp/flamoris-generation-mcp/tree/02ce5e23f2c6cd311181169e36e58a0eb6eb6ff4/src/flamoris_generation_mcp) |
| Studio | `deb0dd9f796e3f47b3eb35ed631ea9cacb18ebdd` | [Gateway and product boundary](https://github.com/flamoris-jp/flamoris-studio/tree/deb0dd9f796e3f47b3eb35ed631ea9cacb18ebdd/flamoris_studio) |
| Controller | `4b6babf08cf7613fc15d71b541a57296790fa4a0` | [Documentation-only baseline](https://github.com/flamoris-jp/flamoris-generation-controller/tree/4b6babf08cf7613fc15d71b541a57296790fa4a0) |

## Source-to-owner inventory

Generation paths are relative to `src/flamoris_generation_mcp/`; Studio paths are relative to `flamoris_studio/`. This class/module inventory guides a later dependency/test audit; it is not an instruction to copy every file unchanged.

| Current source | Planned treatment | Preserve or separate |
| --- | --- | --- |
| Generation `jobs.py`, `durable.py` | Controller's single generation authority | Reservation-before-submit, durable unknown recovery, scoped cancel, job/result/asset identities and archived metadata |
| `workflows.py`, `speech.py`, `music.py`, `transcription.py` | Retained recipe contracts/build/save in Controller | Original schema 1/4/5/6, template/model/ordered-LoRA checks; exact existing recipe identifiers |
| `models.py`, `capabilities.py`, `providers/`, `comfyui.py` | Reuse domain catalogs and provider adapters | Provider execution stays in the actual provider; health, routes, declared outputs and safe failure behavior remain bounded |
| `inputs.py`, `input_uploads.py`, `image_decode.py` and provider staging/copy ledgers | Controller input publication and use protection | Immutable digest/size/type, expiry, decode/staging bounds, lease and reservation coupling |
| `asset_files.py`, `transfers.py`, `retention.py` and job asset operations | Controller asset storage, transfer and retention | Confinement, integrity, deletion/transfer locks and storage identity; transport-specific binary encoding is adapter work |
| `server.py:create_server` | Separate shared domain construction/lifecycle from MCP startup | One provider registry, recipe store, JobStore, transfer/input service and shutdown owner |
| `config.py`, `provenance.py` | Split by responsibility | Provider/storage settings and verified provenance values belong to core; MCP bind/path settings, signed-envelope/replay ingress and SDK errors stay in the facade |
| Generation `server.py` tool handlers | Remain external MCP adapter | Existing tool names, annotations, argument validation, ToolError and MCP Image/content mapping |
| Studio `gateway.py` | Replace upstream MCP transport with the reviewed Controller contract | Safe errors, bounds/timeouts, acknowledgement/result validation and no uncertain-submit retry; do not move the MCP client into Controller |
| Studio `workflow_contract.py`, `speech_contract.py`, `music_contract.py`, `result_contract.py` | Share generation specification where appropriate; keep consumer checks | Core owns domain constraints/profiles; Studio keeps browser DTO mapping, product limits and validation of untrusted results |
| Studio `app.py`, `speech.py`, `music.py`, `generation_requests.py`, input/media/transfer modules and `db.py` | Keep Studio product responsibilities | Accounts/CSRF, owner-scoped opaque handles, history/presets, request admission fences, snapshots, previews/downloads and publication rechecks |

For example, Studio's image seed range is constrained by browser-safe integers, while the current Generation recipe accepts a larger integer range. Sharing generation contracts does not require erasing a legitimate consumer limit or removing validation at ingress.

The deleted custom registry/versioning/composition/qualification/v3/Runtime-delegation code is absent from this inventory. It is not a migration source. Existing `WorkflowStore` supports retained recipes across providers and is not deleted merely because of its name.

The inventory above is the original extraction baseline. The later accepted
[registration profile](COMFY_REGISTRATION.md) adds `comfy.register/get`, immutable
definitions and schema-7 builds; current HTTP and external catalogs have 25
operations. It does not restore the retired subsystem.

## Decisions selected before extraction

| Decision | Required result |
| --- | --- |
| Packaging and imports | `flamoris-generation-controller` / `flamoris_generation_controller`, MCP-free base dependencies; optional `http` extra; installed-package checks |
| Hosting and lifecycle | One runtime in the existing Generation MCP HTTP process. Both adapters share it; output-root lifetime lock rejects duplicate processes before construction/recovery |
| Minimum contract | POST `/api/v1/generation/{operation}`, 23 explicitly allowlisted retained operations, strict JSON request models, direct object results and bounded image bytes; the initial 23-operation baseline is now extended by `comfy.register/get` to 25; see [API.md](API.md) |
| Trusted caller context | Separate operator service credential authenticates Studio backend and grants the bounded API surface. Studio retains user authorization; MCP ingress supplies only verified non-secret provenance. Context is not a request field |
| DTO and failure mapping | Existing IDs/output metadata retained; constant HTTP error codes; external ToolError/Image translation remains in facade; no retry/fallback |
| State compatibility | Existing roots/journals/archive formats remain unchanged. Only `controller-authority/owner.lock` is added; cutover drains/reconciles old versions; see [MIGRATION.md](MIGRATION.md) |

A core package imported by two frontend processes does not make their state shared. Separate JobStores can both admit generation. A shared output directory also does not turn process-local locks into cross-process exclusion. The hosting decision must resolve this before moving code; a distributed scheduler is not part of this scope.

An operation may finish upstream even if acknowledgement or publication fails. Preserve Controller's generation reservation and Studio's owned request fence separately. Never turn timeout, restart or transport replacement into permission to submit again.

## Staged source work

| Stage | Deliverable | Completion evidence |
| --- | --- | --- |
| 1. Contract and hosting | Resolve the decisions above in Controller #1 using retained consumers | Reviewed owner/contract/state map and focused implementation scope |
| 2. Core and lifecycle | Reuse retained domain code, split MCP imports/config/ingress, construct one runtime | MCP-free installed imports and existing domain/provider contract checks |
| 3. External facade | Generation MCP calls that runtime without another store | Retained external tool/catalog/content compatibility and shared admission tests |
| 4. Studio integration | Non-MCP gateway plus shared specification as appropriate | Image/Speech/Music/input/asset paths, browser DTOs, two-account isolation and publication guards |
| 5. Source acceptance | Review/fix affected owners and record exact commits/checks | Passing applicable CI, no retired features, no competing state authority and updated AI progress |
| 6. Operational cutover | Inventory/drain/reconcile, fixed artifacts, backup/rollback and live acceptance | Separately scoped operational evidence; source tests alone are insufficient |

Stages 2–4 must not activate competing old/new authorities during transition. Compatibility remains usable until its replacement is reviewed; fallback must not replay work or restore a retired feature. Offline contract/source work does not require completing earlier live deployment first, but activating a new owner does require state reconciliation.

## Acceptance for later implementation

- Core imports and installed packaging work without MCP, Hub or Studio dependencies.
- Studio and external MCP requests contend for the same generation reservation, including different configured providers.
- Failed submit acknowledgement, provider timeout, uncertain journal commit and restart retain unknown/no-replay behavior. Retired active debt remains opaque and reserved.
- Recipe IDs, native profiles, model validation, ordered LoRA semantics and supported external tools remain compatible or explicitly versioned.
- Scoped cancellation, input-use protection, upload integrity/expiry, declared output roles, archival retrieval/deletion, bounded transfer and retention preserve their existing contracts.
- Verified provenance is recorded without granting ownership. Internal authentication and operation permissions are exercised, not assumed from deployment topology.
- Studio ownership/CSRF, duplicate-request fences, safe errors, and logout/config/grant changes before dispatch or publication still prevent unauthorized access.
- Normal checks use fake providers and bounded fixtures; real model quality, GPU readiness and paid calls remain separate acceptance.

## Current source result

The retained domain is extracted once into Controller. MCP tools use the shared core contract; Studio uses authenticated direct HTTP. The removed custom subsystem is absent. Domain tests moved with pure source; mixed external ingress/protocol tests remain with Generation MCP and import Controller values. API/ownership tests are in Controller, shared MCP/HTTP admission tests in Generation MCP and direct gateway/account tests in Studio.

Review, installed packaging and matched CI evidence are recorded in Controller #1 / AI progress at exact implementation commits. This branch's source does not claim deployment, live model readiness or operational reconciliation.
