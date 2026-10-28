# Contributor and AI-agent instructions

## 2026-10-28 Controller #31

The user explicitly resumes AnimeGen/Wan video integration and short-clip testing. Follow docs/VIDEO.md. Source/test/doc/PR work and the requested live test supersede prior feature holds for this slice. Obtain actual exported production graphs and installed contracts; do not fabricate a validated graph or bypass operator policy. Preserve single authority, managed inputs, scoped cancellation and unknown/no-replay. No merge is requested.

## 2026-10-18 Controller #26

The explicit user request authorizes source implementation, matched MCP/Studio/Hub integration, tests, documentation and reviewable PRs for declarative image graphs. It supersedes older fixed-topology/model-family restrictions for this slice. Follow the [declaration contract](docs/DECLARED_COMFY.md). Preserve existing profiles/data, single authority, immutable inputs, ownership and unknown/no-replay. Live deployment, inference and merges remain separate.

This repository is the owner of FLAMORIS generation-domain code. It contains the MCP-free core and optional internal HTTP adapter. Read README.md, docs/ARCHITECTURE.md, docs/IMPLEMENTATION.md, docs/MIGRATION.md, current [AI progress](https://github.com/flamoris-jp/flamoris-ai/blob/main/PROGRESS.md), [AI #18](https://github.com/flamoris-jp/flamoris-ai/issues/18) and [Controller #1](https://github.com/flamoris-jp/flamoris-generation-controller/issues/1).

## Current task and sequencing

The 2026-10-12 user request explicitly resumes source development for img2img, IPAdapter appearance/style, prepared-map ControlNet and matching Studio integration under #13 / Studio #79. It supersedes older reference-expansion holds for this slice. Use docs/REFERENCE_IMAGES.md; source, tests, documentation and reviewable PRs are authorized. Live deployment, model downloads/inference and merges remain separately authorized.


Controller extraction and the subsequent bounded ComfyWorkFlow registration/reference-image profile are accepted in main through Controller #5/#7 and matched callers, after explicit merge instructions. See AI PROGRESS §4.8/§4.9. The current request aligns documentation and postpones live acceptance. Live cutover, data/grants/credentials, paid calls and further generation features remain separate scopes. Future merges require applicable explicit user authorization.

The selected initial host is one Controller object inside the Generation MCP HTTP process, shared by external MCP and authenticated internal HTTP adapters. The output-root lifetime ownership lock must be acquired before provider construction or recovery. Keep the exact API/service-permission decisions in docs/API.md and Controller #1; never create another runtime per caller/session.

## Dependency and ownership rules

The subsequent explicit 2026-10-05 request authorizes source implementation and
review for ChatGPT ComfyWorkFlow registration and reference-image generation
under Controller #6. Follow docs/COMFY_REGISTRATION.md. This new bounded profile
supersedes the reference-feature hold only for this request; old schema 2/3,
versioning/v3/Runtime delegation and live rollout remain separate.

1. Studio and the external Generation MCP facade call one shared Controller authority through non-MCP contracts. Controller must not depend on MCP SDK objects, Hub or a Studio package.
2. Controller owns retained generation recipes, provider adapters/model/capability metadata, jobs/results, managed inputs/staging, generated assets and retention. Providers perform actual execution.
3. MCP tools/annotations, wire validation, MCP signed-envelope ingress, ToolError and binary content mapping remain with Generation MCP. Split trusted provenance data from MCP middleware.
4. Studio retains authentication, CSRF, per-user ownership, opaque mappings, UI/drafts/presets/history, bounded browser publication and response-time access rechecks. An upstream ID or provenance subject is not an authorization grant.
5. GPU Node Manager retains host-wide lifecycle. AI Runtime retains inference, ExecuteFlow and compiled ExecutionPlan. Agent retains optional personality/conversations. Controller does not acquire these state machines.
6. Reuse the retained reviewed source. Do not restore the deleted custom ComfyWorkFlow registration/versioning/v3/qualification/Runtime-delegation subsystem, introduce a generic scheduler, or add new providers/reference-image features as part of separation.

## Contract and state continuity

The minimum request/result/error/context, package, co-hosting and ownership contract was recorded in Controller #1 before extraction. Follow docs/API.md and docs/ARCHITECTURE.md when changing it. Library reuse in independent processes must not produce independent JobStores, locks or journals; sharing a storage directory does not provide cross-process exclusion. Consider co-hosting the facade and internal adapter before adding a network process.

Preserve existing IDs, data/storage identities, declared output roles, bounded inputs/staging/transfer, input-use leases, retention and non-secret provenance. Separate transport settings from provider/storage settings. Existing `WorkflowStore`, `workflows.*`, `workflow_id`, schemas and configuration literals keep their exact names until a tested compatibility decision.

Journal reservations before provider submission. Ambiguous acceptance, failed acknowledgement or uncertain journal commit stays unknown/reserved; do not replay, auto-fallback or release on reconnect/restart. Cancellation must target owned work and confirm a terminal outcome. Retired active debt and its original journals remain protected until reconciliation by the previous matched authority.

Authenticate trusted caller context at each ingress and enforce the selected authorization contract before effects. Keep credentials in operator configuration and out of browser data, persona text, prompts, public errors and repository files. No arbitrary shell/URLs/paths, hidden downloads or implicit GPU activation.

## Implementation and checks

For further code work, inspect current Generation/Studio source, tests and owner instructions at fixed revisions; the recorded inventory is a starting point, not proof the source has not changed. Keep focused stages: core/lifecycle and state ownership, external MCP compatibility, then Studio gateway replacement. Update affected owner documentation with each code change.

Use fake providers and bounded fixtures in normal CI. Verify MCP-free core imports/installed packaging, one admission authority across both callers, unknown/restart/no-replay behavior, scoped cancellation, immutable inputs, output metadata, provenance, retained records and Studio account isolation. Contract extraction does not prove live model/GPU readiness.

Run core/domain/API tests, lint/format and installed-wheel checks without MCP. Matched facade/Studio CI covers protocol compatibility, shared admission and two-account isolation. Controller #1 source acceptance is recorded; preserve its evidence and record pending operational acceptance separately under AI #18.

## Operations and repository practice

Live deployment, runtime changes, data/grant/credential mutation, paid calls and reference-image expansion are separate scopes. Use flamoris-server-manager for current infrastructure facts when an operational task requires them.

Use focused commits and review final text/code. Merges require explicit user authorization; respect authorization already given for the applicable task. Do not force-push shared branches. Follow the [repository policy](https://github.com/flamoris-jp/flamoris-commons/blob/main/docs/repository-policy.md), keep public documentation portable and preserve licenses and model/provider terms.


## 2026-10-13 linked external gallery scope

Controller #16 / Studio #86 implement internal-only bounded external discovery and the linked per-account gallery. The user explicitly requests implementation through release and live changes. Preserve the 25 external tools, trusted provenance, existing single-owner/unknown fences and output placement. The new index is only a recent receipt, never a competing state owner or Studio ACL. Matched source review/tests and live credential/binding acceptance must be recorded separately.


## 2026-10-13 reference-image continuation

Resume the prepared reference-image PR against current main. Preserve linked external discovery, YuE2 output placement and remembered Assistant models. Match Controller/Generation/Studio dependency pins and new release versions; source CI and real-host model/shared-input acceptance remain distinct.
