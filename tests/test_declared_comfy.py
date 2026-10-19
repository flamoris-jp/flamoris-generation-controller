"""No weights/GPU: saved Qwen topology and installed-node HTTP fixtures."""

import base64
import hashlib
import io
import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from PIL import Image

from flamoris_generation_controller.config import Settings
from flamoris_generation_controller.contracts import CallerContext
from flamoris_generation_controller.jobs import GenerationBusyError
from flamoris_generation_controller.models import ModelCatalog
from flamoris_generation_controller.providers.base import SubmissionRejected, SubmissionUnknown
from flamoris_generation_controller.runtime import GenerationController
from flamoris_generation_controller.workflows import WorkflowStore

HERE = Path(__file__).parent


def example():
    return json.loads((HERE.parent / "examples/qwen-image-edit-declared.json").read_text())


def specs():
    return json.loads((HERE / "fixtures/qwen-object-info.json").read_text())


def test_v3_combo_constants_and_parameter_survive_saved_recipe(declared):
    settings, body, info = declared
    info["FluxKontextMultiReferenceLatentMethod"]["input"]["required"][
        "reference_latents_method"
    ] = ["COMBO", {"options": ["offset", "index_timestep_zero"], "multiselect": False}]
    original = deepcopy(info)
    body["declaration"]["constants"] = [
        t for t in body["declaration"]["constants"] if t["node"] != "12"
    ]
    body["declaration"]["parameters"]["reference_method"] = {
        "type": "string",
        "role": "parameter",
        "label": "Reference method",
        "default": "index_timestep_zero",
        "targets": [{"node": "12", "input": "reference_latents_method"}],
    }
    store = WorkflowStore(
        ModelCatalog(settings), settings.workflow_dir, declared_comfy_nodes=list(info)
    )
    definition = store.definitions.register(**body, node_specs=info)
    assert info == original
    assert definition["parameters"]["reference_method"]["enum"] == ["offset", "index_timestep_zero"]
    built = store.build(definition["id"], {"reference_image": "a" * 32})
    store.save(built["workflow_id"])
    fresh = WorkflowStore(
        ModelCatalog(settings), settings.workflow_dir, declared_comfy_nodes=list(info)
    )
    assert fresh.prompt(fresh.get(built["workflow_id"])) == built["prompt"]
    with pytest.raises(ValueError):
        store.build(definition["id"], {"reference_image": "a" * 32, "reference_method": "bad"})


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"options": []},
        {"options": "dynamic"},
        {"options": [None]},
        {"options": ["index_timestep_zero"], "multiselect": True},
        {"options": ["index_timestep_zero"], "remote": {"route": "/choices"}},
        {"options": ["index_timestep_zero"], "multi_select": {}},
        {"options": ["other"]},
    ],
)
def test_v3_combo_rejects_unsupported_or_unavailable_choices(declared, options):
    settings, body, info = declared
    info["FluxKontextMultiReferenceLatentMethod"]["input"]["required"][
        "reference_latents_method"
    ] = ["COMBO", options]
    store = WorkflowStore(
        ModelCatalog(settings), settings.workflow_dir, declared_comfy_nodes=list(info)
    )
    with pytest.raises(ValueError):
        store.definitions.register(**body, node_specs=info)


