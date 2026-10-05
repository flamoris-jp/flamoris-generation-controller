import hashlib
import io
from copy import deepcopy

import httpx
import pytest
from PIL import Image
from test_controller import controller, http

from flamoris_generation_controller.contracts import CallerContext
from flamoris_generation_controller.models import ModelCatalog
from flamoris_generation_controller.providers.base import SubmissionRejected, SubmissionUnknown
from flamoris_generation_controller.registered_comfy import image_prompt
from flamoris_generation_controller.runtime import GenerationController
from flamoris_generation_controller.workflows import Parameters, WorkflowStore

__all__ = ["controller", "http"]


def graph(reference=True):
    return image_prompt(
        Parameters(checkpoint="base.safetensors", positive_prompt="flowers"), reference=reference
    )


def test_registration_identity_restart_pins_and_reference_contract(settings):
    store = WorkflowStore(ModelCatalog(settings), settings.workflow_dir)
    first = store.definitions.register("Reference image", graph())
    assert first["readiness"]["state"] == "validated"
    assert not first["readiness"]["live_provider_verified"]
    assert store.definitions.register("Other display label", graph())["duplicate"]
    new = WorkflowStore(ModelCatalog(settings), settings.workflow_dir)
    assert (
        new.definitions.descriptor(first["id"])["definition_digest"] == first["definition_digest"]
    )
    built = new.build(
        first["id"],
        {"reference_image": "a" * 32, "positive_prompt": "new"},
        definition_digest=first["definition_digest"],
    )
    assert built["schema_version"] == 7
    assert built["prompt"]["8"]["inputs"]["image"] == "$reference_image"
    new.save(built["workflow_id"])
    assert new.get(built["workflow_id"]).reference_image == "a" * 32
    for params in (
        {},
        {"reference_image": "/tmp/private.png"},
        {"reference_image": "a" * 32, "extra": 1},
    ):
        with pytest.raises(ValueError):
            new.build(first["id"], params)
    with pytest.raises(ValueError):
        new.build(first["id"], {"reference_image": "a" * 32}, definition_digest="b" * 64)
    with pytest.raises(ValueError):
        new.build(first["id"], {"reference_image": "a" * 32}, require_ready=True)


@pytest.mark.parametrize("mutation", ["path", "custom", "unused", "batch", "prefix", "link"])
def test_graph_rejects_unsafe_or_unsupported_structure(settings, mutation):
    candidate = deepcopy(graph())
    if mutation == "path":
        candidate["8"]["inputs"]["image"] = "private.png"
    elif mutation == "custom":
        candidate["8"]["class_type"] = "CustomLoadImage"
    elif mutation == "unused":
        candidate["11"] = {"class_type": "SaveImage", "inputs": {}}
    elif mutation == "batch":
        candidate["9"]["inputs"]["width"] = 8192
    elif mutation == "prefix":
        candidate["7"]["inputs"]["filename_prefix"] = "../../private"
    else:
        candidate["5"]["inputs"]["model"] = ["7", 0]
    store = WorkflowStore(ModelCatalog(settings), settings.workflow_dir)
    with pytest.raises(ValueError):
        store.definitions.register("invalid", candidate)


async def upload(controller):
    stream = io.BytesIO()
    Image.new("RGB", (64, 64)).save(stream, format="PNG")
    raw = stream.getvalue()
    import base64

    upload_id = "a" * 32
    context = CallerContext.internal()
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


async def test_reference_http_provider_submission_identity_terminal_cleanup(
    settings, fake, tmp_path
):
    root = tmp_path / "comfy-input"
    root.mkdir()
    settings = settings.model_copy(update={"comfyui_input_root": root})
    controller = GenerationController(settings, transport=httpx.MockTransport(fake.handle))
    context = CallerContext.external()
    try:
        definition = await controller.invoke(
            "comfy.register", {"name": "reference", "graph": graph()}, context=context
        )
        uploaded = await upload(controller)
        built = await controller.invoke(
            "workflows.build",
            {"template": definition["id"], "parameters": {"reference_image": uploaded["input_id"]}},
            context=context,
        )
        job = await controller.invoke(
            "jobs.submit", {"workflow_id": built["workflow_id"]}, context=context
        )
        assert job["managed_inputs"]["reference_image"]["sha256"] == uploaded["sha256"]
        prompt = fake.prompts[0]["prompt"]
        assert prompt["8"]["inputs"]["image"].startswith("flamoris-inputs/")
        assert prompt["7"]["inputs"]["filename_prefix"] == "flamoris/" + job["job_id"]
        assert len(list((root / "flamoris-inputs").glob("*.png"))) == 1
        fake.finish()
        result = await controller.invoke("jobs.result", {"job_id": job["job_id"]}, context=context)
        assert result["status"] == "completed"
        assert not list((root / "flamoris-inputs").glob("*.png"))
    finally:
        await controller.close()


async def test_ambiguous_reference_post_preserves_copy_and_reservation(settings, fake, tmp_path):
    root = tmp_path / "inputs"
    root.mkdir()
    settings = settings.model_copy(update={"comfyui_input_root": root})

    def handle(request):
        if request.url.path == "/prompt":
            raise httpx.ReadTimeout("lost acknowledgement", request=request)
        return fake.handle(request)

    controller = GenerationController(settings, transport=httpx.MockTransport(handle))
    try:
        definition = controller.workflows.definitions.register("reference", graph())
        uploaded = await upload(controller)
        built = controller.workflows.build(
            definition["id"], {"reference_image": uploaded["input_id"]}
        )
        with pytest.raises(SubmissionUnknown):
            await controller.jobs.submit(built["workflow_id"])
        assert controller.jobs.activity()["busy"]
        assert list((root / "flamoris-inputs").glob("*.png"))
    finally:
        await controller.close()
    restarted = GenerationController(settings, transport=httpx.MockTransport(fake.handle))
    try:
        assert restarted.jobs.activity()["busy"]
        assert list((root / "flamoris-inputs").glob("*.png"))
        assert not fake.prompts
    finally:
        await restarted.close()


async def test_missing_input_storage_rejects_before_post(settings, fake):
    controller = GenerationController(settings, transport=httpx.MockTransport(fake.handle))
    try:
        definition = controller.workflows.definitions.register("reference", graph())
        built = controller.workflows.build(definition["id"], {"reference_image": "a" * 32})
        with pytest.raises(SubmissionRejected):
            await controller.jobs.submit(built["workflow_id"])
        assert not fake.prompts
    finally:
        await controller.close()


async def test_registration_http_transport(http):
    registered = await http.post("comfy.register", json={"name": "reference", "graph": graph()})
    assert registered.status_code == 200
    definition_id = registered.json()["id"]
    retrieved = await http.post("comfy.get", json={"definition_id": definition_id})
    assert retrieved.status_code == 200
    assert retrieved.json()["graph"] == graph()
    assert retrieved.json()["defaults"]["checkpoint"] == "base.safetensors"
