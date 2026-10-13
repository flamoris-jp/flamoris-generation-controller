# Reference images for production

This source change adds reviewed IPAdapter appearance/style and ControlNet map profiles to the existing ComfyUI graph registration. It also connects the retained img2img profile to Studio. These are ComfyUI API-format graphs; ComfyUI executes them. Studio and external MCP use one Controller, one JobStore and the same managed inputs.

| Example | Images used |
| --- | --- |
| [reference-image-comfy.json](../examples/reference-image-comfy.json) | Initial image, img2img; schema 7 |
| [character-comfy.json](../examples/character-comfy.json) | Character appearance |
| [style-comfy.json](../examples/style-comfy.json) | Drawing style |
| [control-map-comfy.json](../examples/control-map-comfy.json) | Prepared pose/depth/edge map |
| [style-control-comfy.json](../examples/style-control-comfy.json) | Style and control map |
| [all-references-comfy.json](../examples/all-references-comfy.json) | Initial image, appearance, style and control map |

Conditioning examples use schema 8. Replace every model filename with a compatible installed model **before registration**. Example filenames do not certify model availability. The conditioning models and checkpoint are fixed to a definition's declared filenames; register changed content for a different model combination.

## Model and host setup

For the SDXL examples, prepare a compatible SDXL checkpoint, IPAdapter weights, their matching CLIP Vision encoder and a compatible ControlNet. Character-only and style-only do not require ControlNet. Control-map-only does not require IPAdapter. The required model kinds are checkpoint, ipadapter, clip_vision and controlnet; models.list reports filenames, not architecture compatibility.

Install ComfyUI IPAdapter nodes in the **ComfyUI environment**. FLAMORIS calls IPAdapterModelLoader and IPAdapterAdvanced; it does not embed their implementation, install nodes, download weights or start a GPU runtime. Use versions whose node interfaces match these examples.

Upstream instructions:
- [IPAdapter models](https://github.com/tencent-ailab/IP-Adapter)
- [ComfyUI IPAdapter nodes, models and encoder pairing](https://github.com/cubiq/ComfyUI_IPAdapter_plus)
- [ComfyUI ControlNet](https://docs.comfy.org/tutorials/controlnet/controlnet)

Third-party code and weights retain their own terms. No third-party source or weights are redistributed here.

ComfyUI and Controller must see the same model filenames. Configure FLAMORIS_MODEL_ROOT or explicit FLAMORIS_MODEL_DIRS accordingly. Configure FLAMORIS_COMFYUI_INPUT_ROOT to an explicitly shared ComfyUI input directory, writable by Controller and readable by ComfyUI; mount that directory into the Generation container. Keep Controller state outside this provider input directory. Existing confined-copy permissions and quotas apply.

## Create a ComfyWorkFlow

The helper writes graph JSON only. It makes no network or provider calls. Run it with the matched Controller source/package installed:

~~~bash
python -m flamoris_generation_controller.reference_cli --checkpoint installed-checkpoint.safetensors --initial-image > img2img.json
python -m flamoris_generation_controller.reference_cli --checkpoint installed-sdxl.safetensors --character-adapter installed-ipadapter.safetensors --clip-vision installed-vision.safetensors > character.json
python -m flamoris_generation_controller.reference_cli --checkpoint installed-sdxl.safetensors --character-adapter installed-ipadapter.safetensors --style-adapter installed-ipadapter.safetensors --clip-vision installed-vision.safetensors --controlnet installed-controlnet.safetensors > appearance-style-pose.json
~~~

Add --initial-image to combine img2img with conditioning. Width/height default to 1024; use --width and --height for another bounded size. Initial-image examples default to denoise 0.65. Lower denoise usually preserves more of the initial image.

## Register and generate through MCP

1. Call comfy.register with a display name and the API-format graph. Keep its id and definition_digest.
2. Upload each image through inputs.upload.begin/write/finish, or create a snapshot with inputs.create from an existing generated asset.
3. Call workflows.build with template equal to the definition id and definition_digest equal to the returned digest.
4. Pass each declared image slot its managed input ID, then submit the returned workflow_id once and observe jobs.status/result.

The retained schema-7 img2img slot is reference_image. Conditioning graph slots are initial_image, character_image, style_image and control_image. Each declared slot is required; arbitrary filenames, URLs and raw paths are rejected. Graph registration uses literal $initial_image, $character_image, $style_image and $control_image placeholders. Only Controller replaces them with confined provider copies.

Public influence settings are character_strength, style_strength and control_strength, plus their respective _start/_end intervals (0–1, start < end). Unsupported roles and models are rejected, never silently ignored. Graph registration is static validation; it does not test a GPU, model compatibility or output quality.

## Studio

Use the matched Studio release and apply its normal Alembic migration to 20261012_13. This additive table stores all execution/reference links; it changes no accounts, grants, transcripts or existing assets. Studio's migration belongs to Studio and runs through the existing app installation/update step.

Keep the existing authenticated Controller HTTP connection. Choose a registered ComfyWorkFlow in the Image editor. Select/upload each required initial, character, style or control image and adjust influence. An image service or shared input store that is unavailable leaves generation disabled. No extra Approve action is introduced.

Use settings restores the definition digest, all Studio-owned image handles and influence values. Expired/deleted images stay visible as unavailable and must be replaced. Inputs retain their existing 24-hour lifetime; keep original character/style files for later uploads. Clear inputs removes draft attachments without deleting stored images. Every reference is owner-checked before upstream calls and remains protected while its execution is nonterminal or uncertain.

## Creative meaning and limits

IPAdapter guides appearance/style; it does not guarantee identical characters, clothes or details across shots. This first slice has one appearance reference and one style reference. Region masks, multiple-character identity separation and FaceID are not included.

ControlNet consumes an **already prepared** pose skeleton, depth map or edge map appropriate to the selected ControlNet. A photograph is not automatically converted. Automatic pose/depth extraction needs a separately supported preprocessor profile. A lower control strength gives the prompt more freedom; it does not turn a pose model into a style model.

## Compatibility and verification

Unreviewed nodes, a second sampler/output, unused nodes, hidden widgets and undeclared image paths remain rejected. Legacy schema 2/3, v3, automatic graph qualification and Runtime delegation remain retired.

Schema-8 recipes require this matched Controller/Generation release. Older software cannot execute them. Reconcile active/uncertain work and protected provider copies before any downgrade. The Studio DB downgrade refuses to remove retained execution/reference history.

Ordinary tests use synthetic images and fake ComfyUI HTTP. Actual installed-model/GPU compatibility, memory use and creative quality must be checked on the chosen host after release. Source tests do not establish those results.

## 日本語の導入手順

1. 使用するcheckpointと、それに対応するIPAdapter・CLIP Vision・ControlNetをComfyUI側へ用意します。外見/画風だけならControlNetは不要、構図だけならIPAdapterは不要です。
2. 上の作成CLIへ実際のモデル名を渡し、ComfyWorkFlowのJSONを作ります。モデルの自動取得や生成はしません。
3. MCPの comfy.register で登録し、画像uploadまたは生成済みAssetからのsnapshotを各画像欄へ指定します。
4. Studioでは登録したComfyWorkFlowを選び、「元画像」「キャラクター」「画風」「ポーズ・構図」の必要な画像を設定します。設定再利用は全画像と強度を戻します。

参照画像は既存の24時間期限を維持します。原本を残し、期限切れの参照は再アップロードしてください。ControlNet欄はモデルに対応する作成済みの骨格・深度・輪郭画像です。写真からの自動抽出、複数人物の領域分離、完全なキャラクター同一性はこの実装では保証しません。

