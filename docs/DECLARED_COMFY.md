# Declarative image ComfyWorkFlows (Controller #26)

Controller 1.1.0 adds an optional `declaration` to `comfy.register(name, graph,
declaration=None)` on both existing ingresses. Omitting it retains the exact
checkpoint/reference profiles and their IDs. This is an image profile, not a
revival of schema 2/3, composition, Runtime delegation or another job service.

## Registration contract

Use ComfyUI **API-format** JSON, with numeric node IDs and ordinary typed links.
[Qwen Image Edit example](../examples/qwen-image-edit-declared.json) is a complete
registration request adapted from the previously executed topology. Its managed
image placeholder/output prefix and 416 × 512 defaults replace the smoke test's
local filename/output folder. It contains no weights or personal image bytes.
The accompanying `object_info` test data are synthetic fixtures, not live evidence.

`declaration.version=1` contains:

| Field | Meaning |
| --- | --- |
| `parameters.<key>.type` | `string`, `integer`, `number`, `boolean` |
| `role` | `prompt`, `parameter`, `model`, or `managed_input` |
| `label` | Bounded user-facing label; Studio displays it |
| `targets` | One or more `{node, input}` literal widget destinations |
| `default`, `required` | Explicit default or a required caller value |
| `minimum`, `maximum`, `enum`, `min_length`, `max_length` | Editable value constraints |
| `model_kind` | Required only for `model`; use an existing model catalog kind |
| `constants` | `{node, input}` destinations of every other literal widget |
| `output` | `{node, prefix_input:"filename_prefix"}` for one `SaveImage` output |

Managed image widgets must equal `$<key>` in the graph, be required, and have no
default. Caller values are managed input IDs, never filenames or server paths.
A key can bind several widgets. Destinations must be disjoint and present;
connection slots cannot be overwritten. All literals, including constants, must
be declared. The output widget must initially be `flamoris`; only Controller
sets `flamoris/<job_id>` at submission. Standard `LoadImage.image` requires a
managed binding. Constant widgets reject paths, URLs and placeholders.

Controller obtains each installed class's `/object_info/<class>` from its
operator-configured ComfyUI origin. It checks required/optional widgets, literal
types, current choices/dependencies and numeric limits, connection output types
and slots, cycles, and reachability to the one declared output. Additional output
nodes and disconnected nodes are rejected. Declared choices/ranges are intersected
with installed specifications and published in descriptors. Model bindings also
require files in the existing model catalog at registration/default validation,
build and immediately before submission. No model name selects a use case.

Parameter names use lowercase identifiers up to 64 characters; managed image
names are limited to 32 characters to fit existing Studio ownership records.

Limits: 128 nodes, 64 parameters, four managed images per job, 512 constant
bindings and 256 KiB for the complete immutable record including node specs.
Metadata reads have byte limits and a total configured request timeout. Dynamic
widgets/unsupported connection types fail explicitly; this profile does not
promise every ComfyUI extension can be represented.

The `comfy-<sha256>` digest covers graph, normalized declaration and the relevant
installed node-spec snapshot. Display names do not affect identity. These records
share `comfy-definitions-v1` and the existing 64-definition capacity with old
profiles. Renumber nodes and all declaration/link destinations together; topology
and binding behavior are preserved, while immutable content gets a new digest.

## Execution policy and trust

Installed custom nodes are **executable trusted code**. `object_info` describes
an interface and cannot establish that a node implementation is safe. An operator
must review the nodes they permit. Set `FLAMORIS_DECLARED_COMFY_NODES` to a JSON
array of exact class names; the default `[]` disables the new execution profile.
It is a configuration allowlist, not model-specific source code. For the example:

```sh
FLAMORIS_DECLARED_COMFY_NODES='["UnetLoaderGGUF","CLIPLoader","VAELoader","LoadImage","TextEncodeQwenImageEditPlus","ModelSamplingAuraFlow","EmptySD3LatentImage","KSampler","VAEDecode","SaveImage","FluxKontextMultiReferenceLatentMethod"]'
```

Configuration, installing code/weights, and mounting the existing shared
`FLAMORIS_COMFYUI_INPUT_ROOT` remain operator work. Registration neither installs
nodes nor downloads models, runs shell commands, switches GPUs, or activates a
runtime. This is not an untrusted-code sandbox; an allowed custom node must not
implement undeclared filesystem/network/command effects. Every node and widget
must be intentionally reviewed under that trust boundary. Graphs cannot opt
into their own policy. Execution rechecks the policy and current `object_info`
before staging/posting. Failed preflight rejects without an uncertain reservation;
uncertain POSTs retain existing job/input-copy fences and are never replayed.

## MCP, HTTP and Studio

1. Install trusted nodes/models in ComfyUI and configure the node policy.
2. Send the complete example request to `comfy.register`; inspect `comfy.get`.
3. Upload/snapshot an image through the existing managed input operations.
4. `workflows.build(template=<definition id>, definition_digest=<digest>,
   parameters={"reference_image":<input id>, "instruction":"Give a subtle smile"})`.
5. Submit the returned `workflow_id` once; poll and retrieve existing Job/Assets.

Graph-free descriptors use `metadata_schema_version=3`, `recipeSchema=10` in
Studio's normalized response, and `image.profile=declared-comfy-v1`. They expose
labels, types, defaults, constraints and managed image roles. Studio Image
renders those fields, retains opaque owner-checked image handles, and calls the
same authenticated Controller HTTP authority. Its result preview/download and
**Use settings** actions use the existing routes and original request snapshot.
It does not send Studio generation through MCP/Hub or create a job owner.
Browser numeric inputs remain limited to exact JavaScript-safe integers.

Registration reports `validated`, `basis=installed_object_info`,
`live_provider_verified=false`, `generation_verified=false`. It proves schema
compatibility at registration, not GPU inference or quality. Current node specs
are checked again at execution. `require_ready=true` still rejects because no
live qualification/attestation mechanism is introduced. Existing clients that
understand only metadata version 2 skip this profile; they retain old templates.

## Matched update and evidence

Deploy matching Controller / Generation MCP / Studio **1.1.0** with the updated
Hub catalog (still 25 tools; only an optional argument is added). Dependency pins
in the matched source PRs identify the exact Controller revision. No database
migration or rewrite of existing recipes, IDs, inputs, assets or definitions is
needed. Preserve configured roots and credentials. Drain/reconcile active work
and protected input copies before upgrade/downgrade. Older software cannot read
schema-10 recipes or the new definition records; do not roll it back over active
new work, delete unknown journals, or start a second authority.

Source tests cover the Qwen topology, renumbering, another family/topology,
negative metadata/binding/dependency cases, matched MCP/HTTP contention, managed
images, unknown/no-replay, restart output identity, Studio field rendering and
owned input/result boundaries. Live deployment and the new end-to-end Qwen GPU
run/visual review are **pending**. The prior direct ComfyUI smoke success does
not certify this new path or general identity/quality stability.
