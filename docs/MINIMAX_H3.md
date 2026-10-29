# MiniMax H3 silent video

This optional profile extends the existing ComfyUI provider. It does not start
another Controller, submit directly to a GPU runtime, download models, or change
operator policy. Wan declaration version 2 and its existing definition digests
remain unchanged.

Declare `version: 3`, with `video.mode` equal to `i2v` or `flf2v`, and the actual
`MiniMaxH3ImageToVideo` node ID as `video.conditioning_node`. Metadata schema 4
reports `video.profile=minimax-h3-video-v1`. Recipe schema 11, the 25 external
operations and authenticated internal HTTP boundary are retained.

## Execution contract

- Required managed `start_image` connects directly from `LoadImage` to
  `MiniMaxH3ImageToVideo.first_frame`. FLF2V additionally requires a distinct
  managed `end_image` connected to `last_frame`. I2V cannot bind a hidden last frame.
- Width/height bind directly to the conditioning node, use multiples of 32,
  remain within 64–1024, and cover at most 832×832 pixels.
- Frame count binds directly to `length`, is within 5–161 and satisfies
  `17k+5`. Consequently the largest admissible count is 158. FPS binds directly
  to `CreateVideo.fps`, with both declaration bounds fixed to 24.
- Exactly one `SamplerCustomAdvanced` receives the H3 latent output and its
  `BasicGuider` receives the H3 conditioning. The guider and `BasicScheduler`
  use the same model connection; `RandomNoise` and `KSamplerSelect` are explicit.
  Scheduler steps are bounded to 1–50 and denoise is 1.0.
- Exactly one `VAEDecode` receives sampler output 0 and the same VAE used by
  conditioning. Its images go directly to `CreateVideo`. This initial profile
  excludes tiled decode, guide chains, reference-to-video and resizing chains.
- Output is **silent** MP4/H.264 through the existing job-owned `SaveVideo`
  namespace. An audio connection is rejected. Existing MP4 byte bounds and full
  decoded geometry, frame count, FPS and timestamp verification still apply.

Unsupported frames or FPS fail before provider submission rather than relying
on H3's implicit frame rounding. A 124-frame clip at 24 FPS encodes 5.1667 seconds.
The source describes a trained range beginning at roughly 124 frames; acceptance
of shorter contract fixtures is not a quality recommendation.

Selecting native `VAEDecode` does not disable ComfyUI's own out-of-memory
fallback. Real acceptance must inspect runtime logs for any automatic tiled
decode and compare brightness continuity; this profile does not claim that
choosing another model eliminates temporal seams.

## Source and operational acceptance

Primary source contracts were checked at ComfyUI revision
`c194dd00cd42aa18d9dbf27d977bf6b85d9ea565`:

- [H3 conditioning and frame grid](https://github.com/Comfy-Org/ComfyUI/blob/c194dd00cd42aa18d9dbf27d977bf6b85d9ea565/comfy_extras/nodes_minimax_h3.py)
- [Native video output](https://github.com/Comfy-Org/ComfyUI/blob/c194dd00cd42aa18d9dbf27d977bf6b85d9ea565/comfy_extras/nodes_video.py)
- [Official H3 guide](https://docs.comfy.org/tutorials/video/minimax/minimax-h3)
- [Model distribution and terms](https://huggingface.co/Comfy-Org/MiniMax-H3)

No weights or third-party runtime implementation are bundled. Model licensing
is separate from this repository's Apache-2.0 code license. Operator deployment
must check the applicable model/distribution terms.

Use installed node specifications, exact model identities and an operator-reviewed
API graph for registration. The tests use synthetic models and contracts. Source
acceptance does not certify AMD execution, quantized kernels, memory use, endpoint
fidelity, or temporal stability. Follow the existing managed-input and one-job
submission/observation/unknown/no-replay lifecycle. Runtime changes remain owned
by the configured GPU Node Manager.
