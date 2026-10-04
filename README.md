# FLAMORIS Generation Controller

Internal generation controller for FLAMORIS, managing generation providers, ComfyUI workflows, jobs, inputs, references, and generated assets independently of MCP transport.

**Status: design and documentation only.** No Controller implementation, service endpoint, package, or deployment has been introduced. Existing generation behavior still lives in `flamoris-generation-mcp`; extraction and consumer migration require separate approval.

Part of [FLAMORIS AI](https://github.com/flamoris-jp/flamoris-ai). Architecture authority: [FLAMORIS AI #18](https://github.com/flamoris-jp/flamoris-ai/issues/18). Owning design task: [#1](https://github.com/flamoris-jp/flamoris-generation-controller/issues/1).

## What it owns

The intended internal generation-domain boundary includes:

- configured generation providers, their capabilities and provider adapters;
- generation-provider workflow construction, particularly ComfyUI API-format workflow JSON and declared parameter/input bindings;
- generation jobs, status, cancellation and results;
- managed inputs and references, provider staging, generated assets and bounded retrieval;
- generation-specific validation, existing verification safeguards and normalized provider failures.

Provider selection is explicit. A common interface makes implementations replaceable; it does not imply automatic provider selection, fallback or a new universal scheduler.

## Where it fits

```text
External MCP access:
ChatGPT -> MCP Hub -> Generation MCP -> Generation Controller -> providers

Internal application access:
Studio ------------------------------> Generation Controller -> providers
```

`flamoris-generation-mcp` is the external MCP adapter. Studio and other internal callers use the Controller's non-MCP contract. That contract's concrete transport and packaging are not decided by this repository's creation.

ComfyUI, Irodori and YuE are examples of generation providers, not claims that this new repository already runs them. Actual provider support and qualification remain separately evidenced.

## Two different meanings of Workflow

| Term | Responsibility | Owner |
| --- | --- | --- |
| ComfyWorkFlow | Build a provider execution definition, such as ComfyWorkFlow JSON, from trusted definitions and allowed values | Generation Controller; ComfyUI executes its graph |
| ExecutionPlan | Control inference, its execution steps and active state | `flamoris-ai-runtime` |

**The ComfyWorkFlow Builder does not move into AI Runtime.** Building JSON does not require an Agent, AI Runtime, MCP Hub, a GPU, or an ExecutionPlan engine. Submitting that JSON to a provider and verifying a production workflow are separate operations with their own prerequisites.

## What it does not own

MCP transport/catalog routing belongs to Generation MCP and MCP Hub. Personality, conversation and memory belong to AI Agent and are optional for generation. Native inference and ExecutionPlans belong to AI Runtime. Host-wide runtime/GPU transitions belong to GPU Node Manager. Studio retains user authorization and product state; ComfyUI retains actual ComfyUI graph execution.

No new generic DAG engine, cross-provider inference bridge, host manager or personality layer is required to extract the existing generation domain.

## Documentation

- [Architecture and boundaries](docs/ARCHITECTURE.md)
- [Extraction inventory and migration gates](docs/MIGRATION.md)
- [Contributor guardrails](AGENTS.md)
- [FLAMORIS organization map](https://github.com/flamoris-jp/.github)
- [AI ecosystem map](https://github.com/flamoris-jp/flamoris-ai/blob/main/docs/ai-ecosystem.md)
- [Shared repository policy](https://github.com/flamoris-jp/flamoris-commons/blob/main/docs/repository-policy.md)

There are no install/build/run commands yet. Do not infer a service port, executable, API route or Docker layout from another FLAMORIS repository.

## 日本語

FLAMORIS Generation Controllerは、生成AIの制御をMCPから分離するための内部層です。ComfyUI用Workflow JSONの組み立て、生成job、provider adapter、参照入力、生成物を担当する設計です。現在は文書のみで、既存コードの移設・実機変更はしていません。

**GenerationのWorkflowはComfyUIなどへ渡す実行定義、AI RuntimeのWorkflowは推論の制御です。両者を統合したり、ComfyUIのJSON生成をAI Runtimeへ移したりしません。** Studioは内部APIから、ChatGPTはMCP HubとGeneration MCPを経由して利用します。人格が必要な場合だけAI Agentが関わります。

## FLAMORIS

FLAMORIS is open-source software for creative work and AI-native production. Commercial use of the licensed code is welcome and does not require individual permission. Software is provided as-is, without guaranteed individual support. Repository documentation, Issues, tests and source are the primary self-support references.

## License

Code and documentation are licensed under [Apache License 2.0](LICENSE), unless otherwise noted. Models, weights, datasets, media, provider assets and generated outputs may have separate terms; the repository license does not automatically cover them.


## Current implementation decision

Do not implement this repository yet. When Generation work is explicitly resumed, the current `flamoris-generation-mcp` ComfyWorkFlow subsystem should be removed from the MCP repository rather than migrated here. Any future Controller ComfyWorkFlow implementation must be designed from the Controller contract at that time. The next implementation priority for FLAMORIS AI is Intelligence boundary cleanup, not this repository.
