import base64
import hashlib
import io
import json
from copy import deepcopy

import httpx
import pytest
from PIL import Image

from flamoris_generation_controller.contracts import CallerContext
from flamoris_generation_controller.models import ModelCatalog
from flamoris_generation_controller.providers.base import SubmissionRejected, SubmissionUnknown
from flamoris_generation_controller.reference_comfy import IMAGE_NODES, conditioning_prompt
from flamoris_generation_controller.runtime import GenerationController
from flamoris_generation_controller.workflows import Conditioning, Parameters, WorkflowStore


def graph(roles):
    return conditioning_prompt(
        Parameters(checkpoint="base.safetensors", positive_prompt="A new scene"),
        Conditioning(
            character_adapter="adapter.safetensors" if "character_image" in roles else None,
            style_adapter="adapter.safetensors" if "style_image" in roles else None,
            clip_vision="vision.safetensors"
            if set(roles) & {"character_image", "style_image"}
            else None,
            controlnet="control.safetensors" if "control_image" in roles else None,
        ),
        roles,
    )


def models(settings):
    for folder, name in (
        ("ipadapter", "adapter.safetensors"),
        ("clip_vision", "vision.safetensors"),
        ("controlnet", "control.safetensors"),
    ):
        p = settings.model_root / folder / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"metadata fixture only")


ROLES = ("initial_image", "character_image", "style_image", "control_image")


@pytest.mark.parametrize(
    "roles", [("character_image",), ("style_image",), ("control_image",), ROLES]
)
def test_graph_registration_renumbering_roles_models_and_saved_recipe(settings, roles):
    models(settings)
    store = WorkflowStore(ModelCatalog(settings), settings.workflow_dir)
    candidate = graph(roles)
    original = store.definitions.register("References", candidate)
    ids = {key: str(1000 + i) for i, key in enumerate(candidate)}
    renumbered = {
        ids[key]: {
            "class_type": node["class_type"],
            "inputs": {
                name: [ids[value[0]], value[1]] if isinstance(value, list) else value
                for name, value in node["inputs"].items()
            },
        }
        for key, node in candidate.items()
    }
    assert store.definitions.register("renumbered", renumbered)["id"] == original["id"]
    assert original["image"]["reference_roles"] == list(roles)
    assert original["readiness"]["live_provider_verified"] is False
    params = {role: chr(97 + i) * 32 for i, role in enumerate(roles)}
    if "character_image" in roles:
        params["character_strength"] = 0.45
    built = store.build(original["id"], params, definition_digest=original["definition_digest"])
    assert built["schema_version"] == 8
    store.save(built["workflow_id"])
    restarted = WorkflowStore(ModelCatalog(settings), settings.workflow_dir)
    recipe = restarted.get(built["workflow_id"])
    assert recipe.images == {key: value for key, value in params.items() if key in ROLES}
    assert restarted.prompt(recipe) == built["prompt"]
    if "character_image" in roles:
        assert recipe.conditioning.character_strength == 0.45


@pytest.mark.parametrize(
    "mutation",
    [
        "filename",
        "custom",
        "unused",
        "cycle",
        "role",
        "output",
        "disconnect",
        "encoder",
        "interval",
        "widget",
    ],
)
def test_unsupported_conditioning_graphs_rejected(settings, mutation):
    candidate = deepcopy(graph(ROLES))
    if mutation == "filename":
        candidate["20"]["inputs"]["image"] = "private.png"
    elif mutation == "custom":
        candidate["22"]["class_type"] = "UnreviewedIPAdapter"
    elif mutation == "unused":
        candidate["99"] = {"class_type": "LoadImage", "inputs": {"image": "$character_image"}}
    elif mutation == "cycle":
        candidate["22"]["inputs"]["model"] = ["32", 0]
    elif mutation == "role":
        candidate["30"]["inputs"]["image"] = "$character_image"
    elif mutation == "output":
        candidate["7"]["inputs"]["filename_prefix"] = "../../escape"
    elif mutation == "disconnect":
        candidate["5"]["inputs"]["model"] = ["1", 0]
    elif mutation == "encoder":
        candidate["22"]["inputs"]["clip_vision"] = ["1", 1]
    elif mutation == "interval":
        candidate["43"]["inputs"].update(start_percent=0.8, end_percent=0.2)
    else:
        candidate["22"]["inputs"]["combine_embeds"] = "subtract"
    with pytest.raises(ValueError):
        WorkflowStore(ModelCatalog(settings), settings.workflow_dir).definitions.register(
            "bad", candidate
        )


def test_build_requires_every_role_and_declared_models_and_finite_strength(settings):
    models(settings)
    store = WorkflowStore(ModelCatalog(settings), settings.workflow_dir)
    definition = store.definitions.register("References", graph(ROLES))
    params = {role: "a" * 32 for role in ROLES}
    for change in (
        {"character_strength": float("nan")},
        {"control_end": 0},
        {"character_strength": True},
        {"style_strength": "0.6"},
        {"character_adapter": "other.safetensors"},
        {"extra": 1},
        {"character_image": "/private/file.png"},
        {"checkpoint": "other.safetensors"},
    ):
        with pytest.raises(ValueError):
            store.build(definition["id"], {**params, **change})
    with pytest.raises(ValueError):
        store.build(
            definition["id"], {key: value for key, value in params.items() if key != "style_image"}
        )
    (settings.model_root / "clip_vision/vision.safetensors").unlink()
    with pytest.raises(ValueError):
        store.build(definition["id"], params)


