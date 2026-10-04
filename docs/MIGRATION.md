# Boundary inventory and future cleanup gates

Parent: [FLAMORIS AI #18](https://github.com/flamoris-jp/flamoris-ai/issues/18). Owning design issue: [#1](https://github.com/flamoris-jp/flamoris-generation-controller/issues/1).

**Documentation first.** This plan does not authorize extraction, deployment or restarting ComfyWorkFlow/reference-image development. Existing merged work remains in place. Resume requires an explicit user instruction.

## Initial responsibility inventory

This is a domain-level classification derived from current Generation documentation and tracked work. It is not a code-migration plan. Exact source/test paths and revisions must be recorded before later deletion or redesign.

| Existing responsibility in Generation MCP | Target disposition |
| --- | --- |
| MCP tools, annotations, transport, native MCP content representation | Keep in Generation MCP; translate to/from the internal contract |
| ProviderRegistry / CapabilityRegistry and provider adapters | Extract generation-domain behavior to Controller; discovery in MCP is a projection |
| ComfyWorkFlow store/registry, trusted definitions, allowed bindings, ComfyUI graph construction | **Delete from Generation MCP when cleanup is later authorized; do not migrate this implementation to Controller and do not move it to AI Runtime** |
| JobStore, submission reservation, status/cancel/result and uncertain-outcome behavior | Extract as one authority, preserving existing guarantees and identity |
| Managed inputs/references, provider staging, generated assets and bounded transfer | Extract domain behavior; preserve Studio ownership checks and MCP presentation separately |
| Generation verification/attestation checks | Preserve generation-domain semantics; audit relocation without discarding safeguards |
| GPU/service lifecycle and runtime fact collection | Leave with GPU Node Manager/provider/deployment owners |
| Native inference and ExecuteFlow machinery | Leave with AI Runtime; not part of this extraction |
| v3 includes, composition, Runtime-delegation proposals and new provider expansion | Classify separately; do not make speculative extensions prerequisites for the basic JSON-builder path |

Relevant source documents include Generation MCP's README, `docs/GENERATION_HUB_DESIGN.md`, `docs/MANAGED_INPUTS.md`, `docs/WORKFLOW_VERIFICATION.md`, `docs/WORKFLOW_V3_FOUNDATION.md` and `docs/RUNTIME_DELEGATION.md`. Inspect the actual current files before relying on their implementation status.

Historical/acceptance trackers: Generation MCP [#19](https://github.com/flamoris-jp/flamoris-generation-mcp/issues/19), [#42](https://github.com/flamoris-jp/flamoris-generation-mcp/issues/42), [#30](https://github.com/flamoris-jp/flamoris-generation-mcp/issues/30), [#25](https://github.com/flamoris-jp/flamoris-generation-mcp/issues/25), [#31](https://github.com/flamoris-jp/flamoris-generation-mcp/issues/31) and [#45](https://github.com/flamoris-jp/flamoris-generation-mcp/issues/45). Their former repository ownership is not the new target architecture, and old checked/closed work is not undone by this plan.

## Design questions that remain open

- What is the smallest common internal contract for the actual Studio and MCP callers?
- Does the existing deployment require a shared service, or can a library boundary meet it without duplicating state? Do not assume one instance per caller is safe.
- Which exact modules, persistent formats and tests can move unchanged?
- How will existing job/asset/input identities, active or uncertain reservations, provenance and configured storage survive migration?
- Which existing MCP contracts remain compatible, and which genuinely require an explicit versioned transition?

Do not answer these questions by inventing a new gateway framework, host topology or API route in documentation.

## Later cleanup sequence, not current authorization

1. Record the source revision and file/test inventory. Separate MCP transport code from misplaced generation-domain code.
2. For the existing Generation MCP ComfyWorkFlow subsystem, plan deletion rather than transfer. Preserve only the tests/evidence that remain useful for later Controller design.
3. Keep Generation Controller unimplemented until a separate instruction defines the minimal internal contract and state authority.
4. After that future decision, adapt Generation MCP and Studio to the reviewed internal contract without leaving two active independent state owners.
5. Perform separately authorized acceptance and rollback checks before live cutover. Preserve uncertain work and retained data; no automatic replay or destructive cleanup.

Each step after design requires separately scoped approval. No source move, new dependency, migration script, runtime activation or paid smoke is part of the current documentation PR.

## Acceptance must remain layered

**Builder-only:** trusted graph/fixture plus allowed values produces deterministic JSON; undeclared or invalid bindings are rejected. No GPU, Agent, Runtime or MCP server is needed for this test.

**Controller/provider:** fake provider tests verify discovery, jobs, references, outputs, bounded failures, authorization integration and no replay. Existing protections must not disappear when code moves.

**MCP adapter:** protocol/schema/result mapping agrees with the reviewed internal behavior without a second store or execution authority.

**Live provider and migration:** actual generation, reference influence, qualification, restart/retained-data compatibility and client isolation are checked only under explicit operational approval. Passing offline tests does not claim these checks passed.

## Existing Issue handling

Keep existing Issues and merged PRs as evidence. Mark affected unfinished work as on hold and link the correction parent/child. After design review, decide item by item whether to retain the remaining requirement, re-scope it under Controller, or supersede it with a linked replacement. Do not mass-close, reopen completed work or remove evidence just to make the board look clean.


## Current sequencing

Do not start Generation implementation from this document. The immediate FLAMORIS AI implementation priority is Intelligence boundary cleanup. Generation Controller and ComfyWorkFlow cleanup remain paused until separately resumed.