@pytest.fixture
def declared(tmp_path):
    info = specs()
    body = example()
    settings = Settings(
        model_root=tmp_path / "models",
        workflow_dir=tmp_path / "recipes",
        output_dir=tmp_path / "outputs",
        comfyui_input_root=tmp_path / "shared",
        declared_comfy_nodes=list(info),
    )
    settings.comfyui_input_root.mkdir()
    from flamoris_generation_controller.config import MODEL_FOLDERS

    for spec in body["declaration"]["parameters"].values():
        if spec["role"] == "model":
            path = settings.model_root / MODEL_FOLDERS[spec["model_kind"]] / spec["default"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture only")
    return settings, body, info


def test_qwen_roundtrip_build_and_save_with_renumbered_nodes(declared):
    settings, body, info = declared
    store = WorkflowStore(
        ModelCatalog(settings), settings.workflow_dir, declared_comfy_nodes=list(info)
    )
    definition = store.definitions.register(**body, node_specs=info)
    assert definition["readiness"]["live_provider_verified"] is False
    assert definition["metadata_schema_version"] == 3
    assert "graph" not in definition
    got = store.definitions.inspect(definition["id"])
    assert got["graph"] == body["graph"]
    built = store.build(definition["id"], {"reference_image": "a" * 32, "instruction": "Smile"})
    assert built["schema_version"] == 10
    assert built["prompt"]["5"]["inputs"]["prompt"] == "Smile"
    assert built["images"] == {"reference_image": "a" * 32}
    store.save(built["workflow_id"])
    fresh = WorkflowStore(
        ModelCatalog(settings), settings.workflow_dir, declared_comfy_nodes=list(info)
    )
    assert fresh.prompt(fresh.get(built["workflow_id"])) == built["prompt"]
    ids = {key: str(int(key) + 200) for key in body["graph"]}
    graph = {
        ids[k]: {
            "class_type": n["class_type"],
            "inputs": {
                f: [ids[v[0]], v[1]] if type(v) is list else v for f, v in n["inputs"].items()
            },
        }
        for k, n in body["graph"].items()
    }
    decl = deepcopy(body["declaration"])
    for spec in decl["parameters"].values():
        for target in spec["targets"]:
            target["node"] = ids[target["node"]]
    for target in decl["constants"]:
        target["node"] = ids[target["node"]]
    decl["output"]["node"] = ids["11"]
    other = store.definitions.register("Renumbered", graph, decl, node_specs=info)
    built = store.build(other["id"], {"reference_image": "b" * 32, "instruction": "Wave"})
    assert built["prompt"][ids["5"]]["inputs"]["prompt"] == "Wave"
    assert store.definitions.output_node(store.get(built["workflow_id"])) == ids["11"]


def test_another_model_family_and_topology_need_only_declarations(declared):
    settings, _, _ = declared
    info = {
        "AnotherImageGenerator": {
            "input": {
                "required": {"caption": ["STRING"], "quality": ["INT", {"min": 1, "max": 5}]}
            },
            "output": ["IMAGE"],
            "output_node": False,
        },
        "SaveImage": specs()["SaveImage"],
    }
    graph = {
        "48": {
            "class_type": "AnotherImageGenerator",
            "inputs": {"caption": "A tree", "quality": 2},
        },
        "90": {
            "class_type": "SaveImage",
            "inputs": {"images": ["48", 0], "filename_prefix": "flamoris"},
        },
    }
    decl = {
        "parameters": {
            "caption": {
                "type": "string",
                "role": "prompt",
                "label": "Scene",
                "default": "A tree",
                "targets": [{"node": "48", "input": "caption"}],
            },
            "quality": {
                "type": "integer",
                "role": "parameter",
                "label": "Quality",
                "default": 2,
                "minimum": 1,
                "maximum": 5,
                "targets": [{"node": "48", "input": "quality"}],
            },
        },
        "output": {"node": "90"},
    }
    store = WorkflowStore(
        ModelCatalog(settings), settings.workflow_dir, declared_comfy_nodes=list(info)
    )
    definition = store.definitions.register("Other model", graph, decl, node_specs=info)
    built = store.build(definition["id"], {"caption": "A mountain", "quality": 4})
    assert built["prompt"]["48"]["inputs"] == {"caption": "A mountain", "quality": 4}
    assert built["images"] == {}


@pytest.mark.parametrize(
    "case",
    [
        "policy",
        "missing_node",
        "missing_model",
        "type",
        "slot",
        "undeclared",
        "overlap",
        "cycle",
        "unused",
        "side_effect",
        "image_path",
        "unknown_parameter",
    ],
)
def test_invalid_dependencies_bindings_and_effects_reject(declared, case):
    settings, body, info = declared
    allowed = list(info)
    if case == "policy":
        allowed.remove("UnetLoaderGGUF")
    elif case == "missing_node":
        info.pop("UnetLoaderGGUF")
    elif case == "missing_model":
        next(settings.model_root.rglob("*.gguf")).unlink()
    elif case == "type":
        body["graph"]["5"]["inputs"]["clip"] = ["3", 0]
    elif case == "slot":
        body["graph"]["5"]["inputs"]["clip"] = ["2", 99]
    elif case == "undeclared":
        body["declaration"]["constants"].pop()
    elif case == "overlap":
        body["declaration"]["constants"].append({"node": "5", "input": "prompt"})
    elif case == "cycle":
        body["graph"]["12"]["inputs"]["conditioning"] = ["13", 0]
        body["graph"]["13"]["inputs"]["conditioning"] = ["12", 0]
    elif case == "unused":
        body["graph"]["98"] = deepcopy(body["graph"]["3"])
    elif case == "side_effect":
        info["VAELoader"]["output_node"] = True
    elif case == "image_path":
        body["graph"]["4"]["inputs"]["image"] = "/srv/private.png"
    store = WorkflowStore(
        ModelCatalog(settings), settings.workflow_dir, declared_comfy_nodes=allowed
    )
    if case == "unknown_parameter":
        item = store.definitions.register(**body, node_specs=info)
        with pytest.raises(ValueError):
            store.build(item["id"], {"reference_image": "a" * 32, "undeclared": 4})
    else:
        with pytest.raises(ValueError):
            store.definitions.register(**body, node_specs=info)


async def upload(controller):
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(buf, format="PNG")
    data = buf.getvalue()
    key = uuid4().hex
    await controller.uploads.begin(key, "image/png", len(data), hashlib.sha256(data).hexdigest())
    await controller.uploads.write(
        key, 0, base64.b64encode(data).decode(), hashlib.sha256(data).hexdigest()
    )
    return (await controller.uploads.finish(key))["input_id"]


@pytest.mark.parametrize("unknown", [False, True])
async def test_shared_authority_staging_unknown_restart_and_declared_output(declared, unknown):
    settings, body, info = declared
    state = {"posts": [], "completed": False}

    def handle(request):
        path = request.url.path
        if path.startswith("/object_info/"):
            key = path.rsplit("/", 1)[1]
            return httpx.Response(200, json={key: info[key]})
        if path == "/prompt":
            state["posts"].append(json.loads(request.content)["prompt"])
            if unknown:
                raise httpx.ReadTimeout("uncertain", request=request)
            return httpx.Response(200, json={"prompt_id": "declared-1", "node_errors": {}})
        if path == "/queue":
            return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
        if path == "/history/declared-1":
            return httpx.Response(
                200,
                json={
                    "declared-1": {
                        "status": {"completed": True, "status_str": "success", "messages": []},
                        "outputs": {
                            "11": {
                                "images": [
                                    {
                                        "filename": "out.png",
                                        "subfolder": "flamoris",
                                        "type": "output",
                                    }
                                ]
                            }
                        },
                    }
                }
                if state["completed"]
                else {},
            )
        raise AssertionError(path)

    controller = GenerationController(settings, transport=httpx.MockTransport(handle))

    async def invoke(op, args, external=False):
        return await controller.invoke(
            op, args, context=CallerContext.external() if external else CallerContext.internal()
        )

    try:
        registered = await invoke("comfy.register", body, True)
        image_id = await upload(controller)
        built = await invoke(
            "workflows.build",
            {
                "template": registered["id"],
                "parameters": {"reference_image": image_id, "instruction": "Smile"},
            },
        )
        controller.workflows.save(built["workflow_id"])
        if unknown:
            with pytest.raises(SubmissionUnknown):
                await invoke("jobs.submit", {"workflow_id": built["workflow_id"]}, True)
        else:
            await invoke("jobs.submit", {"workflow_id": built["workflow_id"]}, True)
        assert len(state["posts"]) == 1
        prompt = state["posts"][0]
        assert prompt["11"]["inputs"]["filename_prefix"].startswith("flamoris/")
        assert prompt["4"]["inputs"]["image"] != "$reference_image"
        assert list(settings.comfyui_input_root.rglob("*.png"))
        with pytest.raises(GenerationBusyError):
            await invoke("jobs.submit", {"workflow_id": built["workflow_id"]})
        job_id = controller.jobs._active_job_id
    finally:
        await controller.close()
    controller = GenerationController(settings, transport=httpx.MockTransport(handle))
    try:
        assert len(state["posts"]) == 1
        with pytest.raises(GenerationBusyError):
            await controller.jobs.submit(built["workflow_id"])
        if not unknown:
            assert controller.comfyui._output_nodes["declared-1"] == "11"
            state["completed"] = True
            result = await controller.jobs.status(job_id)
            assert result["status"] == "completed"
            assert not list(settings.comfyui_input_root.rglob("*.png"))
    finally:
        await controller.close()


async def test_changed_installed_node_fails_before_post_without_unknown_reservation(declared):
    settings, body, info = declared

    def handle(request):
        if request.url.path.startswith("/object_info/"):
            key = request.url.path.rsplit("/", 1)[1]
            return httpx.Response(200, json={key: info[key]} if key in info else {})
        raise AssertionError("No generation POST is allowed")

    c = GenerationController(settings, transport=httpx.MockTransport(handle))
    try:
        item = await c.invoke("comfy.register", body, context=CallerContext.external())
        built = c.workflows.build(item["id"], {"reference_image": "a" * 32})
        info.pop("UnetLoaderGGUF")
        with pytest.raises(SubmissionRejected):
            await c.jobs.submit(built["workflow_id"])
        assert c.jobs._active_job_id is None
    finally:
        await c.close()


def test_malformed_installed_widget_spec_is_an_explicit_rejection(declared):
    settings, body, info = declared
    info["CLIPLoader"]["input"]["required"]["clip_name"] = [{"unexpected": "widget"}]
    store = WorkflowStore(
        ModelCatalog(settings), settings.workflow_dir, declared_comfy_nodes=list(info)
    )
    with pytest.raises(ValueError, match="input type"):
        store.definitions.register(**body, node_specs=info)


def test_corrupt_declared_graph_record_rejects_without_reinterpretation(declared):
    settings, body, info = declared
    store = WorkflowStore(
        ModelCatalog(settings), settings.workflow_dir, declared_comfy_nodes=list(info)
    )
    definition = store.definitions.register(**body, node_specs=info)
    path = store.definitions.directory / (definition["id"][6:] + ".json")
    record = json.loads(path.read_bytes())
    record["graph"] = []
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="graph record"):
        store.definitions.get(definition["id"])
