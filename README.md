# FLAMORIS Generation Controller

Shared bounded generation processing for Studio and the external Generation MCP facade.

**Status: source implementation, 2026-10-05; live cutover pending.** The retained generation domain is now the MCP-free `flamoris_generation_controller` package. [Generation MCP](https://github.com/flamoris-jp/flamoris-generation-mcp) constructs one Controller and exposes its external MCP tools and authenticated internal HTTP API on the same listener. Studio calls the HTTP API directly. See [Controller #1](https://github.com/flamoris-jp/flamoris-generation-controller/issues/1) and [AI #18](https://github.com/flamoris-jp/flamoris-ai/issues/18) for source acceptance.

| Caller | Source path |
| --- | --- |
| Studio backend | Authenticated Controller JSON/binary HTTP API → shared Controller → providers |
| ChatGPT / external MCP | MCP Hub → Generation MCP facade → same Controller → providers |

Controller owns retained recipes, provider adapters and model/capability metadata, jobs/results, managed inputs/uploads/staging, generated assets, transfers and retention. Providers execute. Studio retains accounts/session/CSRF, per-user ownership, opaque mappings, history, request fences and browser delivery. MCP retains tools/annotations, signed-envelope verification and SDK content/errors. GPU Node Manager owns host lifecycle; Runtime owns inference/ExecuteFlow/compiled ExecutionPlan; Agent remains optional.

## Install and library contract

Python 3.11+ and Linux/POSIX local filesystem locking are required. The base package does not require MCP, Hub, Studio or an HTTP server. The optional HTTP adapter uses Starlette:

```sh
python -m pip install '.[http]'
```

```python
from flamoris_generation_controller.config import Settings
from flamoris_generation_controller.contracts import CallerContext
from flamoris_generation_controller.runtime import GenerationController

controller = GenerationController(Settings.from_env())
try:
    metadata = await controller.invoke("workflows.list", {}, context=CallerContext.internal())
finally:
    await controller.close()
```

Construct the runtime once per generation authority, never per caller/request. A lifetime ownership lock rejects a second runtime for the same output root before provider construction or journal recovery. It does not coordinate different storage roots/hosts or own the GPU. Library callers are trusted authority-process code; HTTP callers must authenticate.

## Internal HTTP API

The initial host is Generation MCP's existing HTTP listener; an extra daemon or port is unnecessary. Configure the private operator `FLAMORIS_CONTROLLER_TOKEN` with 32–512 printable ASCII credential characters. An unset token disables internal operations. Studio uses the same private value as `STUDIO_GENERATION_TOKEN` and sets `STUDIO_GENERATION_ENDPOINT` to the exact `/api/v1/generation` base. `STUDIO_GENERATION_NAMESPACE` must be empty. External Hub/MCP credentials and provenance signatures are separate.

POST `/api/v1/generation/{operation}` accepts a strict ordinary JSON argument object. Results are JSON objects, except `assets.get`, which returns bounded image bytes. No MCP envelope, SDK object, identity header or caller-provided provenance enters this contract. The configured service credential grants the trusted backend the bounded operation surface; Studio still enforces all user ownership. See [API contract](docs/API.md).

## Retained behavior and storage

The two builtin Image recipes and configured native Irodori Speech, YuE2 Music and SheetSage2 transcription recipes retain schemas 1/4/5/6, IDs, profiles and limits. Existing `workflows.*`, `workflow_id`, `WorkflowStore` and `FLAMORIS_*` provider/storage keys keep their meaning. Reference-image generation, custom graph registration/versioning/v3/qualification and Runtime delegation remain unavailable.

Reservations are journaled before submission. Unknown acceptance or journal commit remains reserved across disconnect/restart and is never retried or released implicitly. Scoped cancellation, immutable input leases, output roles, archive retrieval, transfer and retention keep their existing protections. Retired active debt stays opaque and reserved. [Migration and rollback](docs/MIGRATION.md) describes unchanged storage and the new ownership lock.

## Development and evidence

```sh
python -m pip install -e '.[dev,http]'
ruff check .
ruff format --check .
pytest
python -m build
```

Normal tests use fake providers, bounded media and isolated storage. They exercise original domain behavior, authentication/permissions/bounds, duplicate processes, shared admission and unknown/restart/no-replay. Generation MCP and Studio own their adapter/integration and account-isolation tests. Installed-wheel checks import core without MCP. These checks do not attest real GPU/model readiness or deploy anything.

- [Architecture](docs/ARCHITECTURE.md)
- [Implementation inventory and decisions](docs/IMPLEMENTATION.md)
- [API contract](docs/API.md)
- [Migration and rollback](docs/MIGRATION.md)
- [Contributor instructions](AGENTS.md)
- [AI progress](https://github.com/flamoris-jp/flamoris-ai/blob/main/PROGRESS.md)
- [Repository policy](https://github.com/flamoris-jp/flamoris-commons/blob/main/docs/repository-policy.md)

## 日本語

StudioとGeneration MCPが、同じControllerの生成処理・実行予約を使う実装です。Studioは認証付きHTTP APIへ直接接続し、MCPには外部向けの変換処理を残します。元の生成機能と保存形式は維持し、旧独自ComfyWorkFlow機能は復活させていません。実機への反映・既存実行の整理・provider受け入れは別の作業です。

## License and support

Code and documentation are [Apache-2.0](LICENSE) unless otherwise stated. Retained domain source originates from FLAMORIS Generation MCP. Models, datasets, media and provider/generated assets may have separate terms. FLAMORIS is provided as-is without guaranteed individual support.
