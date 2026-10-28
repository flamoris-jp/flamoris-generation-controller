"""Synthetic contract fixtures only: no production AnimeGen graph or GPU claim."""

import base64
import hashlib
import io
import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import av
import httpx
import pytest
from PIL import Image

from flamoris_generation_controller.config import Settings
from flamoris_generation_controller.contracts import CallerContext
from flamoris_generation_controller.jobs import GenerationBusyError
from flamoris_generation_controller.models import ModelCatalog
from flamoris_generation_controller.providers.base import ProviderError, SubmissionUnknown
from flamoris_generation_controller.runtime import GenerationController
from flamoris_generation_controller.video import verify_mp4
from flamoris_generation_controller.workflows import WorkflowStore


def fixture(mode="i2v"):
    condition = "WanImageToVideo" if mode == "i2v" else "WanFirstLastFrameToVideo"
    graph = {
        "1": {"class_type": "TestContext", "inputs": {}},
        "2": {"class_type": "LoadImage", "inputs": {"image": "$start_image"}},
        "3": {
            "class_type": condition,
            "inputs": {
                "positive": ["1", 0],
                "negative": ["1", 1],
                "vae": ["1", 2],
                "start_image": ["2", 0],
                "width": 64,
                "height": 64,
                "length": 5,
                "batch_size": 1,
            },
        },
        "4": {
            "class_type": "TestDecode",
            "inputs": {
                "positive": ["3", 0],
                "negative": ["3", 1],
                "latent": ["3", 2],
            },
        },
        "5": {"class_type": "CreateVideo", "inputs": {"images": ["4", 0], "fps": 16}},
        "6": {
            "class_type": "SaveVideo",
            "inputs": {
                "video": ["5", 0],
                "filename_prefix": "flamoris",
                "format": "mp4",
                "codec": "h264",
            },
        },
    }
    info = {
        "TestContext": {"input": {}, "output": ["CONDITIONING", "CONDITIONING", "VAE"]},
        "LoadImage": {"input": {"required": {"image": [["placeholder.png"]]}}, "output": ["IMAGE"]},
        condition: {
            "input": {
                "required": {
                    "positive": ["CONDITIONING"],
                    "negative": ["CONDITIONING"],
                    "vae": ["VAE"],
                    "start_image": ["IMAGE"],
                    "width": ["INT", {"min": 16, "max": 4096}],
                    "height": ["INT", {"min": 16, "max": 4096}],
                    "length": ["INT", {"min": 1, "max": 4096}],
                    "batch_size": ["INT", {"min": 1, "max": 4096}],
                }
            },
            "output": ["CONDITIONING", "CONDITIONING", "LATENT"],
        },
        "TestDecode": {
            "input": {
                "required": {
                    "positive": ["CONDITIONING"],
                    "negative": ["CONDITIONING"],
                    "latent": ["LATENT"],
                }
            },
            "output": ["IMAGE"],
        },
        "CreateVideo": {
            "input": {"required": {"images": ["IMAGE"], "fps": ["FLOAT", {"min": 1, "max": 120}]}},
            "output": ["VIDEO"],
        },
        "SaveVideo": {
            "input": {
                "required": {
                    "video": ["VIDEO"],
                    "filename_prefix": ["STRING"],
                    "format": [["mp4", "webm"]],
                    "codec": [["h264", "av1"]],
                }
            },
            "output": [],
            "output_node": True,
        },
    }
    parameters = {
        "start_image": {
            "type": "string",
            "role": "managed_input",
            "required": True,
            "label": "Start",
            "targets": [{"node": "2", "input": "image"}],
        }
    }
    for key, node, widget, default, low, high in (
        ("width", "3", "width", 64, 64, 1024),
        ("height", "3", "height", 64, 64, 1024),
        ("frames", "3", "length", 5, 5, 161),
        ("fps", "5", "fps", 16, 1, 32),
    ):
        parameters[key] = {
            "type": "integer",
            "role": "parameter",
            "label": key,
            "default": default,
            "minimum": low,
            "maximum": high,
            "targets": [{"node": node, "input": widget}],
        }
    if mode == "flf2v":
        graph["7"] = {"class_type": "LoadImage", "inputs": {"image": "$end_image"}}
        graph["3"]["inputs"]["end_image"] = ["7", 0]
        info[condition]["input"]["required"]["end_image"] = ["IMAGE"]
        parameters["end_image"] = {
            "type": "string",
            "role": "managed_input",
            "required": True,
            "label": "End",
            "targets": [{"node": "7", "input": "image"}],
        }
    body = {
        "name": "Synthetic Wan contract",
        "graph": graph,
        "declaration": {
            "version": 2,
            "video": {"mode": mode, "conditioning_node": "3"},
            "parameters": parameters,
            "constants": [
                {"node": "3", "input": "batch_size"},
                {"node": "6", "input": "format"},
                {"node": "6", "input": "codec"},
            ],
            "output": {"node": "6"},
        },
    }
    return body, info