async def upload(controller, index):
    stream = io.BytesIO()
    Image.new("RGB", (64, 64), (index * 50, 20, 30)).save(stream, format="PNG")
    raw = stream.getvalue()
    context = CallerContext.internal()
    upload_id = str(index + 1) * 32
    await controller.invoke(
        "inputs.upload.begin",
        {
            "upload_id": upload_id,
            "mime_type": "image/png",
            "size_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        context=context,
    )
    await controller.invoke(
        "inputs.upload.write",
        {
            "upload_id": upload_id,
            "offset": 0,
            "data_base64": base64.b64encode(raw).decode(),
            "chunk_sha256": hashlib.sha256(raw).hexdigest(),
        },
        context=context,
    )
    return await controller.invoke(
        "inputs.upload.finish", {"upload_id": upload_id}, context=context
    )


@pytest.mark.parametrize("outcome", ["success", "partial-stage", "unknown", "rejected"])
async def test_four_images_use_normal_admission_copy_ledger_and_recovery(
    settings, fake, tmp_path, outcome
):
    models(settings)
    root = tmp_path / "comfy-input"
    root.mkdir()
    settings = settings.model_copy(update={"comfyui_input_root": root})

    def handle(request):
        if request.url.path == "/prompt" and outcome == "unknown":
            raise httpx.ReadTimeout("acknowledgement lost", request=request)
        if request.url.path == "/prompt" and outcome == "rejected":
            return httpx.Response(
                400,
                json={
                    "error": {
                        "type": "prompt_outputs_failed_validation",
                        "message": "Invalid workflow",
                    },
                    "node_errors": {},
                },
            )
        return fake.handle(request)

    controller = GenerationController(settings, transport=httpx.MockTransport(handle))
    try:
        definition = controller.workflows.definitions.register("References", graph(ROLES))
        inputs = {role: await upload(controller, i) for i, role in enumerate(ROLES)}
        built = controller.workflows.build(
            definition["id"], {role: value["input_id"] for role, value in inputs.items()}
        )
        if outcome == "partial-stage":
            stage = controller.comfyui.input_copies.stage
            calls = []

            def broken(*args):
                calls.append(args)
                if len(calls) == 3:
                    raise ValueError("fixture stage failure")
                return stage(*args)

            controller.comfyui.input_copies.stage = broken
        if outcome in {"partial-stage", "rejected"}:
            with pytest.raises(SubmissionRejected):
                await controller.jobs.submit(built["workflow_id"])
            assert not controller.jobs.activity()["busy"]
            assert not list((root / "flamoris-inputs").glob("*.png"))
            return
        if outcome == "unknown":
            with pytest.raises(SubmissionUnknown):
                await controller.jobs.submit(built["workflow_id"])
            assert controller.jobs.activity()["busy"]
            assert len(list((root / "flamoris-inputs").glob("*.png"))) == 4
        else:
            job = await controller.jobs.submit(built["workflow_id"])
            prompt = fake.prompts[0]["prompt"]
            paths = []
            for role, node in IMAGE_NODES.items():
                path = root / prompt[node]["inputs"]["image"]
                assert hashlib.sha256(path.read_bytes()).hexdigest() == inputs[role]["sha256"]
                paths.append(path)
            assert len(set(paths)) == 4
            assert set(job["managed_inputs"]) == set(ROLES)
            fake.finish()
            assert (await controller.jobs.result(job["job_id"]))["status"] == "completed"
            assert not list((root / "flamoris-inputs").glob("*.png"))
    finally:
        await controller.close()
    if outcome == "unknown":
        restarted = GenerationController(settings, transport=httpx.MockTransport(fake.handle))
        try:
            assert restarted.jobs.activity()["busy"]
            assert len(list((root / "flamoris-inputs").glob("*.png"))) == 4
            assert not fake.prompts
        finally:
            await restarted.close()


def test_checked_in_comfyworkflows_are_api_format_profiles(settings):
    from pathlib import Path

    from flamoris_generation_controller.registered_comfy import validate_graph

    for path in (Path(__file__).parents[1] / "examples").glob("*-comfy.json"):
        validate_graph(json.loads(path.read_text()))


@pytest.mark.parametrize(
    "flags",
    [
        ["--initial-image"],
        ["--controlnet", "control.safetensors"],
        ["--character-adapter", "adapter.safetensors", "--clip-vision", "vision.safetensors"],
    ],
)
def test_installed_cli_writes_supported_graph_without_provider_calls(flags):
    import subprocess
    import sys

    from flamoris_generation_controller.registered_comfy import validate_graph

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "flamoris_generation_controller.reference_cli",
            "--checkpoint",
            "base.safetensors",
            *flags,
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    graph = json.loads(result.stdout)
    validate_graph(graph)
    if "--initial-image" in flags:
        assert graph["5"]["inputs"]["denoise"] == 0.65

