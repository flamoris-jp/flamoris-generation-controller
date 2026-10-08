# Updater compatibility (1.0.0)

The original adoption review/fix loop and CI passed. A later pre-deployment
correction expands the explicit retained-resource contract; its current PR/CI
state must be checked separately. Version metadata does not certify a published
release or a real-host update. No production data, credentials or service is
changed by source work.

`flamoris-generation-controller-update-owner --config /protected/owner.json` serves the application's
separate bounded mTLS Owner endpoint. Its schema/resource validation lives in
`flamoris_generation_controller.updater`. Updater Web is independent of Studio.

Applications install the MCP-independent SDK pinned to an immutable Updater
source commit. Do not install the full Updater distribution into this environment.
Managed deployments set `FLAMORIS_UPDATE_REQUIRED=1` and
`FLAMORIS_UPDATE_STATE=/private/owner-state`; missing state fails closed.
Accepted work is durable across processes. Unknown/cancelled work remains a
blocker across restart; no timer, PID disappearance or reconnect clears it.

The independent entry CLI plans and verifies existing schemas, backup and an
isolated restore before target activation. It does not initialize databases,
erase data or auto-reconcile uncertain requests. Legacy services/external writers
must be stopped through their existing authorized maintenance procedure.

Read the [complete application entry contract](https://github.com/flamoris-jp/flamoris-updater/blob/feat/application-entry-v1/docs/APPLICATION_ENTRY.md)
for required resource classes, Owner/Helper/Entry configuration fields, restore
isolation, fixed lifecycle bindings and failure handling. Private profiles and
signed CI-built artifacts are required; source compatibility is not operational
acceptance. Preserve all current data, grants, configuration identities and
independently readable history.

Controller is a pinned library inside the Generation MCP artifact. Keep one
Controller authority and admission state; do not deploy another JobStore/Owner
for the same recipes/output root. Retired/unknown reservations are preserved.

The Owner profile requires five explicit tree resources: `configuration`,
`definitions`, `inputs`, `recipes`, and `outputs`. The output resource includes
generated assets, managed input snapshots/uploads, job reservations, journals,
and the authority lock. The separate provider input tree remains retained even
when it contains only staged reference images. Existing definition and recipe
trees are retained rather than silently omitted. Shared model storage is an
external read-only library and must remain an unchanged read-only mount; it is
not an application-owned backup resource.
