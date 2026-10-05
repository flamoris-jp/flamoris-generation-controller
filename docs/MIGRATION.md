# Retained domain separation and state continuity

Parent: [AI #18](https://github.com/flamoris-jp/flamoris-ai/issues/18). Local scope: [Controller #1](https://github.com/flamoris-jp/flamoris-generation-controller/issues/1). The 2026-10-05 decision begins implementation preparation; this repository still has no package or running endpoint. Follow [IMPLEMENTATION.md](IMPLEMENTATION.md) for the staged source plan.

## Source baseline

- Intelligence #12, Agent #40 and Studio #63 implemented the internal Intelligence/Agent non-MCP paths.
- Generation #69/#70 and matched Studio/Hub/Runtime changes retired custom definition registration/versioning, qualification, v3 composition and the Runtime-delegation bridge. Their source deletion is complete and is not repeated or transferred here.
- The two schema-1 builtin ComfyUI templates and configured schema-4 Speech, schema-5 Music and schema-6 transcription recipes remain. Their recipe/provider/job/input/asset domain is currently co-located with the external Generation MCP facade. Studio still calls that MCP compatibility path.

The new ownership work reuses this retained domain. It does not reinstate the retired subsystem. See the [retirement contract](https://github.com/flamoris-jp/flamoris-generation-mcp/blob/main/docs/LEGACY_RETIREMENT.md) and [AI progress](https://github.com/flamoris-jp/flamoris-ai/blob/main/PROGRESS.md) for accepted source and operational status.

## Source separation

| Concern | Destination |
| --- | --- |
| Retained recipe construction and validation, provider/model/capability adapters, jobs/results | Controller core, with one construction/lifecycle owner |
| Managed inputs, upload publication, staging/copy ledgers, generated assets, bounded transfer and retention | Same Controller state authority; preserve storage and immutable identities |
| External MCP tools/annotations, SDK content/errors and signed-envelope verification | Generation MCP facade |
| Verified provenance data and safe domain errors | Transport-independent core contract; ingress remains with each adapter |
| Studio session/CSRF/ownership, history/presets, opaque mappings, request fences and browser delivery | Studio; replace its upstream transport without moving product state |
| Host lifecycle / Runtime inference / Agent conversations | Existing GPU Node Manager / Runtime / Agent owners |

## Reservation and data continuity

Source separation is not persistent-data deletion. Preserve saved recipes/definitions, user assets/inputs, grants, provider copy ledgers, historical evidence and active journals. Record the mapping of IDs, storage roots/namespaces, archive formats and configured providers before a future cutover; do not invent a rename in documentation.

Only one process or otherwise explicitly designed authority can own live generation admission. Do not run old and new owners against one output directory, or copy unresolved active work into independent per-frontend stores. Provider acceptance or durable-write uncertainty keeps the reservation unknown; restart, timeout or missing queue records do not prove release.

Retired custom/delegated active debt stays opaque: preserve its original journal and busy reservation without new polling, replay, cancellation or release. Reconcile using the previous matched authority before an operational upgrade. Keep input-use protection and terminal-state release consistent with generation reservations.

## Staged cutover and rollback

Later source PRs introduce the MCP-free core, connect the external facade and then replace Studio's gateway against the same authority. Preserve the supported external tools, literal IDs/configuration and retained provider behavior, or explicitly version any unavoidable compatibility change. Never hide a fallback to the retired feature or an uncertain resubmission.

Operational planning separately inventories installed versions, active work, backups and restore/rollback conditions. Drain or reconcile the previous authority before activating its replacement. Rollback must also keep one owner and verify data/format compatibility; restarting an old process is not automatically safe after state changes.

Normal contract tests use fake providers. Deployment, configuration/data changes, live GPU/model tests and paid calls are not part of the current documentation task. Neither source extraction nor this plan certifies real generation quality or host readiness.
