# FLAMORIS Generation Controller

Shared generation control for FLAMORIS, independent of MCP transport.

**Status: implementation preparation, 2026-10-05.** The user has selected Controller as the next generation architecture work and requested README/AGENTS setup and implementation-policy updates first. This repository still contains documentation only: no Controller package, API or running service exists. The earlier blanket preparation hold is superseded by this decision; code implementation and deployment are subsequent tasks. See [AI #18](https://github.com/flamoris-jp/flamoris-ai/issues/18) and [Controller #1](https://github.com/flamoris-jp/flamoris-generation-controller/issues/1).

## Purpose and target paths

Studio and Generation MCP are two callers of the same Controller. Controller owns shared generation processing; it does not relay Studio requests through MCP.

| Caller | Target path, not yet implemented |
| --- | --- |
| Studio | Studio → non-MCP Controller contract → configured providers |
| ChatGPT / external MCP client | MCP Hub → Generation MCP facade → the same Controller → configured providers |

Both paths use one generation job/input/asset authority and one active-generation reservation. Sharing a Python package alone is insufficient if each frontend creates a separate JobStore. A separate Controller network process is not required by this policy; hosting and internal transport will be selected from the actual callers.

## Responsibilities

| Owner | Responsibility |
| --- | --- |
| Controller | Provider adapters, model/capability metadata, validated generation recipes, requests/jobs/results, managed inputs, staging, generated assets and retention |
| Generation MCP | External tools, MCP validation/annotations, signed MCP ingress and request/result/content translation |
| Studio | Accounts/sessions, user authorization, UI/drafts/presets/history, opaque job/asset/input mappings and browser delivery |
| Providers | Actual media execution and provider-local state |
| GPU Node Manager | Host-wide runtime/GPU lifecycle |
| AI Runtime / Agent | Inference and ExecuteFlow / optional personality and conversations, respectively |

Implementation starts from the retained, reviewed Generation domain, not a new generation framework. It preserves the two builtin image recipes and configured native Speech, Music and transcription recipes. The removed custom ComfyWorkFlow registry/versioning/v3/qualification/Runtime-bridge subsystem is not an extraction source.

## Implementation sequence

1. Record the retained-source inventory, smallest non-MCP contract, hosting, trusted caller context and one state owner.
2. Reuse the retained recipe/provider/job/input/asset code in a transport-independent Controller package, with shared construction and lifecycle.
3. Connect the external Generation MCP facade to that authority while preserving its reviewed compatibility contracts.
4. Replace Studio's Generation MCP gateway with the same non-MCP contract; retain Studio ownership and uncertain-request fences.
5. Verify both callers, packaging, retained state and failure behavior with fake providers; prepare live cutover/rollback separately.

The [implementation plan](docs/IMPLEMENTATION.md) defines the inventory, open decisions and acceptance criteria. Splitting responsibilities does not itself add reference-image generation, arbitrary graph registration, new providers or automatic GPU switching.

## Terminology

| Name | Meaning |
| --- | --- |
| `ComfyWorkFlow` | ComfyUI graph / API-format JSON; ComfyUI executes it |
| `ExecuteFlow` | AI Runtime's inference dependency/data/control flow |
| `ExecutionPlan` | AI Runtime's existing compiled representation |

Non-ComfyUI requests remain generation recipes. Preserve current identifiers such as `WorkflowStore`, `workflows.*` and `workflow_id` until a reviewed compatibility change. JSON construction, submission and real provider acceptance are separate stages.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Implementation plan and retained-source inventory](docs/IMPLEMENTATION.md)
- [Retirement baseline, state continuity and cutover](docs/MIGRATION.md)
- [Contributor and AI-agent instructions](AGENTS.md)
- [AI progress](https://github.com/flamoris-jp/flamoris-ai/blob/main/PROGRESS.md)
- [AI ecosystem](https://github.com/flamoris-jp/flamoris-ai/blob/main/docs/ai-ecosystem.md)
- [Repository policy](https://github.com/flamoris-jp/flamoris-commons/blob/main/docs/repository-policy.md)

## 日本語

Generation Controllerは、StudioとGeneration MCPの両方から呼ばれる共通の生成制御層です。StudioとMCPを直列につなぐ中継ではなく、生成Job/Input/Assetと実行予約を一つに保ちます。

2026-10-05の指示で、README・AGENTSと実装方針の整備へ進みました。ソースはまだ未実装です。現在残っている基本/native生成を再利用し、MCPには外部入口、Studioにはユーザー権限と画面・履歴を残します。具体的なコード実装、実機切替、新しい参照画像機能は後続の作業です。

## FLAMORIS and license

FLAMORIS is open-source software for creative work and AI-native production. Commercial use of the licensed code is welcome without individual permission. Software is provided as-is without guaranteed individual support; documentation, Issues, tests and source are self-support references.

Code and documentation are licensed under [Apache License 2.0](LICENSE), unless otherwise noted. Models, weights, datasets, media, provider assets and generated outputs may have separate terms.
