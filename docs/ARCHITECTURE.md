# Generation Controller architecture

Authority: [AI #18](https://github.com/flamoris-jp/flamoris-ai/issues/18) and [Controller #1](https://github.com/flamoris-jp/flamoris-generation-controller/issues/1). The 2026-10-05 implementation instruction supersedes documentation-only preparation. This source implements the selected boundary; live deployment remains pending.

## Source paths and lifecycle

| Caller | Implemented source boundary |
| --- | --- |
| Studio generation | Authenticated non-MCP HTTP v1 → one Controller → providers |
| External generation | MCP Hub → Generation MCP facade → same Controller → providers |

The `flamoris_generation_controller` package owns shared construction in `runtime.py`. Generation MCP hosts its two transport adapters around the exact same runtime and closes it once at host shutdown. Disconnecting an MCP session does not close providers or release the reservation. Studio imports neither MCP nor Controller provider internals; it uses ordinary HTTP DTOs and retains consumer validation.

Shutdown rejects new invocations, drains already admitted calls and closes providers before releasing the ownership lock. Concurrent closers join one cleanup task; cancelling a close waiter does not cancel cleanup or admit another owner while an operation still has pending effects. Cancelling an admitted submit retains its unknown journal, then permits shutdown to drain; shutdown does not delete generation reservations.

`authority.py` holds a nonblocking local filesystem lock in the configured output root before provider construction/recovery. Duplicate instances fail before effects, including separate processes. Dispatch checks that the root, namespace and lock identity remain pinned. Lock shutdown leaves the file and all job journals intact. This is local storage ownership, not a distributed scheduler or GPU lifecycle lock; deployments must use one authority/storage root and drain pre-lock versions before activation.

The optional `http_api.py` adapter authenticates a separate operator credential and supplies trusted service context. External MCP ingress authenticates its signed provenance independently. [API.md](API.md) defines strict bounded request models, ordinary results, binary content, safe errors and the service permission boundary.

## Ownership

| Component | Responsibility |
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

The existing builtin image and native Speech/Music/transcription recipe paths are the source of the initial Controller scope. Ordinary domain processing and construction/lifecycle now live in Controller; the facade preserves existing wire behavior and identities. Shared schema/profile definitions may be reused by consumers without importing Controller runtime or provider internals into Studio.

Generation #69/#70 removed custom registry/versioning, composition/v3, qualification and Runtime delegation. Those deleted subsystems are not copied or rebuilt. Existing `WorkflowStore` and `workflow_id` describe retained generation recipes, including non-ComfyUI providers.

A ComfyWorkFlow is ComfyUI API-format graph/JSON; ComfyUI executes it. ExecuteFlow and compiled ExecutionPlan remain Runtime concepts. Constructing a bounded image graph requires neither an Agent nor a Runtime bridge. The subsequent [bounded registration profile](COMFY_REGISTRATION.md) implements immutable checkpoint txt2img/img2img graphs and managed initial images (schema 7), accepted in Controller #7. Arbitrary custom nodes, IPAdapter/ControlNet, new model families and further features require separate scope; live acceptance remains pending.

## One state owner and trusted ingress

Both caller paths must use the same active-job reservation, durable unknown state, input-use leases, asset identity and retention records. A shared package or storage path alone cannot establish exclusion across independent processes. The co-hosted runtime and lifetime ownership lock implement this exclusion; reservation-before-submit semantics remain intact.

Keep MCP signed-envelope verification and replay admission at external ingress. Controller accepts only the verified, transport-independent caller context defined by the implementation contract, and records non-secret provenance. Studio's user ownership and opaque mappings remain enforced before dispatch and before publication; an identifier or provenance value is not an access grant. Internal service authentication and explicit operation permissions are checked before effects; the service credential grants the trusted backend the API surface while Studio retains user authorization.

Retained paths preserve immutable references, declared output roles, bounded decoding/staging/transfer, safe errors/paths and no hidden retry/fallback. JSON validity, provider availability, host readiness and real generation acceptance remain different claims. Source acceptance and live cutover are recorded separately.
