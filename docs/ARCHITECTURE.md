# Generation Controller architecture

Status: agreed responsibility direction; implementation and concrete internal API remain pending. Coordination: [FLAMORIS AI #18](https://github.com/flamoris-jp/flamoris-ai/issues/18). Local design: [#1](https://github.com/flamoris-jp/flamoris-generation-controller/issues/1).

## Purpose

Define the future generation-domain boundary separately from the MCP-facing adapter. This document does not authorize or begin a code extraction. The target is not a new inference platform. It is a reusable internal boundary for existing generation-provider operations.

```text
ChatGPT -> MCP Hub -> Generation MCP --+
                                      |
Studio -------------------------------+-> Generation Controller -> providers
```

Arrows after the MCP adapter represent internal, non-MCP calls. Library versus authenticated service transport is a later, bounded design decision based on actual deployment and state ownership. This diagram does not prescribe a new network hop for every call.

## Responsibility matrix

| Component | Owns | Does not acquire through this correction |
| --- | --- | --- |
| Generation Controller | Provider metadata/adapters; generation-definition building; generation jobs, inputs/references, outputs/assets; domain validation | MCP routing, personality, native inference, host lifecycle |
| Generation MCP | External tools, MCP schemas/annotations, protocol validation and internal request/result translation | A second generation store, compiler, reservation or provider engine |
| ComfyUI / other providers | Their actual provider execution and provider-local state | Studio user identity or FLAMORIS product documents |
| AI Runtime | Inference execution, ExecuteFlows, active jobs/state/resources | Ownership of ComfyUI graph construction merely because the older terminology used the same word |
| AI Agent | Optional personality, conversation, memory and context policy | Mandatory mediation of every generation request |
| GPU Node Manager | Configured host runtime/GPU lifecycle and transition coordination | Generation-job state or per-ComfyWorkFlow approval |
| Studio | Authenticated UI, product drafts and user-scoped access | Provider graph internals or an alternate generation state store |

## ComfyWorkFlow: build JSON, let the provider execute

```text
Trusted ComfyUI API-format definition
  + declared parameter bindings
  + validated values / managed reference handles
                         |
                         v
                ComfyWorkFlow JSON builder
                         |
                         v
                 ComfyWorkFlow JSON
                         |
                separate submit operation
                         v
                       ComfyUI
```

A future Controller-side ComfyWorkFlow builder would apply declared substitutions and check the relevant contract. The existing Generation MCP implementation is not transferred here. It does not execute model nodes, schedule inference, introduce Agent conversations or require a Runtime bridge. Graph construction may be tested entirely offline. Actual model/node compatibility and production verification are separate from successful JSON construction.

Reference-image bindings, templates and supported model-specific graphs stay in the generation domain. Mentioning LoRA, ControlNet, video or another graph family in a design is not evidence of current implementation or support.

## ExecuteFlow: inference control

AI Runtime's ExecuteFlow controls inference and associated execution steps, state, suspension/resumption and registered capabilities where implemented. It is a different execution model and authority. No conversion from every ComfyUI graph to Runtime IR is required or authorized here.

A future explicitly requested inference workload may call generation as a bounded internal capability. That is optional integration, not a prerequisite for the Controller, JSON builder, reference images or Studio generation.

## Internal contract principles

Expose only the operations needed by current callers: discover configured capabilities/definitions, build an execution definition, submit/observe/cancel generation, and manage authorized input/output handles. Exact method names, DTOs, transport, authentication and deployment are not frozen in this document.

MCP and Studio adapters must share the same underlying generation authority. A result/status projection is not a duplicate scheduler. Provider IDs and generation job IDs must retain their documented meaning; transport reconnection must not mint a new generation attempt after an uncertain outcome.

Provider-specific details stay in the appropriate adapter. Provider choice remains explicit. Optional provider unavailability does not imply permission to select another provider or activate a GPU runtime.

## Safety belongs to the domain too

Transport separation must not remove existing protections: immutable references, user authorization, input decoding/size limits, staging confinement, bounded transfer, declared outputs, persisted provenance and uncertain-submit fences. Internal callers are not inherently authorized merely because they are internal.

Preserve the distinction between static validation, provider availability, runtime evidence and generation qualification. Existing automated verification requirements are not replaced by human approval or a hand-written ready flag. Do not turn these safeguards into a new inference engine or general platform gate for building JSON.

## Non-goals

No new generic DAG scheduler, inference kernel, Agent-memory service, GPU/systemd manager, cross-provider composition framework, automatic provider fallback or model installation. No new source code, endpoint, service or data migration is implemented by this document.


## No code migration of the current ComfyWorkFlow subsystem

The existing Generation MCP ComfyWorkFlow registry/builder implementation is intentionally not an extraction target. When Generation cleanup is later authorized, remove that subsystem from the MCP repository instead of copying it into Controller. This repository remains documentation-only until a separate implementation decision defines the minimal Controller contract and its own implementation.