def settings_for(tmp_path, info):
    shared = tmp_path / "shared"
    shared.mkdir(exist_ok=True)
    return Settings(
        model_root=tmp_path / "models",
        workflow_dir=tmp_path / "recipes",
        output_dir=tmp_path / "outputs",
        comfyui_input_root=shared,
        declared_comfy_nodes=list(info),
    )


def native_video_fixture():
    body, info = fixture()
    native = json.loads((Path(__file__).parent / "fixtures/native-video-v3.json").read_text())
    info.update(native["node_specs"])
    output = body["graph"]["6"]["inputs"]
    output["format.codec"] = output.pop("codec")
    body["declaration"]["constants"][-1]["input"] = "format.codec"
    return body, info


def test_installed_native_dynamic_video_schema_roundtrip(tmp_path):
    body, info = native_video_fixture()
    original = deepcopy(info)
    settings = settings_for(tmp_path, info)
    store = WorkflowStore(
        ModelCatalog(settings), settings.workflow_dir, declared_comfy_nodes=list(info)
    )
    item = store.definitions.register(**body, node_specs=info)
    built = store.build(item["id"], {"start_image": "a" * 32})
    output = built["prompt"]["6"]["inputs"]
    assert output["format"] == "mp4" and output["format.codec"] == "h264"
    assert "codec" not in output
    assert info == original  # Never alter the installed schema receipt.
    store.save(built["workflow_id"])
    fresh = WorkflowStore(
        ModelCatalog(settings), settings.workflow_dir, declared_comfy_nodes=list(info)
    )
    assert fresh.prompt(fresh.get(built["workflow_id"])) == built["prompt"]


@pytest.mark.parametrize(
    "bad",
    [
        "legacy_codec",
        "missing_codec",
        "wrong_codec",
        "nested_format",
        "encoding",
        "parameter_format",
        "parameter_codec",
        "missing_mp4",
        "missing_h264",
        "duplicate_choice",
        "required_format_input",
        "required_codec_input",
        "malformed_inputs",
        "image_declaration",
    ],
)
def test_dynamic_video_support_stays_fixed_and_checks_installed_branch(tmp_path, bad):
    body, info = native_video_fixture()
    output = body["graph"]["6"]["inputs"]
    declaration = body["declaration"]
    fmt = info["SaveVideo"]["input"]["required"]["format"][1]["options"]
    mp4_branch = next(choice for choice in fmt if choice["key"] == "mp4")
    codecs = mp4_branch["inputs"]["required"]["codec"][1]["options"]
    h264_branch = next(choice for choice in codecs if choice["key"] == "h264")
    if bad == "legacy_codec":
        output["codec"] = output.pop("format.codec")
    elif bad == "missing_codec":
        del output["format.codec"]
    elif bad == "wrong_codec":
        output["format.codec"] = "av1"
    elif bad == "nested_format":
        output["format"] = {"format": "mp4", "codec": {"codec": "h264"}}
    elif bad == "encoding":
        output["format.codec.encoding"] = "auto"
        declaration["constants"].append({"node": "6", "input": "format.codec.encoding"})
    elif bad in {"parameter_format", "parameter_codec"}:
        key = "format" if bad == "parameter_format" else "format.codec"
        target = {"node": "6", "input": key}
        declaration["constants"].remove(target)
        declaration["parameters"]["container_choice"] = {
            "type": "string",
            "role": "parameter",
            "label": "Choice",
            "targets": [target],
            "default": output[key],
        }
    elif bad == "missing_mp4":
        fmt.remove(mp4_branch)
    elif bad == "missing_h264":
        codecs.remove(h264_branch)
    elif bad == "duplicate_choice":
        fmt.append(deepcopy(mp4_branch))
    elif bad == "required_format_input":
        mp4_branch["inputs"]["required"]["new_input"] = ["STRING"]
    elif bad == "required_codec_input":
        h264_branch["inputs"]["required"]["new_input"] = ["STRING"]
    elif bad == "malformed_inputs":
        mp4_branch["inputs"] = {"required": []}
    elif bad == "image_declaration":
        declaration["version"] = 1
        del declaration["video"]
    settings = settings_for(tmp_path, info)
    store = WorkflowStore(
        ModelCatalog(settings), settings.workflow_dir, declared_comfy_nodes=list(info)
    )
    with pytest.raises(ValueError):
        store.definitions.register(**body, node_specs=info)


