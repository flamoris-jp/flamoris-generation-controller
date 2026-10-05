# Retained domain separation and state continuity

Parent: [AI #18](https://github.com/flamoris-jp/flamoris-ai/issues/18). Local scope: [Controller #1](https://github.com/flamoris-jp/flamoris-generation-controller/issues/1). The 2026-10-05 implementation instruction authorizes the retained-domain package and matched caller changes. The source supplies an internal HTTP adapter; live cutover remains pending. Follow [IMPLEMENTATION.md](IMPLEMENTATION.md) and [API.md](API.md).

## Source baseline

- Intelligence #12, Agent #40 and Studio #63 implemented the internal Intelligence/Agent non-MCP paths.
- Generation #69/#70 and matched Studio/Hub/Runtime changes retired custom definition registration/versioning, qualification, v3 composition and the Runtime-delegation bridge. Their source deletion is complete and is not repeated or transferred here.
- The two schema-1 builtin ComfyUI templates and configured schema-4 Speech, schema-5 Music and schema-6 transcription recipes remain. Their recipe/provider/job/input/asset domain now belongs to Controller. The facade hosts one shared runtime; Studio calls its authenticated HTTP API.

The new ownership work reuses this retained domain. It does not reinstate the retired subsystem. See the [retirement contract](https://github.com/flamoris-jp/flamoris-generation-mcp/blob/main/docs/LEGACY_RETIREMENT.md) and [AI progress](https://github.com/flamoris-jp/flamoris-ai/blob/main/PROGRESS.md) for accepted source and operational status.

## Subsequent registration profile

Controller #7 and matched Generation #72 / Hub #39 add the bounded
[registration profile](COMFY_REGISTRATION.md) after this extraction baseline.
The current catalogs expose 25 operations. New immutable definitions live in
`FLAMORIS_WORKFLOW_DIR/comfy-definitions-v1` and built recipes use schema 7;
original schema 1/4/5/6 files keep their identities. Reference execution requires
the configured shared `COMFYUI_INPUT_ROOT` and protects copies with the same
lease/reservation authority. Drain or reconcile schema-7 jobs and protected copies
before rollback to an older binary; preserve definitions, recipes and unknown
journals. The no-database-migration statement below concerns generation transport
extraction only; Studio assistant model handoff has its own migration.

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

Source separation is not persistent-data deletion. Preserve saved recipes/definitions, user assets/inputs, grants, provider copy ledgers, historical evidence and active journals. Record the mapping of IDs, storage roots/namespaces, archive formats and configured providers before operational cutover; do not invent a rename in documentation.

Only one process or otherwise explicitly designed authority can own live generation admission. Do not run old and new owners against one output directory, or copy unresolved active work into independent per-frontend stores. Provider acceptance or durable-write uncertainty keeps the reservation unknown; restart, timeout or missing queue records do not prove release.

Retired custom/delegated active debt stays opaque: preserve its original journal and busy reservation without new polling, replay, cancellation or release. Reconcile using the previous matched authority before an operational upgrade. Keep input-use protection and terminal-state release consistent with generation reservations.

## Staged cutover and rollback

Matched source PRs introduce the MCP-free core, connect the external facade and replace Studio's gateway against the same authority. Preserve the supported external tools, literal IDs/configuration and retained provider behavior, or explicitly version any unavoidable compatibility change. Never hide a fallback to the retired feature or an uncertain resubmission.

Operational planning separately inventories installed versions, active work, backups and restore/rollback conditions. Drain or reconcile the previous authority before activating its replacement. Rollback must also keep one owner and verify data/format compatibility; restarting an old process is not automatically safe after state changes.

Normal contract tests use fake providers. Deployment, configuration/data changes, live GPU/model tests and paid calls are not part of the source implementation task. Neither source extraction nor this plan certifies real generation quality or host readiness.

## Exact storage and configuration continuity

| Existing identity | After extraction |
| --- | --- |
| `FLAMORIS_WORKFLOW_DIR`, recipe UUIDs and schema 1/4/5/6 files | Same paths/IDs/formats; no import, rewrite or deletion |
| `FLAMORIS_OUTPUT_DIR/job-authority/active.json` | Same durable reservation/recovery record, including unknown and opaque retired debt |
| Output root job UUID directories, materialized files and archived metadata | Same IDs/directory-fd confinement/archive formats |
| `managed-inputs`, upload journals, lease/copy records, transfer and retention records | Same relative namespaces, formats, expiry and bounded storage; moved source only |
| `comfyui-input-authority`, provider shared-input ledger | Same authority/storage identities and retained-copy protection |
| `external-provenance` signed ingress replay records | Remain owned by MCP ingress at the same output-root namespace |
| Provider configuration `FLAMORIS_*` keys | Same provider/storage values; MCP ingress/bind keys remain facade settings |
| New `controller-authority/owner.lock` | Lifetime local exclusion; never delete while any authority is alive; not an active-job record |

Do not start an old pre-lock binary alongside this version: the new lock cannot constrain software that does not acquire it. Drain/reconcile, back up and stop the previous owner first. Unknown reservations survive shutdown; acquiring the new lock is not permission to release them. Network/distributed filesystem locking is outside this contract.

Studio preserves its DB schema, user accounts, opaque mappings, request fences and history. No database migration is added. Its operator configuration must explicitly change from `/mcp` or Hub to `/api/v1/generation`, supply the separate service credential and empty the generation namespace. The new client rejects legacy endpoints rather than silently falling back. Roll back both matched clients/host if necessary, after checking the single-owner and durable-state conditions.
