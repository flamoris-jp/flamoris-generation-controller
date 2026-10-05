# Retirement baseline and future ownership

Parent: [AI #18](https://github.com/flamoris-jp/flamoris-ai/issues/18). Local scope:
[#1](https://github.com/flamoris-jp/flamoris-generation-controller/issues/1).
This filename is retained for existing links. Controller is documentation-only;
there is no package, running endpoint or source migration to configure.

## Implemented source baseline

- Intelligence #12 supplies the shared non-MCP provider library. Agent #40 uses it
  directly and exposes the internal HTTP API. Studio #63 uses the library for raw
  inference and Agent HTTP for personality/conversations.
- Generation #69 removes custom definition registration/versioning/bindings,
  qualification, v3 composition and the Runtime-delegation bridge. Hub #37 removes
  their tools; Studio #63 removes their dispatch; Runtime #25 removes its matching
  media lowering. These features are retired, not transferred here.
- The original two schema-1 ComfyUI templates and opt-in schema-4 Speech, schema-5
  Music and schema-6 transcription recipes remain. Generation currently co-locates
  their domain logic and external facade. Studio generation still uses that MCP
  compatibility path; it has not already cut over to Controller.

See the owning [Generation retirement contract](https://github.com/flamoris-jp/flamoris-generation-mcp/blob/main/docs/LEGACY_RETIREMENT.md)
and [AI progress](https://github.com/flamoris-jp/flamoris-ai/blob/main/PROGRESS.md)
for reviewed revisions, evidence and operational holds.

## Future contract decisions

| Retained concern | Ownership decision still required |
| --- | --- |
| External MCP tools/annotations/content translation | Remain the external Generation facade |
| Provider/capability metadata and adapters | Candidate Controller contract; use actual retained consumers, no wholesale copy |
| Recipe construction, jobs/status/cancel/results | One shared domain authority; decide library/service packaging and DTOs |
| Managed inputs, historical copy ledgers, assets and transfer | Preserve ownership, storage identity, bounds and retention; no data deletion |
| Host lifecycle and runtime facts | Remain with GPU Node Manager/provider/deployment owners |
| ExecuteFlow and compiled ExecutionPlan | Remain with AI Runtime |

A later Controller task needs its own minimal scope and explicit authorization.
It must not recreate custom registration, qualification, composition or an internal
MCP service bus. Names such as `WorkflowStore` describe retained recipe support,
including non-ComfyUI providers, and are not grounds for indiscriminate deletion.

## Data, uncertainty and acceptance

Saved definitions/recipes, user assets/inputs, grants, historical evidence and
active journals remain intact. New Generation source refuses old custom execution
and preserves opaque active debt without polling, replaying, cancelling or releasing
its busy reservation. Reconcile that work with the previous matched authority before
an operational upgrade; a missing provider record is insufficient proof of release.

Source tests establish retained contracts, not live GPU/model/host readiness.
Controller implementation, real generation, cutover, rollback and persistent-data
changes are separate tasks. Historical Generation #19/#42, #30, #25/#31 and v3
records stay in Git/Issue history and do not act as current implementation orders.
