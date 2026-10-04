# Boundary inventory and deferred cleanup

Parent: [AI #18](https://github.com/flamoris-jp/flamoris-ai/issues/18). Local scope: [#1](https://github.com/flamoris-jp/flamoris-generation-controller/issues/1); MCP-side scope: [Generation #67](https://github.com/flamoris-jp/flamoris-generation-mcp/issues/67).

This filename is retained for existing links. **This is not an extraction order. Controller is not implemented now; Intelligence cleanup comes first.**

## Disposition to review later

| Existing concern | Direction, not present authorization |
| --- | --- |
| MCP tools/annotations/transport/content mapping | Keep external MCP responsibility in Generation MCP |
| ComfyWorkFlow builder/registry/bindings and associated subsystem | Later removal from Generation MCP, not transfer into Controller or Runtime; inventory exact boundaries first |
| Provider metadata/adapters and capability registry | Candidate future Controller responsibility; no blanket copy or rewrite authorized |
| JobStore/reservation/status/cancel/results | Decide future ownership without duplicating state or losing unresolved work; no move now |
| Managed inputs/staging/assets/transfer | Preserve data, access checks and safety; future ownership review, not data deletion |
| Qualification/invalidation records and checks | Separate safeguards needed by retained paths from checks owned only by a retired feature; no bypass or evidence deletion by default |
| Host lifecycle and measured runtime facts | Remain with GPU Node Manager/provider/deployment owners |
| ExecuteFlow, compiled ExecutionPlan and native inference | Remain with AI Runtime |
| v3 composition/bridge/provider expansion | Deferred; not a prerequisite for Intelligence cleanup or simple ComfyWorkFlow JSON construction |

This is a domain-level inventory, not a completed file-by-file audit. The later removal task must pin the source revision and exact source/test/tool/caller scope. In particular, a generic recipe store may support non-ComfyUI providers; names alone are insufficient grounds to delete it.

## Before any later Generation deletion

Record affected external tools and Studio consumers, the behavior that remains, unsupported-call errors, data readers, saved definitions/assets/inputs/evidence, active or uncertain reservations, and rollback needs. Do not treat code retirement as permission to drop persistent data or break retained provider paths silently.

Existing source/test evidence can inform design without migrating the implementation. A future Controller task must be minimal and separately authorized; it is not ordered to recreate the same subsystem.

## Evidence and history

Builder-only tests establish JSON construction, not provider execution or production qualification. Later retained provider/domain tests must cover authorization, bounds, state and uncertain outcomes; external MCP tests cover mapping rather than a second state machine. Real generation, cutover and rollback require separate operational approval.

Historical references include Generation MCP #19/#42 (ComfyUI definitions and reference semantics), #30 (managed inputs), #25/#31 (providers), and #45-related v3 work. Their literal code/file names and acceptance records remain history. No completed Issue or merged PR is reopened or reverted by this document.

## Current completion boundary

Only documentation review, fixes and requested documentation merges are in scope. Exact inventories, internal API/packaging decisions, Controller implementation, source deletion, reference-image expansion and live migration remain future work. Keep the design/correction Issues open for their actual remaining scope.
