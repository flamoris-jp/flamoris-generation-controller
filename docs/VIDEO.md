# Wan / AnimeGen video through Controller

The optional [MiniMax H3 silent-video profile](MINIMAX_H3.md) uses declaration
version 3 and its own frame grid. The Wan version-2 contract below is retained.

Owner: [Controller #31](https://github.com/flamoris-jp/flamoris-generation-controller/issues/31),
resumed from [Generation #25](https://github.com/flamoris-jp/flamoris-generation-mcp/issues/25).
**1.2.1 adds installed native SaveVideo V3 compatibility; real GPU acceptance pending.**

`video.generate` uses the existing ComfyUI provider and the same JobStore,
input leases, reservation journal and asset store as Image/Speech/Music. There
is no AnimeGen service, second Controller, automatic runtime switch or fallback.
Generation MCP and authenticated internal HTTP expose the existing 25 operations;
the tool schemas do not change. This does not implement a Studio Video editor.
Existing linked external result discovery and MP4 delivery can consume video assets.

## Registration contract

Use `comfy.register(name, graph, declaration)` with a **real reviewed API-format
export**, not a UI-format authoring graph. The declaration extends the existing
[installed-node contract](DECLARED_COMFY.md) with `version: 2` and:

```json
{
  "version": 2,
  "video": {"mode": "i2v", "conditioning_node": "ACTUAL_NUMERIC_NODE_ID"},
  "parameters": {},
  "constants": [],
  "output": {"node": "ACTUAL_NUMERIC_SAVEVIDEO_ID"}
}
```

This fragment describes fields only: the symbolic IDs/empty parameters are
intentionally not a runnable registration request. No production graph is
bundled. The synthetic `TestContext`/`TestDecode` tests are contract fixtures,
not AnimeGen inference graphs and must never be installed or registered in production.

| Field | Required graph relationship |
| --- | --- |
| `video.mode=i2v` | Exactly one `WanImageToVideo` conditioning node |
| `video.mode=flf2v` | Exactly one `WanFirstLastFrameToVideo` conditioning node |
| `start_image` | Required managed image binding on `LoadImage.image`, connected directly to the conditioning node's `start_image` input |
| `end_image` | Required only for FLF2V, a distinct `LoadImage.image` binding connected to `end_image` |
| `width`, `height` | Integer parameters bound to conditioning `width` / `height`, each bounded to 64–1024 |
| `frames` | Integer parameter bound to conditioning `length`, bounded to 5–161 |
| `fps` | Integer parameter bound to the single `CreateVideo.fps`, bounded to 1–32 |
| `output.node` | A single `SaveVideo` receiving `CreateVideo` output 0; fixed `format=mp4`, `codec=h264` |

Each semantic numeric parameter must declare a minimum/maximum within these
bounds. Dimensions must be multiples of 16 and at most 832×832 total pixels;
frames must satisfy `4n+1`; encoded duration must be at most 10.1 seconds.
`batch_size=1`; each supported KSampler/KSamplerAdvanced uses at most 50 steps.
All other widgets, links, models, constants and output bindings follow the
existing strict declaration and operator-node allowlist. Model/LoRA filenames
must come from installed catalogs; this package invents none.

The profile requires direct image links and static scalar widgets. In 1.2.1,
installed native `SaveVideo` V3 dynamic metadata additionally supports exactly
the MP4/H.264 branch: API prompt inputs are flat `"format": "mp4"` and
`"format.codec": "h264"`, both declared constants. ComfyUI assembles these into
nested execution arguments; Controller retains the flat prompt unchanged.
The installed MP4 and H.264 branches must exist and must not introduce additional
required inputs. Optional encoding/CRF branches, the hidden legacy `codec`,
other dotted keys, nested JSON values and parameterized choices are rejected.
Legacy static `format` / `codec` graphs keep their original representation,
identities and validation. This exception applies only to declaration v2 video
output; it does not enable general dynamic widgets for image declarations.
Resizing chains, VHS outputs and other dynamic contracts remain unsupported.

Both modes have distinct immutable definition IDs and content digests. A
build with an unexpected `end_image`, or a FLF2V build without it, fails.
Mode is fixed by the registered graph, not a mutable string that can silently
fall back to I2V. Binding proves the reference reaches the declared node; it
does not prove model compatibility or pixel-exact endpoint adherence.

Registered descriptors use metadata schema 4, `capability_id=video.generate`
and `video.profile=wan-video-v1`. They do not carry Image metadata. Saved recipes
use schema 11 / `template=registered-video`. Existing image declaration v1,
digests, recipes and storage identities remain unchanged. Older Studio Image
normalization skips this profile instead of presenting it as an image generator.
Capabilities advertise the video route only with the relevant configured Wan,
LoadImage/CreateVideo/SaveVideo node policy; this is availability, not a
registered production workflow or generation-readiness attestation.

## Use the existing lifecycle

1. Capture installed node/model/source identities and review the actual successful
   export. Add only reviewed classes to operator `FLAMORIS_DECLARED_COMFY_NODES`.
   Confirm shared input storage and the existing runtime configuration.
2. Register the graph/declaration; inspect `comfy.get` and pin the returned digest.
3. Upload PNG/JPEG/WebP using `inputs.upload.begin/write/finish`, or snapshot
   an owned generated image with `inputs.create`. Input IDs are not provider filenames.
4. `workflows.build(template=<definition id>, definition_digest=<digest>,
   parameters={start_image: <managed id>, width: ..., height: ...,
   frames: 81, fps: 16, ...})`; FLF2V also supplies `end_image`.
5. Submit the returned `workflow_id` **once** with `jobs.submit`; use
   `jobs.status/result/cancel`. Node policy, model presence, effective budgets
   and current installed metadata are rechecked before input staging / POST.
6. Retrieve `assets.list`, then `assets.prepare/read`. Verify the whole-file
   SHA-256 after reassembling bounded chunks and play the resulting MP4.

All existing ambiguity protections apply. A POST timeout stays unknown/reserved
across restart and is never replayed. A known acknowledged execution restores
its output node, effective video dimensions/count/FPS and job namespace without
another POST. Scoped cancellation uses existing ComfyUI support. A runtime with
no targeted interrupt reports running cancellation unsupported; global GPU
interrupts are never substituted. Input copies are released only on observed
terminal provider outcomes. Queue absence alone does not prove cancellation.

## MP4 identity and verification

The supported upstream native `SaveVideo` serializes its saved preview under
history `images`, with filename/subfolder/type. Primary source anchors:
[SaveVideo and CreateVideo](https://github.com/Comfy-Org/ComfyUI/blob/c194dd00cd42aa18d9dbf27d977bf6b85d9ea565/comfy_extras/nodes_video.py),
[PreviewVideo.as_dict](https://github.com/Comfy-Org/ComfyUI/blob/c194dd00cd42aa18d9dbf27d977bf6b85d9ea565/comfy_api/latest/_ui.py),
[Wan image conditioning](https://github.com/Comfy-Org/ComfyUI/blob/c194dd00cd42aa18d9dbf27d977bf6b85d9ea565/comfy_extras/nodes_wan.py).
These source contracts are not an installed LIME receipt.

Only the declared output node can publish a single MP4 in
`flamoris/<job_id>_<counter>_.mp4`. Wrong container, another job prefix,
undeclared count, temp/input folder or an image preview fails. Output role is
`video_clip` on port `video`. Provider completion metadata is distinct from
successful byte retrieval/decoding; a completed job with malformed bytes fails
`jobs.result`/asset preparation rather than returning a usable movie.

Both direct materialization and bounded streaming check a 64 MiB clip ceiling
without raising the existing global transfer limits. Before publishing bytes,
PyAV decodes a memory-backed MP4 and checks one H.264 video track, submitted
dimensions/FPS, exact frame count and frame timestamps. GIF/WebM, audio tracks,
wrong dimensions/count/rate and corrupt/truncated streams are rejected. Transfer
integrity, disk budgets/deadlines and archived metadata/role retrieval remain
owned by existing asset infrastructure. Optional provider retention recognizes
only the same job-owned native MP4 namespace.

PyAV is BSD-3-Clause; bundled FFmpeg/codecs retain their own build-specific
licensing/distribution terms. Model weights and generated media retain their
separate terms. No third-party implementation, weights or production media is
copied into this repository.

## Real-machine acceptance still required

Generation MCP / Controller 1.2.0 were released and deployed. The subsequent
read-only operator preflight confirms installed Wan I2V nodes and the native
V3 SaveVideo schema at ComfyUI revision
`c194dd00cd42aa18d9dbf27d977bf6b85d9ea565`. Both FP8 weights are present on the
host, but the operator node policy excludes required video classes and the
model links resolve outside the Generation model mount. These need deliberate
operator configuration; Controller confinement must not be weakened. Version
1.2.0 did not support that installed SaveVideo schema. The V3 metadata fixture
in this repository contains only native node schemas, with no host paths or
production media. It verifies registration compatibility, not GPU inference.

A new minimal graph candidate was reconstructed from pinned public AnimeGen
FP8 distributor material because the old successful export was unavailable.
It remains unqualified until installed validation, managed image staging,
actual GPU execution, verified MP4 retrieval and visual review succeed. No
AnimeGen job has been submitted. Static/fake-provider tests are not live
acceptance receipts. The Kachinco project remains unchanged.

Phase 1 uses the actual FP8 high/low model and four-step Lightning graph after
checking installed identities, licenses and `--disable-pinned-memory`. Inspect
shared authority for active/unknown work, then activate ComfyUI through LIME
Manager's GPU exclusivity rather than direct systemd control. Record and restore
the preceding runtime when work is settled. Deploy the matched facade candidate
with this Controller pin before expecting video registration through MCP.

Use the existing `red-shoes.webp` as the unmodified managed start image. Prompt:

> The red high heels perform a graceful, precise tango step on a glossy black
> floor. Subtle reflections and flowing shadows. Fixed camera, consistent shoes
> and proportions, monochrome with crimson accents, elegant cinematic anime
> style. No text, no zoom, no morphing.

Use 81 frames at 16 FPS and dimensions selected from the **real successful graph
and input aspect ratio**. The encoded duration is **5.0625 seconds**, not exactly
5.0. Do not trim the final FLF2V pose silently to obtain a round duration. Record
definition digest, input digests, model/LoRA revisions, seed, effective graph
parameters, job/result/asset IDs, MP4 hash, decoded dimensions/rate/count/duration,
elapsed time, sampled GPU/VRAM peak and visible quality. Keep private topology,
paths, credentials and production media out of public Issues/commits.

Phase 2 requires a separately reviewed FLF2V export plus an approved final-pose
image. Compare identical seeds/settings where valid, endpoint image fidelity,
motion, artifacts, elapsed time and observed VRAM. Report model incompatibility
explicitly; Phase 1 remains independently usable.

Future clip chaining can publish a `last_frame` PNG derived from the **decoded
verified MP4**, then reuse the ordinary `inputs.create` managed snapshot for
the next start image. That additional output/extraction operation is not
implemented here. Keep video/frame asset identity, digest, source job and
frame index together; never accept an arbitrary provider path as the next frame.

This compatibility change does not implement a Studio Video editor or claim
production graph qualification, actual video generation or Kachinco mutation.
