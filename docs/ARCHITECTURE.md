# Generation Controller architecture

Authority: [AI #18](https://github.com/flamoris-jp/flamoris-ai/issues/18) and local [Controller #1](https://github.com/flamoris-jp/flamoris-generation-controller/issues/1). On 2026-10-05 the user selected implementation preparation, beginning with documentation. Controller remains unimplemented; this is the target boundary. [IMPLEMENTATION.md](IMPLEMENTATION.md) records the source inventory, decisions still needed and staged acceptance.

## Current and target paths

| Caller | Current source | Target |
| --- | --- | --- |
| Studio generation | Generation MCP compatibility gateway → co-located domain/providers | Non-MCP contract → one Controller authority → providers |
| External generation | MCP Hub → Generation MCP → co-located domain/providers | MCP Hub → Generation MCP facade → the same Controller → providers |

Controller is the shared domain owner, not a Studio-to-MCP relay. No extra network hop is mandated: a domain package, its host and its transport adapters are separate decisions. Whatever hosting is chosen, there is one JobStore/reservation authority, not one per caller. Internal contracts carry ordinary typed values and safe domain errors, never MCP SDK objects.

## Ownership

| Component | Target responsibility |
| --- | --- |
| Controller | Provider/model/capability contracts, retained generation recipes, request validation, jobs/status/cancel/results, managed inputs/staging, assets/transfer and retention |
| Generation MCP | External tool/schema/annotations, wire validation, signed-envelope ingress and MCP content/error translation |
| Studio | User/session authorization, CSRF, user-facing orchestration, drafts/presets/history, owned opaque references and bounded browser responses |
| Providers | Actual execution, provider-specific command/API adaptation and provider-local state |
| AI Runtime | Inference, ExecuteFlow, compiled ExecutionPlan, its active jobs/state/resources |
| AI Agent | Optional personality, conversations and context policy |
| GPU Node Manager | Host-wide runtime/GPU lifecycle |

Studio can retain request records and displayed job state as references/projections. Controller owns authoritative generation state. User-input DTO checks and untrusted-result checks remain at the Studio boundary even when generation constraints share a common contract.

## Reuse the retained domain

The existing builtin image and native Speech/Music/transcription recipe paths are the source of the initial Controller scope. Move ordinary domain processing and its construction/lifecycle out of MCP-specific startup; preserve current behavior and identities. Shared schema/profile definitions may be reused by consumers without importing Controller runtime or provider internals into Studio.

Generation #69/#70 removed custom registry/versioning, composition/v3, qualification and Runtime delegation. Those deleted subsystems are not copied or rebuilt. Existing `WorkflowStore` and `workflow_id` describe retained generation recipes, including non-ComfyUI providers.

A ComfyWorkFlow is ComfyUI API-format graph/JSON; ComfyUI executes it. ExecuteFlow and compiled ExecutionPlan remain Runtime concepts. Constructing a bounded image graph requires neither an Agent nor a Runtime bridge. Reference-image generation and new custom-graph features require separate product scope.

## One state owner and trusted ingress

Both caller paths must use the same active-job reservation, durable unknown state, input-use leases, asset identity and retention records. A shared package or storage path alone cannot establish exclusion across independent processes. Select the hosting/locking contract before extraction and preserve reservation-before-submit semantics.

Keep MCP signed-envelope verification and replay admission at external ingress. Controller accepts only the verified, transport-independent caller context defined by the implementation contract, and records non-secret provenance. Studio's user ownership and opaque mappings remain enforced before dispatch and before publication; an identifier or provenance value is not an access grant. Internal service authentication and operation permissions must be designed explicitly, not inferred from a local connection.

Retained paths preserve immutable references, declared output roles, bounded decoding/staging/transfer, safe errors/paths and no hidden retry/fallback. JSON validity, provider availability, host readiness and real generation acceptance remain different claims. Source acceptance and live cutover are recorded separately.
