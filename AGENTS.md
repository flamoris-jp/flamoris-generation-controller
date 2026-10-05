# Contributor and AI-agent instructions

This repository is the planned owner of FLAMORIS generation-domain code. It currently contains documentation only. Read README.md, docs/ARCHITECTURE.md, docs/IMPLEMENTATION.md, docs/MIGRATION.md, current [AI progress](https://github.com/flamoris-jp/flamoris-ai/blob/main/PROGRESS.md), [AI #18](https://github.com/flamoris-jp/flamoris-ai/issues/18) and [Controller #1](https://github.com/flamoris-jp/flamoris-generation-controller/issues/1).

## Current task and sequencing

The latest 2026-10-05 user decision requests Controller README/AGENTS setup and implementation-policy preparation in Controller and FLAMORIS AI. This supersedes the earlier blanket hold on Controller preparation. The current task is documentation, source inventory, issue alignment and reviewable PRs; it does not implement Controller or change caller source.

Subsequent code work follows the user's scoped implementation instruction and the plan below. These instructions are not a permanent ban on Controller development. Do not infer code or live-operation authorization from merging documentation. User instructions govern scope; existing session authorization does not need to be requested again.

## Dependency and ownership rules

1. Studio and the external Generation MCP facade call one shared Controller authority through non-MCP contracts. Controller must not depend on MCP SDK objects, Hub or a Studio package.
2. Controller owns retained generation recipes, provider adapters/model/capability metadata, jobs/results, managed inputs/staging, generated assets and retention. Providers perform actual execution.
3. MCP tools/annotations, wire validation, MCP signed-envelope ingress, ToolError and binary content mapping remain with Generation MCP. Split trusted provenance data from MCP middleware.
4. Studio retains authentication, CSRF, per-user ownership, opaque mappings, UI/drafts/presets/history, bounded browser publication and response-time access rechecks. An upstream ID or provenance subject is not an authorization grant.
5. GPU Node Manager retains host-wide lifecycle. AI Runtime retains inference, ExecuteFlow and compiled ExecutionPlan. Agent retains optional personality/conversations. Controller does not acquire these state machines.
6. Reuse the retained reviewed source. Do not restore the deleted custom ComfyWorkFlow registration/versioning/v3/qualification/Runtime-delegation subsystem, introduce a generic scheduler, or add new providers/reference-image features as part of separation.

## Contract and state continuity

Before code extraction, settle the minimum request/result/error/context contract, packaging/hosting and one reservation owner in Controller #1. Library reuse in independent processes must not produce independent JobStores, locks or journals; sharing a storage directory does not provide cross-process exclusion. Consider co-hosting the facade and internal adapter before adding a network process.

Preserve existing IDs, data/storage identities, declared output roles, bounded inputs/staging/transfer, input-use leases, retention and non-secret provenance. Separate transport settings from provider/storage settings. Existing `WorkflowStore`, `workflows.*`, `workflow_id`, schemas and configuration literals keep their exact names until a tested compatibility decision.

Journal reservations before provider submission. Ambiguous acceptance, failed acknowledgement or uncertain journal commit stays unknown/reserved; do not replay, auto-fallback or release on reconnect/restart. Cancellation must target owned work and confirm a terminal outcome. Retired active debt and its original journals remain protected until reconciliation by the previous matched authority.

Authenticate trusted caller context at each ingress and enforce the selected authorization contract before effects. Keep credentials in operator configuration and out of browser data, persona text, prompts, public errors and repository files. No arbitrary shell/URLs/paths, hidden downloads or implicit GPU activation.

## Implementation and checks

For a later code task, inspect current Generation/Studio source, tests and owner instructions at fixed revisions; the recorded inventory is a starting point, not proof the source has not changed. Keep focused stages: core/lifecycle and state ownership, external MCP compatibility, then Studio gateway replacement. Update affected owner documentation with each code change.

Use fake providers and bounded fixtures in normal CI. Verify MCP-free core imports/installed packaging, one admission authority across both callers, unknown/restart/no-replay behavior, scoped cancellation, immutable inputs, output metadata, provenance, retained records and Studio account isolation. Contract extraction does not prove live model/GPU readiness.

The current documentation task checks local links, exact source identifiers, dependency direction, as-built/target status and diff whitespace. Do not claim runtime tests from this change. Keep Controller #1 open while contract decisions and implementation remain pending; preserve prior PR/Issue evidence.

## Operations and repository practice

Live deployment, runtime changes, data/grant/credential mutation, paid calls and reference-image expansion are separate scopes. Use flamoris-server-manager for current infrastructure facts when an operational task requires them.

Use focused commits and review final text/code. Merges require explicit user authorization; respect authorization already given for the applicable task. Do not force-push shared branches. Follow the [repository policy](https://github.com/flamoris-jp/flamoris-commons/blob/main/docs/repository-policy.md), keep public documentation portable and preserve licenses and model/provider terms.