def mp4(*, frames=5, fps=16):
    buffer = io.BytesIO()
    with av.open(buffer, mode="w", format="mp4") as container:
        stream = container.add_stream("libx264", rate=fps)
        stream.width = stream.height = 64
        stream.pix_fmt = "yuv420p"
        for _ in range(frames):
            frame = av.VideoFrame.from_image(Image.new("RGB", (64, 64), "red"))
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return buffer.getvalue()


@pytest.mark.parametrize("mode", ["i2v", "flf2v"])
def test_video_registration_build_roundtrip_and_image_schema_guard(tmp_path, mode):
    body, info = fixture(mode)
    settings = settings_for(tmp_path, info)
    store = WorkflowStore(
        ModelCatalog(settings), settings.workflow_dir, declared_comfy_nodes=list(info)
    )
    item = store.definitions.register(**body, node_specs=info)
    assert item["capability_id"] == "video.generate"
    assert "image" not in item and item["video"]["mode"] == mode
    images = {"start_image": "a" * 32, **({"end_image": "b" * 32} if mode == "flf2v" else {})}
    built = store.build(item["id"], images)
    assert built["schema_version"] == 11 and built["template"] == "registered-video"
    store.save(built["workflow_id"])
    fresh = WorkflowStore(
        ModelCatalog(settings), settings.workflow_dir, declared_comfy_nodes=list(info)
    )
    assert fresh.prompt(fresh.get(built["workflow_id"])) == built["prompt"]
    recipe = fresh.get(built["workflow_id"])
    corrupt = recipe.model_dump()
    corrupt.update(schema_version=10, template="registered-comfy")
    with pytest.raises(ValueError, match="schema mismatch"):
        fresh.prompt(fresh.parse(corrupt))
    for changes in (
        {"end_image": "c" * 32} if mode == "i2v" else {"end_image": None},
        {"frames": 80},
        {"fps": 0},
        {"width": 1024, "height": 1024},
        {"start_image": "/srv/private.webp"},
    ):
        with pytest.raises(ValueError):
            store.build(item["id"], {**images, **changes})


@pytest.mark.parametrize(
    "bad", ["mode", "ignored_end", "alias_end", "output", "fps", "batch", "policy"]
)
def test_video_contract_rejects_ignored_roles_unbounded_or_wrong_outputs(tmp_path, bad):
    body, info = fixture("flf2v")
    allowed = list(info)
    if bad == "mode":
        body["declaration"]["video"]["mode"] = "i2v"
    elif bad == "ignored_end":
        body["graph"]["3"]["inputs"]["end_image"] = ["2", 0]
    elif bad == "alias_end":
        body["declaration"]["parameters"]["end_image"]["targets"][0]["node"] = "2"
    elif bad == "output":
        body["graph"]["6"]["inputs"]["format"] = "webm"
    elif bad == "fps":
        body["declaration"]["parameters"]["fps"]["maximum"] = 120
    elif bad == "batch":
        body["graph"]["3"]["inputs"]["batch_size"] = 2
    elif bad == "policy":
        allowed.remove("SaveVideo")
    settings = settings_for(tmp_path, info)
    store = WorkflowStore(
        ModelCatalog(settings), settings.workflow_dir, declared_comfy_nodes=allowed
    )
    with pytest.raises(ValueError):
        store.definitions.register(**body, node_specs=info)


@pytest.mark.parametrize("kind", ["valid", "bad_bytes", "wrong_count", "wrong_fps"])
def test_real_mp4_decode_and_effective_recipe_verification(kind):
    data = (
        b"not video"
        if kind == "bad_bytes"
        else mp4(frames=9 if kind == "wrong_count" else 5, fps=8 if kind == "wrong_fps" else 16)
    )
    expected = {"width": 64, "height": 64, "frames": 5, "fps": 16}
    if kind == "valid":
        verify_mp4(data, expected)
    else:
        with pytest.raises(ValueError):
            verify_mp4(data, expected)


async def upload(controller, color):
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buffer, format="PNG")
    data = buffer.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    request_id = uuid4().hex
    await controller.uploads.begin(request_id, "image/png", len(data), digest)
    await controller.uploads.write(request_id, 0, base64.b64encode(data).decode(), digest)
    return (await controller.uploads.finish(request_id))["input_id"]


