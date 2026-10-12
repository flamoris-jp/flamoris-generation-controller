# FLAMORIS Generation Controller v1.0.1

Optional operator-only `FLAMORIS_YUE2_OUTPUT_ROOT` publishes validated Music audio, ABC score and replay metadata into dated per-song bundles in an existing configured directory. Private job/workflow paths, asset IDs, API retrieval, reservation and no-replay behavior are preserved; publication is disabled by default. Published copies are independent and never overwritten or recreated after operator deletion.

Missing/symlink/colliding destinations report publication failure without failing completed generation. Operator directory permissions and container bindings require deployment configuration. ComfyUI image originals and native provider availability are unchanged.

This release publishes the Controller wheel/source and checksums. Generation MCP embeds the pinned library and owns the runnable image/catalog; do not start a second authority. Release publication does not change hosts or move/delete existing data.
