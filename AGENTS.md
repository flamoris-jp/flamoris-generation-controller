# Contributor and AI-agent instructions

This repository documents a future internal generation-domain controller. It has no Controller implementation. Read README.md, docs/ARCHITECTURE.md, docs/MIGRATION.md and [AI #18](https://github.com/flamoris-jp/flamoris-ai/issues/18).

## Current authorization

Documentation review/fixes and explicitly requested documentation merges are permitted in Chat. Do not implement Controller, delegate Controller work to Work, migrate/delete source, deploy, restart, change credentials or run providers. Documentation merge is not implementation resumption. The Intelligence cleanup elsewhere is implemented; check current progress before ordering new work.

## Fixed boundaries

1. Generation MCP is an external adapter. Internal application/service calls use non-MCP contracts, not MCP Hub.
2. `ComfyWorkFlow` means ComfyUI graph/API-format JSON. `ExecuteFlow` means Runtime inference flow; `ExecutionPlan` remains the Runtime's compiled representation. Do not use bare `Workflow` for a new FLAMORIS design concept. Preserve exact current identifiers and historical quotations when required for truthful documentation.
3. The existing Generation MCP ComfyWorkFlow subsystem is not a migration source for this repository. Its custom registry/v3/Runtime-bridge removal is implemented in Generation #69 and matched consumers; do not automatically recreate it here.
4. Other provider requests are not automatically ComfyWorkFlow. Provider execution stays with the provider. No generic inference or scheduling platform is inferred from generation requests.
5. Agent is optional personality/conversation/memory. GPU Node Manager retains host lifecycle authority. Studio retains product state and user authorization.
6. Future internal and external callers must share one generation state owner, not per-frontend stores/reservations.

## Design and retained safety

Before any future implementation, inspect actual source/tests/callers and record exact retained versus removed behavior. Do not invent endpoints, ports, dependencies, formats or commands. Source retirement is not deletion of assets, saved definitions, user data, evidence or unresolved jobs.

Retained paths must keep authorization, immutable inputs, bounded decoding/staging/transfer, safe paths and errors, provenance and no replay of uncertain submissions. Do not silently restore a removed feature through fallback, bypass checks or fabricate successful execution. Separate JSON validity, provider availability, qualification and authorization.

Credentials stay in operator configuration, not browser inputs, persona text, prompts, public errors or repository files. No arbitrary shell/URLs/paths, model downloads or implicit GPU activation.

## Review

Use focused documentation commits. Read the resulting text for contradictions, not only keyword replacement. Merge only with explicit authorization. Do not claim runtime tests or live qualification from a documentation-only change. Keep the design Issue open for its remaining deliverables.

Follow the [repository policy](https://github.com/flamoris-jp/flamoris-commons/blob/main/docs/repository-policy.md). Keep public documentation portable; preserve licenses and third-party/model terms. Do not force-push shared branches.
