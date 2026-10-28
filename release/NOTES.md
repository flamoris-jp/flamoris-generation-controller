# flamoris-generation-controller 1.2.1

Fix native SaveVideo V3 registration observed in the operator preflight. Accept only installed MP4/H.264 branches using constant flat API keys `format` and `format.codec`; reject additional dynamic inputs and preserve legacy graph identities. Managed inputs, job authority, output verification and unknown/no-replay remain unchanged. Native metadata and synthetic transport tests do not prove AnimeGen GPU success. See [video contract](../docs/VIDEO.md).

# flamoris-generation-controller 1.2.0

Add bounded Wan I2V/FLF2V declarations through the existing ComfyUI provider, managed inputs, jobs and assets. MP4 output bytes are decoded and compared to the effective recipe before retrieval. See [video contract and pending real-machine acceptance](../docs/VIDEO.md). No production graph is bundled; no live AnimeGen success, rollout or model compatibility is claimed.

# flamoris-generation-controller 1.1.1

Fix declarative graph registration for ComfyUI V3 static COMBO widgets, including the Qwen reference method. Preserve installed choice validation, saved definition identity and existing legacy choices. Dynamic/remote/multiselect COMBO widgets remain unsupported. No schema, external tool, storage or ownership changes.
