# FLAMORIS Generation Controller

Planned internal generation-domain boundary for FLAMORIS, independent of MCP transport.

**Status: documentation only. Do not implement Controller in the current phase.** No package, endpoint, running service or source migration is provided. The next implementation priority is Intelligence cleanup, coordinated by [FLAMORIS AI #18](https://github.com/flamoris-jp/flamoris-ai/issues/18).

The existing Generation MCP ComfyWorkFlow subsystem is for later removal from that repository, not transfer here. Creating this repository does not require recreating that subsystem. A future Controller implementation needs a separate minimal scope and explicit authorization.

## Intended responsibility

A future Controller can own configured generation providers/adapters and capability metadata, generation requests/jobs/results, managed inputs/references, staging and generated assets. Concrete API, packaging and deployment decisions remain open in [#1](https://github.com/flamoris-jp/flamoris-generation-controller/issues/1).

```text
Target external path:
ChatGPT -> MCP Hub -> Generation MCP -> future Controller -> providers

Target internal path:
Studio ------------------------------> future Controller -> providers
```

MCP exposes external tools; internal callers use a non-MCP contract. Both frontends must eventually use one generation state owner, not independent JobStores/reservations. This diagram does not claim current deployment or mandate a new network hop.

## Terminology

| Name | Meaning |
| --- | --- |
| `ComfyWorkFlow` | ComfyUI graph / API-format JSON; ComfyUI executes it |
| `ExecuteFlow` | AI Runtime's inference dependency/data/control flow |
| `ExecutionPlan` | AI Runtime's existing compiled representation |

ComfyWorkFlow is specific to ComfyUI. Other generation providers may use request/recipe contracts without a graph. Neither naming nor MCP separation transfers the ComfyUI builder to AI Runtime. Constructing JSON, submitting it and qualifying real generation are separate operations.

## Non-goals now

No Controller implementation, automatic extraction, replacement builder, new provider, generic scheduler, inference kernel, Agent-memory layer or GPU/systemd control. Agent is optional personality; GPU Node Manager retains host-wide lifecycle authority; Studio retains user authorization and product state.

ComfyUI, Irodori and YuE are contextual provider examples, not support claims for this empty implementation repository. There are no install/build/run commands, service ports or API routes to configure.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Boundary inventory and deferred cleanup](docs/MIGRATION.md)
- [Contributor instructions](AGENTS.md)
- [AI ecosystem](https://github.com/flamoris-jp/flamoris-ai/blob/main/docs/ai-ecosystem.md)
- [Organization map](https://github.com/flamoris-jp/.github)
- [Repository policy](https://github.com/flamoris-jp/flamoris-commons/blob/main/docs/repository-policy.md)

## 日本語

Generation Controllerは将来の内部生成制御層です。現在は文書のみで、まだ実装しません。Intelligence整備を先行します。Generation MCPの既存ComfyWorkFlow実装は移植せず、後の削除対象として整理します。同じ仕組みをここで作り直す指示ではありません。

ComfyWorkFlowはComfyUI用グラフ・JSON、ExecuteFlowはRuntimeの推論フロー、ExecutionPlanはRuntimeの既存コンパイル済み表現です。内部通信にはMCPを使いません。

## FLAMORIS and license

FLAMORIS is open-source software for creative work and AI-native production. Commercial use of the licensed code is welcome without individual permission. Software is provided as-is without guaranteed individual support; documentation, Issues, tests and source are self-support references.

Code and documentation are licensed under [Apache License 2.0](LICENSE), unless otherwise noted. Models, weights, datasets, media, provider assets and generated outputs may have separate terms.
