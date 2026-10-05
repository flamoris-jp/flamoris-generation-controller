# Future Generation Controller boundary

Authority: [AI #18](https://github.com/flamoris-jp/flamoris-ai/issues/18); local design [#1](https://github.com/flamoris-jp/flamoris-generation-controller/issues/1). Controller implementation is deferred. This document defines a target, not a running service or a code-extraction task.

```text
ChatGPT -> MCP Hub -> Generation MCP --+
                                      |
Studio -------------------------------+-> future Controller -> providers
```

Behind the MCP facade, callers use an internal non-MCP contract. Exact library/service packaging, authentication, deployment and DTOs require later design; no extra network service is mandated.

## Intended ownership

| Component | Target responsibility |
| --- | --- |
| Controller | Internal generation requests/provider adapters, jobs/results, inputs/references, assets and domain checks |
| Generation MCP | External tools, MCP validation/annotations and request/result translation |
| ComfyUI / other providers | Actual provider execution and provider-local state |
| AI Runtime | Inference, ExecuteFlow and compiled ExecutionPlan, active jobs/state/resources |
| AI Agent | Optional personality, conversations and context policy |
| GPU Node Manager | Host-wide runtime/GPU lifecycle |
| Studio | Authenticated UI, drafts and user-scoped access |

## ComfyWorkFlow is not ExecuteFlow

A ComfyWorkFlow is ComfyUI API-format graph/JSON. Future JSON construction would use its separately reviewed parameter/input contract; the provider then executes the graph. It is not a Runtime inference flow and needs no compulsory Agent, AI Runtime or MCP Hub. Other providers may use ordinary generation recipes instead.

ExecuteFlow belongs to AI Runtime. Its source/control description is distinct from the existing compiled ExecutionPlan and scheduler-visible Jobs. A future Runtime call to generation could be an explicit internal capability, but that is not required or implemented here.

## No migration of the old ComfyWorkFlow subsystem

The earlier plan to copy the existing Generation MCP builder/registry into Controller is superseded. Generation #69 retired the custom registry/versioning/composition/qualification and Runtime-delegation subsystem; Studio #63, Hub #37 and Runtime #25 removed its consumers. Original builtin/native recipes, jobs and protected data remain. See [retirement baseline](MIGRATION.md). Do not interpret this document as an instruction to implement a replacement builder or retain every obsolete subsystem forever.

The implemented removal separates source and dependent tests from saved definitions, asset/input records, credentials, historical evidence and unresolved provider work. Persistent-data deletion and live cutover are separate decisions. Controller remains unimplemented meanwhile.

## Future contract constraints

When separately commissioned, use the smallest contract required by real consumers. Preserve one shared generation state authority, explicit provider selection and honest unavailable/unknown results. A reference or transport reconnect does not authorize a new attempt.

Retained paths keep user authorization, bounded input/output/staging, immutable references, declared output handling, safe errors/provenance and no hidden retry/fallback. Static JSON validity, current provider availability and real generation qualification are different claims. No manual ready flag or weakened protection is introduced by this architecture.

## Current phase

Controller remains documentation-only. Intelligence/Agent internal connections and custom generation source retirement are already implemented elsewhere. No source extraction/deletion, new framework, API, deployment, provider call, runtime switch or Controller implementation occurs here.