@pytest.mark.parametrize("mode", ["i2v", "flf2v"])
@pytest.mark.parametrize(
    "outcome", ["completed", "unknown", "bad_output", "bad_bytes", "cancelled"]
)
async def test_shared_jobs_transfer_restart_and_no_replay(tmp_path, mode, outcome):
    body, info = fixture(mode)
    settings = settings_for(tmp_path, info)
    state = {"posts": [], "done": False}
    data = mp4() if outcome != "bad_bytes" else b"invalid mp4"

    def handle(request):
        path = request.url.path
        if path.startswith("/object_info/"):
            cls = path.rsplit("/", 1)[1]
            return httpx.Response(200, json={cls: info[cls]})
        if path == "/prompt":
            state["posts"].append(json.loads(request.content)["prompt"])
            if outcome == "unknown":
                raise httpx.ReadTimeout("uncertain", request=request)
            return httpx.Response(200, json={"prompt_id": "video-1", "node_errors": {}})
        if path == "/queue":
            return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
        if path == "/history/video-1":
            prefix = state["posts"][0]["6"]["inputs"]["filename_prefix"].split("/")[-1]
            return httpx.Response(
                200,
                json={
                    "video-1": {
                        "status": {
                            "completed": True,
                            "status_str": "success",
                            "messages": [["execution_interrupted", {}]]
                            if outcome == "cancelled"
                            else [],
                        },
                        "outputs": {
                            "6": {
                                "images": [
                                    {
                                        "filename": prefix + "_00001_.mp4",
                                        "subfolder": "elsewhere"
                                        if outcome == "bad_output"
                                        else "flamoris",
                                        "type": "output",
                                    }
                                ]
                            }
                        },
                    }
                }
                if state["done"]
                else {},
            )
        if path == "/view":
            return httpx.Response(200, content=data)
        raise AssertionError(path)

    transport = httpx.MockTransport(handle)
    controller = GenerationController(settings, transport=transport)
    context = CallerContext.external()
    try:
        item = await controller.invoke("comfy.register", body, context=context)
        images = {"start_image": await upload(controller, "red")}
        if mode == "flf2v":
            images["end_image"] = await upload(controller, "blue")
        built = controller.workflows.build(item["id"], images)
        controller.workflows.save(built["workflow_id"])
        if outcome == "unknown":
            with pytest.raises(SubmissionUnknown):
                await controller.jobs.submit(built["workflow_id"])
        else:
            await controller.invoke(
                "jobs.submit", {"workflow_id": built["workflow_id"]}, context=context
            )
        job_id = controller.jobs._active_job_id
        prompt = state["posts"][0]
        assert prompt["2"]["inputs"]["image"] != "$start_image"
        if mode == "flf2v":
            assert prompt["7"]["inputs"]["image"] != prompt["2"]["inputs"]["image"]
        with pytest.raises(GenerationBusyError):
            await controller.jobs.submit(built["workflow_id"])
    finally:
        await controller.close()
    controller = GenerationController(settings, transport=transport)
    try:
        assert len(state["posts"]) == 1
        state["done"] = True
        if outcome == "unknown":
            assert (await controller.jobs.status(job_id))["status"] == "unknown"
            with pytest.raises(GenerationBusyError):
                await controller.jobs.submit(built["workflow_id"])
            assert list(settings.comfyui_input_root.rglob("*.png"))
            return
        if outcome == "cancelled":
            assert (await controller.jobs.cancel(job_id))["status"] == "cancelled"
            assert not list(settings.comfyui_input_root.rglob("*.png"))
            return
        if outcome == "bad_output":
            with pytest.raises(ProviderError):
                await controller.jobs.status(job_id)
            return
        assets = await controller.jobs.list_assets(job_id)
        asset = assets["assets"][0]
        assert asset["role"] == "video_clip" and asset["mime_type"] == "video/mp4"
        if outcome == "bad_bytes":
            with pytest.raises(ProviderError):
                await controller.transfers.prepare(asset["asset_id"])
            assert not list(settings.output_dir.rglob("output-*.mp4"))
            return
        receipt = await controller.transfers.prepare(asset["asset_id"])
        read = await controller.transfers.read(asset["asset_id"], receipt["sha256"], 0)
        assert base64.b64decode(read["data_base64"]) == data
        assert not list(settings.comfyui_input_root.rglob("*.png"))
    finally:
        await controller.close()
    controller = GenerationController(settings, transport=transport)
    try:
        assert (await controller.jobs.list_assets(job_id))["assets"][0]["materialized"]
        assert (await controller.transfers.prepare(asset["asset_id"]))["sha256"] == receipt[
            "sha256"
        ]
        assert len(state["posts"]) == 1
    finally:
        await controller.close()
