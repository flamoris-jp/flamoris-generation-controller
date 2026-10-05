# ComfyWorkFlow registration and reference-image generation

Scope: [Controller #6](https://github.com/flamoris-jp/flamoris-generation-controller/issues/6).
This is a new Controller-owned bounded profile, distinct from retired schema 2/3
definitions, versioning, v3 composition and Runtime delegation.

The initial profiles accept checkpoint-based ComfyUI core txt2img and init-image
img2img API-format graphs. Numeric node IDs may vary. The exact reviewed graph
topology, one sampler, one output, batch size one and bounded parameter widgets
are enforced. Custom nodes, arbitrary input files/URLs, output paths, extra nodes
and undeclared widgets are rejected. IPAdapter/ControlNet and other model families
need their own reviewed profiles; img2img does not claim their semantics.

External tools are `comfy.register(name, graph)` and `comfy.get(definition_id)`.
The same operations are exposed by authenticated Controller HTTP. Existing
`workflows.list` includes graph-free descriptors. Registration is static validation,
with `readiness.state=validated` and `live_provider_verified=false`. It does not run
the graph, certify installed models, activate the GPU or claim generation success.

Each normalized graph receives `comfy-<sha256>` as its definition ID. Display-name
changes do not create duplicate definitions. The new bounded directory is
`FLAMORIS_WORKFLOW_DIR/comfy-definitions-v1`; old definitions are not imported or
reinterpreted. Up to 64 definitions are retained. Definitions are immutable;
register changed content for a new identity. Digest integrity is rechecked on use.

## ChatGPT sequence

1. Use `models.list(kind="checkpoint")` and select an installed checkpoint.
2. Read `examples/reference-image-comfy.json`, replace the checkpoint widget with
   that model name, and call `comfy.register`. A different numeric node numbering
   is accepted if all links preserve the supported topology.
3. Upload an image with `inputs.upload.begin/write/finish`, or snapshot an existing
   generated asset with `inputs.create`. Use the returned managed `input_id`.
4. Call `workflows.build` with the returned definition ID as `template`, its digest
   as `definition_digest`, and parameters such as:

```json
{
  "checkpoint": "installed-checkpoint.safetensors",
  "positive_prompt": "Describe the desired result",
  "reference_image": "32-character-managed-input-id",
  "denoise": 0.65
}
```

5. Submit the built `workflow_id` once, then use `jobs.status/result` and the
   existing Asset tools. Registration ID and built recipe ID are different.

The img2img graph reads the managed reference, scales it with Lanczos without
cropping to the bounded width/height, VAE-encodes it and uses it as the sampler's
initial latent. A lower denoise usually preserves more of that initialization;
quality and suitable settings require actual model testing.

The graph's `LoadImage.image` must be exactly `$reference_image` at registration.
The Controller replaces it only with its own confined provider input copy during
submission. Callers cannot select filenames or local paths. Missing, expired or
changed inputs and absent shared input storage reject before generation POST.
Source image decoding, byte/pixel bounds, immutable leases and copy-ledger checks
precede submission. An uncertain POST retains the job reservation and protected
copy; neither reconnect nor restart resubmits it. Copies are released only after
a definite rejection or observed terminal provider outcome.

## Acceptance and rollout

Source tests use synthetic images and fake ComfyUI HTTP. Actual model/GPU success
remains unverified until separately requested deployment acceptance. Install the
matched Controller/Generation facade and refresh the Hub's 25-tool catalog.
Configure the existing shared `FLAMORIS_COMFYUI_INPUT_ROOT` explicitly for reference
execution. Studio's existing builtin Image UI remains on its retained contract;
the new external ChatGPT path does not add a Studio Image editor feature.

New built recipes use schema 7. Drain/reconcile them and their protected copies
before rolling back to software that does not understand that schema. Preserve
all old recipes, saved data and uncertain journals.
